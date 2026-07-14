# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""End-to-end test: ReActAgent with real LLM + real tools, tracer auto-reports OTel spans.

Validates that when OtelAgentHandler is globally registered, a real agent invocation
automatically produces OTel spans for tool execution (plugin spans).

Note: LLM spans are produced via tracer.decorate_model_with_trace(), which only
wraps models obtained through Runner.resource_mgr.get_model(). Since ReActAgent
creates the Model directly via _get_llm(), LLM spans are NOT auto-generated in
the current architecture. A separate test (test_agent_llm_span_via_tracer_trigger)
manually verifies that OtelAgentHandler correctly translates LLM events when
triggered explicitly.

Uses a simple LocalFunction tool (weather query) and real SiliconFlow LLM.

LLM test requires real API credentials — fill in API_BASE / API_KEY /
MODEL_NAME / MODEL_PROVIDER below or set environment variables before running.
"""

import os

import pytest

from opentelemetry import trace

from tests.conftest_otel import _EXPORTER, _OTEL_TRACER, jaeger_is_available
from openjiuwen.core.session.tracer.tracer import Tracer, TracerHandlerRegistry
from openjiuwen.core.session.tracer.handler import TracerHandlerName
from openjiuwen.core.session.agent import Session, create_agent_session
from openjiuwen.core.single_agent.agents.react_agent import ReActAgent, ReActAgentConfig
from openjiuwen.core.single_agent.schema.agent_card import AgentCard
from openjiuwen.core.foundation.tool import ToolCard, LocalFunction
from openjiuwen.core.foundation.llm.schema.config import ModelClientConfig, ModelRequestConfig
from openjiuwen.extensions.tracer_otel.config import OtelTracerConfig
from openjiuwen.extensions.tracer_otel.handler import OtelAgentHandler, OtelWorkflowHandler
from openjiuwen.extensions.tracer_otel.otel_rail import OtelRail
from openjiuwen.extensions.tracer_otel.semconv import (
    GEN_AI_SYSTEM,
    GEN_AI_SYSTEM_VALUE,
    GEN_AI_OPERATION_NAME,
    GEN_AI_REQUEST_MODEL,
    OJ_AGENT_INVOKE_TYPE,
    OJ_AGENT_NAME,
    OJ_AGENT_INPUTS,
    OJ_AGENT_OUTPUTS,
)

pytestmark = pytest.mark.asyncio

# ---------------------------------------------------------------------------
# LLM credentials — fill in before running
# ---------------------------------------------------------------------------
API_BASE = os.getenv("OTEL_TEST_API_BASE", "")
API_KEY = os.getenv("OTEL_TEST_API_KEY", "")
MODEL_NAME = os.getenv("OTEL_TEST_MODEL_NAME", "")
MODEL_PROVIDER = os.getenv("OTEL_TEST_MODEL_PROVIDER", "")

_LLM_CREDENTIALS_AVAILABLE = bool(API_BASE and API_KEY and MODEL_NAME and MODEL_PROVIDER)

# ---------------------------------------------------------------------------
# Shared fixtures and helpers
# ---------------------------------------------------------------------------


def _get_weather(city: str) -> str:
    """Simple weather tool: returns mock weather data for a city."""
    weather_map = {
        "beijing": "Beijing: 22°C, sunny, humidity 45%",
        "shanghai": "Shanghai: 26°C, cloudy, humidity 78%",
        "guangzhou": "Guangzhou: 30°C, rainy, humidity 85%",
        "shenzhen": "Shenzhen: 28°C, partly cloudy, humidity 70%",
    }
    return weather_map.get(city.lower(), f"{city}: weather data unavailable")


@pytest.fixture(autouse=True)
def _clean_registry_and_exporter():
    TracerHandlerRegistry.clear()
    _EXPORTER.clear()
    yield
    TracerHandlerRegistry.clear()
    _EXPORTER.clear()


def _register_otel_handlers():
    """Register OtelAgentHandler + OtelWorkflowHandler to global registry."""
    config = OtelTracerConfig(redaction_enabled=False)
    TracerHandlerRegistry.register_handler("otel_agent", OtelAgentHandler(_OTEL_TRACER, config))
    TracerHandlerRegistry.register_handler("otel_workflow", OtelWorkflowHandler(_OTEL_TRACER, config))


def _create_weather_tool() -> LocalFunction:
    """Create a weather query LocalFunction tool."""
    card = ToolCard(
        name="get_weather",
        description="Get weather information for a given city. Input should be a city name like 'beijing', 'shanghai'.",
        input_params={
            "type": "object",
            "properties": {
                "city": {
                    "description": "City name to query weather for",
                    "type": "string",
                },
            },
            "required": ["city"],
        },
    )
    return LocalFunction(card=card, func=_get_weather)


def _create_model_client_config() -> ModelClientConfig:
    return ModelClientConfig(
        client_provider=MODEL_PROVIDER,
        api_key=API_KEY,
        api_base=API_BASE,
        timeout=60,
        verify_ssl=False,
    )


def _create_model_request_config() -> ModelRequestConfig:
    return ModelRequestConfig(
        model=MODEL_NAME,
        temperature=0.7,
        top_p=0.9,
    )


# ---------------------------------------------------------------------------
# E2E: Agent with real LLM + real tool (tool span auto-generated)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not _LLM_CREDENTIALS_AVAILABLE,
    reason="LLM credentials not set — fill in OTEL_TEST_API_BASE / OTEL_TEST_API_KEY / "
           "OTEL_TEST_MODEL_NAME / OTEL_TEST_MODEL_PROVIDER environment variables",
)
class TestE2EAgentWithLLMAndTool:
    """ReActAgent calls real LLM + weather tool, producing OTel tool (plugin) spans.

    Tool spans are auto-generated via decorate_tool_with_trace() when
    Runner.resource_mgr.get_tool() is called with an agent session that has
    a tracer. LLM spans are NOT auto-generated in this path because
    ReActAgent._get_llm() creates the Model directly without tracer decoration.

    Validates:
    - Tool span (SpanKind.INTERNAL, tool.* name, gen_ai.* attributes)
    - All spans carry gen_ai.system = openjiuwen
    """

    async def asyncSetUp(self):
        from openjiuwen.core.runner import Runner
        await Runner.start()

    async def asyncTearDown(self):
        from openjiuwen.core.runner import Runner
        await Runner.stop()

    async def test_agent_invoke_with_tool(self):
        _register_otel_handlers()

        card = AgentCard(
            name="otel_test_agent",
            description="Agent for OTel e2e test with weather tool",
        )

        config = ReActAgentConfig(
            model_name=MODEL_NAME,
            model_provider=MODEL_PROVIDER,
            api_key=API_KEY,
            api_base=API_BASE,
            prompt_template=[
                {"role": "system", "content": "You are a helpful assistant. Use the get_weather tool when asked about weather."},
            ],
            max_iterations=3,
            model_client_config=_create_model_client_config(),
            model_config_obj=_create_model_request_config(),
        )

        agent = ReActAgent(card=card)
        agent.configure(config)

        # Register weather tool via add_ability (also registers in Runner.resource_mgr)
        weather_tool = _create_weather_tool()
        agent.ability_manager.add_ability(weather_tool.card, weather_tool)

        session = create_agent_session(session_id="otel_agent_test", card=card)
        await session.pre_run()

        result = await agent.invoke(
            {"query": "What's the weather in Beijing?", "conversation_id": "otel_agent_test"},
            session=session,
        )

        assert result is not None
        assert "result_type" in result

        # Verify OTel spans were produced
        finished = _EXPORTER.get_finished_spans()
        # Tool span is auto-generated via decorate_tool_with_trace
        assert len(finished) >= 1

        # Verify tool span exists (name starts with "tool.")
        tool_spans = [s for s in finished if s.name.startswith("tool.")]
        assert len(tool_spans) >= 1
        for ts in tool_spans:
            assert ts.attributes.get(GEN_AI_SYSTEM) == GEN_AI_SYSTEM_VALUE
            assert ts.attributes.get(GEN_AI_OPERATION_NAME) == "execute_tool"
            assert ts.attributes.get(OJ_AGENT_INVOKE_TYPE) is not None
            assert ts.attributes.get(OJ_AGENT_NAME) is not None

    async def test_agent_invoke_pure_conversation(self):
        """Agent calls LLM without tool usage — no tool spans produced."""
        _register_otel_handlers()

        card = AgentCard(
            name="otel_conversation_agent",
            description="Agent for OTel e2e test - pure conversation",
        )

        config = ReActAgentConfig(
            model_name=MODEL_NAME,
            model_provider=MODEL_PROVIDER,
            api_key=API_KEY,
            api_base=API_BASE,
            prompt_template=[
                {"role": "system", "content": "You are a helpful assistant. Answer questions directly without using tools."},
            ],
            max_iterations=2,
            model_client_config=_create_model_client_config(),
            model_config_obj=_create_model_request_config(),
        )

        agent = ReActAgent(card=card)
        agent.configure(config)

        session = create_agent_session(session_id="otel_agent_conv_test", card=card)
        await session.pre_run()

        result = await agent.invoke(
            {"query": "What is 1+1? Just answer the number.", "conversation_id": "otel_agent_conv_test"},
            session=session,
        )

        assert result is not None
        assert result.get("result_type") == "answer"

        # No tool spans should be produced (pure conversation)
        finished = _EXPORTER.get_finished_spans()
        tool_spans = [s for s in finished if s.name.startswith("tool.")]
        assert len(tool_spans) == 0


# ---------------------------------------------------------------------------
# E2E: Agent with OtelRail — root span + LLM child span via rail callbacks
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not _LLM_CREDENTIALS_AVAILABLE,
    reason="LLM credentials not set",
)
class TestE2EAgentWithOtelRail:
    """ReActAgent with OtelRail registered — validates full span lifecycle.

    The OtelRail hooks into BEFORE_INVOKE/AFTER_INVOKE to create the agent
    root span (chain.*), and into BEFORE_MODEL_CALL/AFTER_MODEL_CALL to
    create LLM child spans (llm.*). This test verifies both spans are
    produced with the correct parent-child relationship when the rail is
    opt-in registered on the agent.

    This is the integration path that supplements the auto-generated tool
    spans (decorate_tool_with_trace) with agent-root and LLM spans that
    ReActAgent does not produce on its own.
    """

    async def asyncSetUp(self):
        from openjiuwen.core.runner import Runner
        await Runner.start()

    async def asyncTearDown(self):
        from openjiuwen.core.runner import Runner
        await Runner.stop()

    async def test_otel_rail_produces_root_and_llm_spans(self):
        _register_otel_handlers()

        card = AgentCard(
            name="otel_rail_agent",
            description="Agent for OtelRail e2e test",
        )

        config = ReActAgentConfig(
            model_name=MODEL_NAME,
            model_provider=MODEL_PROVIDER,
            api_key=API_KEY,
            api_base=API_BASE,
            prompt_template=[
                {"role": "system", "content": "You are a helpful assistant. Answer questions directly without using tools."},
            ],
            max_iterations=2,
            model_client_config=_create_model_client_config(),
            model_config_obj=_create_model_request_config(),
        )

        agent = ReActAgent(card=card)
        agent.configure(config)

        # Opt-in: register OtelRail for agent-root + LLM span lifecycle
        await agent.register_rail(OtelRail())

        session = create_agent_session(session_id="otel_rail_test", card=card)
        await session.pre_run()

        result = await agent.invoke(
            {"query": "What is 1+1? Just answer the number.", "conversation_id": "otel_rail_test"},
            session=session,
        )

        assert result is not None

        finished = _EXPORTER.get_finished_spans()
        all_names = [s.name for s in finished]

        # Root chain span (OtelRail.before_invoke → on_chain_start)
        chain_spans = [s for s in finished if s.name.startswith("chain.")]
        assert len(chain_spans) >= 1, f"Expected chain span, got: {all_names}"
        chain_span = chain_spans[0]
        assert chain_span.kind == trace.SpanKind.INTERNAL
        assert chain_span.attributes.get(GEN_AI_SYSTEM) == GEN_AI_SYSTEM_VALUE
        assert chain_span.attributes.get(OJ_AGENT_INVOKE_TYPE) is not None
        assert chain_span.attributes.get(OJ_AGENT_NAME) == "otel_rail_agent"

        # LLM child span (OtelRail.before_model_call → on_llm_start)
        llm_spans = [s for s in finished if s.name.startswith("llm.")]
        assert len(llm_spans) >= 1, f"Expected llm span, got: {all_names}"
        llm_span = llm_spans[0]
        assert llm_span.kind == trace.SpanKind.CLIENT
        assert llm_span.attributes.get(GEN_AI_SYSTEM) == GEN_AI_SYSTEM_VALUE
        assert llm_span.attributes.get(GEN_AI_OPERATION_NAME) == "chat"
        assert llm_span.attributes.get(GEN_AI_REQUEST_MODEL) is not None

        # Parent-child relationship: LLM span's parent should be the chain span
        assert llm_span.parent is not None
        assert llm_span.parent.span_id == chain_span.context.span_id, (
            f"LLM span parent {llm_span.parent.span_id} != chain span {chain_span.context.span_id}"
        )


# ---------------------------------------------------------------------------
# E2E: Agent LLM + tool span via tracer.trigger (manual verification)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not _LLM_CREDENTIALS_AVAILABLE,
    reason="LLM credentials not set",
)
class TestE2EAgentSpanViaTracerTrigger:
    """Verify OtelAgentHandler correctly translates LLM and tool events
    when triggered via tracer.trigger() manually.

    This test validates the OTel handler's event translation logic for
    LLM spans (SpanKind.CLIENT, gen_ai.* attributes) and tool spans
    (SpanKind.INTERNAL, tool.* name) without requiring real agent invoke.
    """

    async def test_agent_llm_span_via_tracer_trigger(self):
        """LLM span produced via tracer.trigger on_llm_start/on_llm_end."""
        config = OtelTracerConfig(redaction_enabled=False)
        TracerHandlerRegistry.register_handler("otel_agent", OtelAgentHandler(_OTEL_TRACER, config))

        tracer = Tracer()
        tracer.init()

        # Create and trigger an LLM span
        llm_span = tracer.tracer_agent_span_manager.create_agent_span()
        await tracer.trigger(
            TracerHandlerName.TRACE_AGENT.value, "on_llm_start",
            span=llm_span, inputs="test prompt",
            instance_info={"class_name": "TestLLMModel", "type": "llm"},
        )
        await tracer.trigger(
            TracerHandlerName.TRACE_AGENT.value, "on_llm_end",
            span=llm_span, outputs="test response",
        )

        finished = _EXPORTER.get_finished_spans()
        assert len(finished) >= 1

        # Verify LLM span attributes
        llm_spans = [s for s in finished if s.kind == trace.SpanKind.CLIENT]
        assert len(llm_spans) >= 1
        for ls in llm_spans:
            assert ls.name.startswith("llm.")
            assert ls.attributes.get(GEN_AI_SYSTEM) == GEN_AI_SYSTEM_VALUE
            assert ls.attributes.get(GEN_AI_OPERATION_NAME) == "chat"
            assert ls.attributes.get(GEN_AI_REQUEST_MODEL) is not None

    async def test_agent_tool_span_via_tracer_trigger(self):
        """Tool (plugin) span produced via tracer.trigger on_plugin_start/on_plugin_end."""
        config = OtelTracerConfig(redaction_enabled=False)
        TracerHandlerRegistry.register_handler("otel_agent", OtelAgentHandler(_OTEL_TRACER, config))

        tracer = Tracer()
        tracer.init()

        # Create and trigger a tool span
        tool_span = tracer.tracer_agent_span_manager.create_agent_span()
        tool_span.invoke_type = "plugin"
        tool_span.name = "get_weather"
        await tracer.trigger(
            TracerHandlerName.TRACE_AGENT.value, "on_plugin_start",
            span=tool_span, inputs={"city": "beijing"},
            instance_info={"class_name": "get_weather", "type": "tool"},
        )
        await tracer.trigger(
            TracerHandlerName.TRACE_AGENT.value, "on_plugin_end",
            span=tool_span, outputs={"result": "Beijing: sunny"},
        )

        finished = _EXPORTER.get_finished_spans()
        assert len(finished) >= 1

        # Verify tool span attributes
        tool_spans = [s for s in finished if s.name.startswith("tool.")]
        assert len(tool_spans) >= 1
        for ts in tool_spans:
            assert ts.kind == trace.SpanKind.INTERNAL
            assert ts.attributes.get(GEN_AI_SYSTEM) == GEN_AI_SYSTEM_VALUE
            assert ts.attributes.get(GEN_AI_OPERATION_NAME) == "execute_tool"
            assert ts.attributes.get(OJ_AGENT_INVOKE_TYPE) is not None
            assert ts.attributes.get(OJ_AGENT_NAME) is not None
            assert ts.attributes.get(OJ_AGENT_INPUTS) is not None
            assert ts.attributes.get(OJ_AGENT_OUTPUTS) is not None


# ---------------------------------------------------------------------------
# E2E: Agent with LLM + tool — Jaeger verification
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not _LLM_CREDENTIALS_AVAILABLE,
    reason="LLM credentials not set",
)
@pytest.mark.skipif(
    not jaeger_is_available(),
    reason="Jaeger not available at localhost:16686",
)
class TestE2EAgentJaegerIntegration:
    """Agent spans exported to Jaeger via OTLP and verified through HTTP API."""

    async def asyncSetUp(self):
        from openjiuwen.core.runner import Runner
        await Runner.start()

    async def asyncTearDown(self):
        from openjiuwen.core.runner import Runner
        await Runner.stop()

    async def test_agent_tool_spans_in_jaeger(self):
        """Agent tool span is visible in Jaeger after real agent invoke."""
        _register_otel_handlers()

        card = AgentCard(
            name="otel_jaeger_agent",
            description="Agent for Jaeger OTel e2e test",
        )

        config = ReActAgentConfig(
            model_name=MODEL_NAME,
            model_provider=MODEL_PROVIDER,
            api_key=API_KEY,
            api_base=API_BASE,
            prompt_template=[
                {"role": "system", "content": "You are a helpful assistant. Use the get_weather tool when asked about weather."},
            ],
            max_iterations=3,
            model_client_config=_create_model_client_config(),
            model_config_obj=_create_model_request_config(),
        )

        agent = ReActAgent(card=card)
        agent.configure(config)

        weather_tool = _create_weather_tool()
        agent.ability_manager.add_ability(weather_tool.card, weather_tool)

        session = create_agent_session(session_id="otel_jaeger_agent_test", card=card)
        await session.pre_run()

        result = await agent.invoke(
            {"query": "What's the weather in Shanghai?", "conversation_id": "otel_jaeger_agent_test"},
            session=session,
        )

        assert result is not None

        # Wait for BatchSpanProcessor to export
        import asyncio
        await asyncio.sleep(6)

        span_names = _query_jaeger_traces("openjiuwen")

        # Expect tool span names (LLM spans not auto-generated via agent invoke)
        tool_span_names = [s for s in span_names if s.startswith("tool.")]
        assert len(tool_span_names) >= 1, (
            f"Expected at least 1 tool span in Jaeger. Found: {span_names}"
        )

        # Also verify total span count >= 1
        assert len(span_names) >= 1, (
            f"Expected at least 1 span in Jaeger. Found: {span_names}"
        )


def _query_jaeger_traces(service_name: str) -> list[str]:
    """Query Jaeger HTTP API for span names of a given service."""
    import requests
    resp = requests.get(
        "http://localhost:16686/api/traces",
        params={"service": service_name, "limit": 10},
        timeout=10,
    )
    data = resp.json()
    traces = data.get("data", [])
    all_span_names = []
    for trace_data in traces:
        for span in trace_data.get("spans", []):
            all_span_names.append(span.get("operationName", ""))
    return all_span_names
