# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Regression tests for bug #2046: duplicate execute_tool spans.

With OtelRail registered, one physical tool call used to emit TWO
``execute_tool`` spans — OtelRail's BEFORE/AFTER_TOOL_CALL hooks and the
legacy session-tracer decorator wrapping ``tool.invoke`` both fired
``on_plugin_start``. These tests pin the fixed contract:

- rail + decorator both active  -> exactly one execute_tool span per call,
  carrying BOTH the decorator payload contract (``inputs``/``outputs``
  wrappers) and the rail's GenAI tool identity attributes;
- decorator only (no rail)      -> still exactly one span (dedupe must not
  suppress the sole emitter);
- parallel same-name tool calls -> one span per call, each paired with its
  own tool-call id and arguments;
- tool failure                  -> a single error span (the decorator's
  duplicate error event is a no-op).
"""

import ast
import os
from unittest.mock import patch

import pytest

from openjiuwen.core.foundation.llm.schema.message import AssistantMessage
from openjiuwen.core.foundation.llm.schema.tool_call import ToolCall
from openjiuwen.core.foundation.tool import LocalFunction, ToolCard
from openjiuwen.core.runner import Runner
from openjiuwen.core.session.agent import create_agent_session
from openjiuwen.core.session.tracer.tracer import TracerHandlerRegistry
from openjiuwen.core.single_agent.agents.react_agent import ReActAgent, ReActAgentConfig
from openjiuwen.core.single_agent.schema.agent_card import AgentCard
from openjiuwen.extensions.tracer_otel.config import OtelTracerConfig
from openjiuwen.extensions.tracer_otel.handler import OtelAgentHandler, OtelWorkflowHandler
from openjiuwen.extensions.tracer_otel.otel_rail import OtelRail
from openjiuwen.extensions.tracer_otel.semconv import (
    GEN_AI_AGENT_ID,
    GEN_AI_AGENT_NAME,
    GEN_AI_OPERATION_NAME,
    GEN_AI_TOOL_CALL_ID,
    GEN_AI_TOOL_NAME,
    OJ_AGENT_ERROR_MESSAGE,
    OJ_AGENT_INPUTS,
    OJ_AGENT_OUTPUTS,
    OJ_AGENT_NAME,
)

from tests.conftest_otel import _EXPORTER, _OTEL_TRACER
from tests.unit_tests.fixtures.mock_llm import (
    MockLLMModel,
    create_text_response,
    create_tool_call_response,
)

pytestmark = pytest.mark.asyncio


def _payload(span, key: str) -> dict:
    """Parse an openjiuwen.agent.* payload attribute.

    The handler stores these via ``redact()``, i.e. ``str(value)`` — a
    Python-repr string, not JSON.
    """
    return ast.literal_eval(span.attributes[key])


def _get_weather(city: str) -> str:
    return f"{city}: 22C, sunny"


def _make_agent(agent_id: str) -> ReActAgent:
    card = AgentCard(id=agent_id, name=agent_id, description="weather helper")
    agent = ReActAgent(card=card)
    config = ReActAgentConfig()
    config.configure_model_client(
        provider="OpenAI",
        api_key="sk-fake",
        api_base="https://api.openai.com/v1",
        model_name="mock-model",
        verify_ssl=False,
    )
    config.configure_prompt_template(
        [{"role": "system", "content": "Use the get_weather tool for weather questions."}]
    )
    agent.configure(config)
    return agent


def _make_weather_tool() -> LocalFunction:
    card = ToolCard(
        name="get_weather",
        description="Get weather for a city.",
        input_params={
            "type": "object",
            "properties": {"city": {"type": "string"}},
            "required": ["city"],
        },
    )
    return LocalFunction(card=card, func=_get_weather)


def _make_parallel_turn() -> AssistantMessage:
    """One assistant message with two same-name tool calls (parallel turn)."""
    return AssistantMessage(
        content="",
        tool_calls=[
            ToolCall(id="mock_call_beijing", type="function", name="get_weather",
                     arguments='{"city": "Beijing"}'),
            ToolCall(id="mock_call_shanghai", type="function", name="get_weather",
                     arguments='{"city": "Shanghai"}'),
        ],
        finish_reason="tool_calls",
    )


class _ToolSpanDedupScenario:
    """ReAct agent + traced session + OTel handlers, mirroring the QA case
    test_otel_agent_tool_stream_001 (bug #2046)."""

    def __init__(self, agent_id: str, tool: LocalFunction, with_rail: bool) -> None:
        self.agent = _make_agent(agent_id)
        self.agent.ability_manager.add_ability(tool.card, tool)
        self.with_rail = with_rail
        self.conversation_id = f"{agent_id}-conv"

    async def run(self, mock_llm: MockLLMModel) -> dict:
        os.environ.setdefault("LLM_SSL_VERIFY", "false")
        await Runner.start()
        TracerHandlerRegistry.clear()
        _EXPORTER.clear()
        config = OtelTracerConfig(redaction_enabled=False)
        try:
            TracerHandlerRegistry.register_handler("otel_agent", OtelAgentHandler(_OTEL_TRACER, config))
            TracerHandlerRegistry.register_handler("otel_workflow", OtelWorkflowHandler(_OTEL_TRACER, config))

            session = create_agent_session(session_id=self.conversation_id, card=self.agent.card)
            await session.pre_run()

            if self.with_rail:
                await self.agent.register_rail(OtelRail())

            with patch(
                "openjiuwen.core.foundation.llm.model.Model.invoke",
                side_effect=mock_llm.invoke,
            ), patch(
                "openjiuwen.core.foundation.llm.model.Model.stream",
                side_effect=mock_llm.stream,
            ):
                result = await self.agent.invoke(
                    {"query": "weather?", "conversation_id": self.conversation_id},
                    session=session,
                    _streaming=True,
                )
            self.result = result
            self.spans = _EXPORTER.get_finished_spans()
            return result
        finally:
            TracerHandlerRegistry.clear()
            _EXPORTER.clear()
            await Runner.stop()

    def tool_spans(self) -> list:
        # Select by the operation attribute, not span name: display naming
        # differs across release branches ("execute_tool X" vs "tool.X")
        # while the GenAI attribute is set by both.
        return [
            s for s in self.spans
            if s.attributes.get(GEN_AI_OPERATION_NAME) == "execute_tool"
        ]


class TestToolSpanSingleEmission:
    async def test_rail_and_decorator_emit_one_tool_span(self):
        """Bug #2046 scenario: both emitters active -> exactly one span whose
        payload is a superset of the two contracts."""
        scenario = _ToolSpanDedupScenario("dedup_agent", _make_weather_tool(), with_rail=True)
        mock_llm = MockLLMModel()
        mock_llm.set_responses([
            create_tool_call_response("get_weather", '{"city": "Beijing"}'),
            create_text_response("Beijing: 22C, sunny"),
        ])
        await scenario.run(mock_llm)

        spans = scenario.tool_spans()
        assert len(spans) == 1, f"expected 1 execute_tool span, got {len(spans)}"
        span = spans[0]
        # Decorator contract (payload shape): parsed arguments under "inputs",
        # result wrapped under "outputs".
        assert _payload(span, OJ_AGENT_INPUTS) == {
            "id": "mock_call_get_weather",
            "name": "get_weather",
            "type": "function",
            "inputs": {"city": "Beijing"},
        }
        assert _payload(span, OJ_AGENT_OUTPUTS) == {"outputs": "Beijing: 22C, sunny"}
        # Rail contract (GenAI tool identity) survives on the same span.
        assert span.attributes[GEN_AI_TOOL_CALL_ID] == "mock_call_get_weather"
        assert span.attributes[GEN_AI_TOOL_NAME] == "get_weather"
        assert span.attributes[GEN_AI_AGENT_NAME] == "dedup_agent"
        assert span.attributes[GEN_AI_AGENT_ID] == "dedup_agent"

    async def test_decorator_only_still_emits_tool_span(self):
        """Without OtelRail the decorator is the sole emitter — dedupe must
        not suppress it."""
        scenario = _ToolSpanDedupScenario("decorator_only_agent", _make_weather_tool(), with_rail=False)
        mock_llm = MockLLMModel()
        mock_llm.set_responses([
            create_tool_call_response("get_weather", '{"city": "Beijing"}'),
            create_text_response("Beijing: 22C, sunny"),
        ])
        await scenario.run(mock_llm)

        spans = scenario.tool_spans()
        assert len(spans) == 1, f"expected 1 execute_tool span, got {len(spans)}"
        span = spans[0]
        assert _payload(span, OJ_AGENT_INPUTS) == {"inputs": {"city": "Beijing"}}
        assert _payload(span, OJ_AGENT_OUTPUTS) == {"outputs": "Beijing: 22C, sunny"}
        assert GEN_AI_TOOL_CALL_ID not in span.attributes

    async def test_parallel_same_name_calls_emit_one_span_each(self):
        """Two same-name tool calls in one turn: one span per call, each with
        its own tool-call id and arguments (no cross-wiring, no collapse)."""
        scenario = _ToolSpanDedupScenario("parallel_agent", _make_weather_tool(), with_rail=True)
        mock_llm = MockLLMModel()
        mock_llm.set_responses([
            _make_parallel_turn(),
            create_text_response("Beijing 22C, Shanghai 25C"),
        ])
        await scenario.run(mock_llm)

        spans = scenario.tool_spans()
        assert len(spans) == 2, f"expected 2 execute_tool spans, got {len(spans)}"
        by_call = {s.attributes[GEN_AI_TOOL_CALL_ID]: s for s in spans}
        assert set(by_call) == {"mock_call_beijing", "mock_call_shanghai"}
        assert _payload(by_call["mock_call_beijing"], OJ_AGENT_INPUTS)["inputs"] == {"city": "Beijing"}
        assert _payload(by_call["mock_call_shanghai"], OJ_AGENT_INPUTS)["inputs"] == {"city": "Shanghai"}

    async def test_failing_tool_emits_single_error_span(self):
        """A failing tool emits one error span; the decorator's duplicate
        error event for the skipped span must be a no-op."""
        def _boom(city: str) -> str:
            raise RuntimeError("weather service down")

        card = ToolCard(
            name="get_weather",
            description="Get weather for a city.",
            input_params={
                "type": "object",
                "properties": {"city": {"type": "string"}},
                "required": ["city"],
            },
        )
        scenario = _ToolSpanDedupScenario(
            "failing_agent", LocalFunction(card=card, func=_boom), with_rail=True
        )
        mock_llm = MockLLMModel()
        mock_llm.set_responses([
            create_tool_call_response("get_weather", '{"city": "Beijing"}'),
            create_text_response("weather unavailable"),
        ])
        await scenario.run(mock_llm)

        spans = scenario.tool_spans()
        assert len(spans) == 1, f"expected 1 execute_tool span, got {len(spans)}"
        span = spans[0]
        assert OJ_AGENT_ERROR_MESSAGE in span.attributes
        assert span.attributes[OJ_AGENT_NAME] == "get_weather"

    async def test_dedupe_probe_failure_fails_open(self):
        """A raising dedupe probe must fail open — the rail's tool span is
        still created. Fail-closed (probe sharing the method's fallback)
        would lose the sole execute_tool span for the call entirely."""
        scenario = _ToolSpanDedupScenario(
            "probe_fail_agent", _make_weather_tool(), with_rail=True
        )
        mock_llm = MockLLMModel()
        mock_llm.set_responses([
            create_tool_call_response("get_weather", '{"city": "Beijing"}'),
            create_text_response("Beijing: 22C, sunny"),
        ])

        def _probe_raises(self, span, instance_info):
            raise RuntimeError("probe exploded")

        with patch.object(OtelAgentHandler, "_is_duplicate_tool_event", _probe_raises):
            await scenario.run(mock_llm)

        # Probe down → dedupe disabled for this run → both emitters export
        # (rail + decorator). The rail span (tool-call id carrier) must
        # survive — that is the fail-open contract.
        spans = scenario.tool_spans()
        assert any(
            s.attributes.get(GEN_AI_TOOL_CALL_ID) for s in spans
        ), "fail-open: rail tool span must survive a probe failure"
        assert any(
            not s.attributes.get(GEN_AI_TOOL_CALL_ID) for s in spans
        ), "probe down: decorator twin is expected alongside (dedupe off)"
