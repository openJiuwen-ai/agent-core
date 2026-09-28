# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unit tests for OtelRail callback wiring (issue #1833).

OtelRail is the only emitter of agent-dimension tracer events: it turns
AgentRail callbacks into ``tracer.trigger`` calls.  These tests stub the
tracer/session and verify the forwarded event names, ``instance_info``
payloads, and span-stack bookkeeping — the OTel attribute side is covered
by ``test_handler.py``.
"""

import pytest

from openjiuwen.core.foundation.llm.schema.config import ModelRequestConfig
from openjiuwen.core.foundation.llm.schema.message import AssistantMessage, UsageMetadata
from openjiuwen.core.foundation.llm.schema.tool_call import ToolCall
from openjiuwen.core.session.tracer.handler import TracerHandlerName
from openjiuwen.core.session.tracer.span import SpanManager
from openjiuwen.core.session.tracer.tracer import Tracer, TracerHandlerRegistry
from openjiuwen.core.single_agent.rail.base import (
    AgentCallbackContext,
    InvokeInputs,
    ModelCallInputs,
    ToolCallInputs,
)
from openjiuwen.extensions.tracer_otel.config import OtelTracerConfig
from openjiuwen.extensions.tracer_otel.handler import OtelAgentHandler
from openjiuwen.extensions.tracer_otel.otel_rail import OtelRail
from openjiuwen.extensions.tracer_otel.semconv import (
    GEN_AI_AGENT_NAME,
    GEN_AI_REQUEST_MAX_TOKENS,
    GEN_AI_REQUEST_TEMPERATURE,
    GEN_AI_REQUEST_TOP_P,
    GEN_AI_RESPONSE_FINISH_REASONS,
    GEN_AI_RESPONSE_MODEL,
    GEN_AI_TOOL_CALL_ID,
    GEN_AI_TOOL_NAME,
    GEN_AI_TOOL_TYPE,
    GEN_AI_USAGE_INPUT_TOKENS,
    GEN_AI_USAGE_OUTPUT_TOKENS,
    OJ_GEN_AI_USAGE_TOTAL_COST,
    OJ_LLM_PREV_MESSAGE_COUNT,
)
from tests.conftest_otel import _EXPORTER, _OTEL_TRACER

pytestmark = pytest.mark.asyncio


class _StubTracer:
    """Records trigger() calls; creates real TraceAgentSpans."""

    def __init__(self):
        self._span_manager = SpanManager("test-trace-id")
        self.calls: list[tuple[str, str, dict]] = []

    @property
    def tracer_agent_span_manager(self) -> SpanManager:
        return self._span_manager

    async def trigger(self, handler_class_name: str, event_name: str, **kwargs):
        self.calls.append((handler_class_name, event_name, kwargs))


class _StubSession:
    def __init__(self, tracer: _StubTracer):
        self._tracer = tracer
        self.agent_span = None

    def tracer(self) -> _StubTracer:
        return self._tracer


class _StubCard:
    def __init__(self, name: str = "HelperAgent", card_id: str = "card-abc"):
        self.name = name
        self.id = card_id


class _StubAgentConfig:
    def __init__(self, model_config_obj=None):
        self.model_config_obj = model_config_obj


class _StubAgent:
    def __init__(self, config=None, card=None):
        self.config = config
        self.card = card or _StubCard()


def _make_ctx(agent, session, inputs, exception=None) -> AgentCallbackContext:
    return AgentCallbackContext(agent=agent, session=session, inputs=inputs, exception=exception)


def _events(tracer: _StubTracer) -> list[str]:
    return [name for _, name, _ in tracer.calls]


class TestOtelRailAgentCallbacks:
    async def test_before_invoke_forwards_agent_id(self):
        tracer = _StubTracer()
        session = _StubSession(tracer)
        agent = _StubAgent(card=_StubCard(name="MyAgent", card_id="id-123"))
        rail = OtelRail()

        await rail.before_invoke(_make_ctx(agent, session, inputs={}))

        assert _events(tracer) == ["on_chain_start"]
        handler_name, _, kwargs = tracer.calls[0]
        assert handler_name == TracerHandlerName.TRACE_AGENT.value
        assert kwargs["instance_info"]["class_name"] == "MyAgent"
        assert kwargs["instance_info"]["agent_id"] == "id-123"
        assert session.agent_span is kwargs["span"]

    async def test_before_invoke_without_card_id_omits_none(self):
        tracer = _StubTracer()
        session = _StubSession(tracer)
        card = _StubCard()
        card.id = None
        agent = _StubAgent(card=card)
        rail = OtelRail()

        await rail.before_invoke(_make_ctx(agent, session, inputs={}))

        # getattr fallback yields None — handler skips falsy agent_id.
        _, _, kwargs = tracer.calls[0]
        assert kwargs["instance_info"]["agent_id"] is None

    async def test_before_model_call_extracts_request_params_and_count(self):
        tracer = _StubTracer()
        session = _StubSession(tracer)
        config = _StubAgentConfig(
            ModelRequestConfig(
                model="test-model",
                temperature=0.5,
                top_p=0.9,
                max_tokens=1024,
                top_k=40,
            )
        )
        agent = _StubAgent(config=config)
        rail = OtelRail()
        inputs = ModelCallInputs(messages=["m1", "m2", "m3"])

        await rail.before_model_call(_make_ctx(agent, session, inputs))

        assert _events(tracer) == ["on_llm_start"]
        _, _, kwargs = tracer.calls[0]
        assert kwargs["instance_info"]["class_name"] == "test-model"
        assert kwargs["instance_info"]["request_params"] == {
            "temperature": 0.5,
            "top_p": 0.9,
            "top_k": 40,
            "max_tokens": 1024,
        }
        assert kwargs["instance_info"]["message_count"] == 3
        assert len(rail._llm_spans) == 1

    async def test_before_model_call_without_config_sets_no_params(self):
        tracer = _StubTracer()
        session = _StubSession(tracer)
        agent = _StubAgent(config=None)
        rail = OtelRail()

        await rail.before_model_call(_make_ctx(agent, session, ModelCallInputs()))

        _, _, kwargs = tracer.calls[0]
        assert "request_params" not in kwargs["instance_info"]
        # Empty message list still reports a (zero) count — it is a valid fact.
        assert kwargs["instance_info"]["message_count"] == 0
        assert kwargs["instance_info"]["class_name"] == "LLM"


class TestOtelRailToolCallbacks:
    async def test_before_tool_call_forwards_tool_semconv_payload(self):
        tracer = _StubTracer()
        session = _StubSession(tracer)
        agent = _StubAgent(card=_StubCard(name="HelperAgent"))
        rail = OtelRail()
        tool_call = ToolCall(id="call-9", type="function", name="echo", arguments="{}")
        inputs = ToolCallInputs(tool_call=tool_call, tool_name="echo", tool_args={})

        await rail.before_tool_call(_make_ctx(agent, session, inputs))

        assert _events(tracer) == ["on_plugin_start"]
        _, _, kwargs = tracer.calls[0]
        assert kwargs["instance_info"]["class_name"] == "echo"
        assert kwargs["instance_info"]["tool_type"] == "function"
        assert kwargs["instance_info"]["agent_name"] == "HelperAgent"
        assert kwargs["inputs"] == {"id": "call-9", "name": "echo", "type": "function"}
        assert len(rail._tool_spans) == 1

    async def test_before_tool_call_defaults_when_no_tool_call_object(self):
        tracer = _StubTracer()
        session = _StubSession(tracer)
        agent = _StubAgent()
        rail = OtelRail()
        inputs = ToolCallInputs(tool_name="lookup")

        await rail.before_tool_call(_make_ctx(agent, session, inputs))

        _, _, kwargs = tracer.calls[0]
        assert kwargs["instance_info"]["class_name"] == "lookup"
        assert kwargs["instance_info"]["tool_type"] == "function"
        assert kwargs["inputs"] == {}

    async def test_after_tool_call_pops_span_and_forwards_result(self):
        tracer = _StubTracer()
        session = _StubSession(tracer)
        agent = _StubAgent()
        rail = OtelRail()
        inputs = ToolCallInputs(tool_name="echo")

        await rail.before_tool_call(_make_ctx(agent, session, inputs))
        inputs.tool_result = "ok"
        await rail.after_tool_call(_make_ctx(agent, session, inputs))

        assert _events(tracer) == ["on_plugin_start", "on_plugin_end"]
        _, _, kwargs = tracer.calls[1]
        assert kwargs["outputs"] == "ok"
        assert rail._tool_spans == []

    async def test_on_tool_exception_forwards_error(self):
        tracer = _StubTracer()
        session = _StubSession(tracer)
        agent = _StubAgent()
        rail = OtelRail()
        inputs = ToolCallInputs(tool_name="echo")

        await rail.before_tool_call(_make_ctx(agent, session, inputs))
        error = RuntimeError("tool boom")
        await rail.on_tool_exception(_make_ctx(agent, session, inputs, exception=error))

        assert _events(tracer) == ["on_plugin_start", "on_plugin_error"]
        _, _, kwargs = tracer.calls[1]
        assert kwargs["error"] is error
        assert rail._tool_spans == []

    async def test_after_tool_call_without_pending_span_is_noop(self):
        tracer = _StubTracer()
        session = _StubSession(tracer)
        rail = OtelRail()

        await rail.after_tool_call(_make_ctx(_StubAgent(), session, ToolCallInputs()))
        await rail.on_tool_exception(_make_ctx(_StubAgent(), session, ToolCallInputs(), exception=RuntimeError("x")))

        assert tracer.calls == []


class TestOtelRailNoSession:
    async def test_all_callbacks_noop_without_session(self):
        rail = OtelRail()
        agent = _StubAgent()

        await rail.before_invoke(_make_ctx(agent, None, inputs={}))
        await rail.after_invoke(_make_ctx(agent, None, inputs={}))
        await rail.before_model_call(_make_ctx(agent, None, ModelCallInputs()))
        await rail.before_tool_call(_make_ctx(agent, None, ToolCallInputs()))
        # No exception, no span leaks
        assert rail._llm_spans == []
        assert rail._tool_spans == []


class TestOtelRailEndToEnd:
    """Wire OtelRail to a real Tracer with a registered OtelAgentHandler
    and assert the emitted spans carry the GenAI semconv attributes."""

    async def test_llm_and_tool_spans_carry_genai_attrs(self):
        TracerHandlerRegistry.clear()
        _EXPORTER.clear()
        try:
            handler = OtelAgentHandler(
                _OTEL_TRACER, OtelTracerConfig(redaction_enabled=False), trace_id="e2e-trace"
            )
            TracerHandlerRegistry.register_handler("otel_agent", handler)
            tracer = Tracer(session_id="e2e-session")
            tracer.init()

            session = _StubSession(tracer)
            config = _StubAgentConfig(
                ModelRequestConfig(model="test-model", temperature=0.3, top_p=0.8, max_tokens=512)
            )
            agent = _StubAgent(config=config, card=_StubCard(name="E2EAgent", card_id="card-e2e"))
            rail = OtelRail()

            # Agent root span
            await rail.before_invoke(_make_ctx(agent, session, InvokeInputs(query="hi")))

            # LLM child span with params + usage/cost-bearing response
            await rail.before_model_call(
                _make_ctx(agent, session, ModelCallInputs(messages=["m1", "m2"]))
            )
            response = AssistantMessage(
                content="answer",
                finish_reason="stop",
                usage_metadata=UsageMetadata(
                    model_name="test-model",
                    input_tokens=10,
                    output_tokens=20,
                    input_cost=0.1,
                    output_cost=0.2,
                    total_cost=0.3,
                ),
            )
            ctx = _make_ctx(agent, session, ModelCallInputs(response=response))
            await rail.after_model_call(ctx)

            # Tool child span
            tool_call = ToolCall(id="call-1", type="function", name="echo", arguments="{}")
            tool_inputs = ToolCallInputs(tool_call=tool_call, tool_name="echo", tool_args="{}")
            await rail.before_tool_call(_make_ctx(agent, session, tool_inputs))
            tool_inputs.tool_result = "ok"
            await rail.after_tool_call(_make_ctx(agent, session, tool_inputs))

            # Agent root span end
            end_ctx = _make_ctx(agent, session, InvokeInputs(query="hi"))
            await rail.after_invoke(end_ctx)

            spans = _EXPORTER.get_finished_spans()
            by_name = {s.name: s for s in spans}

            llm_span = by_name["chat test-model"]
            assert llm_span.attributes[GEN_AI_REQUEST_TEMPERATURE] == 0.3
            assert llm_span.attributes[GEN_AI_REQUEST_TOP_P] == 0.8
            assert llm_span.attributes[GEN_AI_REQUEST_MAX_TOKENS] == 512
            assert llm_span.attributes[OJ_LLM_PREV_MESSAGE_COUNT] == 2
            assert llm_span.attributes[GEN_AI_RESPONSE_FINISH_REASONS] == ("stop",)
            assert llm_span.attributes[GEN_AI_RESPONSE_MODEL] == "test-model"
            assert llm_span.attributes[GEN_AI_USAGE_INPUT_TOKENS] == 10
            assert llm_span.attributes[GEN_AI_USAGE_OUTPUT_TOKENS] == 20
            assert llm_span.attributes[OJ_GEN_AI_USAGE_TOTAL_COST] == pytest.approx(0.3)

            tool_span = by_name["execute_tool echo"]
            assert tool_span.attributes[GEN_AI_TOOL_NAME] == "echo"
            assert tool_span.attributes[GEN_AI_TOOL_TYPE] == "function"
            assert tool_span.attributes[GEN_AI_TOOL_CALL_ID] == "call-1"
            assert tool_span.attributes[GEN_AI_AGENT_NAME] == "E2EAgent"

            # LLM and tool spans share the agent root span's trace
            agent_span_names = [s.name for s in spans if s.name not in ("chat test-model", "execute_tool echo")]
            assert agent_span_names, "agent root span expected"
            root = spans[[s.name for s in spans].index(agent_span_names[0])]
            assert llm_span.context.trace_id == root.context.trace_id
            assert tool_span.context.trace_id == root.context.trace_id
        finally:
            TracerHandlerRegistry.clear()
            _EXPORTER.clear()
