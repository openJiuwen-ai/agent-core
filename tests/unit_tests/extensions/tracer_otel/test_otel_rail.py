# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unit tests for OtelRail callback wiring (issue #1833).

OtelRail is the only emitter of agent-dimension tracer events: it turns
AgentRail callbacks into ``tracer.trigger`` calls.  These tests stub the
tracer/session and verify the forwarded event names, ``instance_info``
payloads, and span-stack bookkeeping — the OTel attribute side is covered
by ``test_handler.py``.
"""

import json

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
    GEN_AI_AGENT_DESCRIPTION,
    GEN_AI_AGENT_ID,
    GEN_AI_AGENT_NAME,
    GEN_AI_CONVERSATION_ID,
    GEN_AI_PROVIDER_NAME,
    GEN_AI_REQUEST_MAX_TOKENS,
    GEN_AI_REQUEST_REASONING_LEVEL,
    GEN_AI_REQUEST_STOP_SEQUENCES,
    GEN_AI_REQUEST_TEMPERATURE,
    GEN_AI_REQUEST_TOP_P,
    GEN_AI_RESPONSE_FINISH_REASONS,
    GEN_AI_RESPONSE_MODEL,
    GEN_AI_RESPONSE_TIME_TO_FIRST_CHUNK,
    GEN_AI_TOOL_CALL_ID,
    GEN_AI_TOOL_NAME,
    GEN_AI_TOOL_TYPE,
    GEN_AI_USAGE_INPUT_TOKENS,
    GEN_AI_USAGE_OUTPUT_TOKENS,
    OJ_GEN_AI_METADATA,
    OJ_GEN_AI_TRACE_NAME,
    OJ_GEN_AI_USAGE_TOTAL_COST,
    OJ_GEN_AI_USER_ID,
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
    def __init__(self, tracer: _StubTracer, session_id: str = "sess-1", source_metadata=None):
        self._tracer = tracer
        self.agent_span = None
        self._session_id = session_id
        self._source_metadata = source_metadata if source_metadata is not None else {"user_id": "u-77"}

    def tracer(self) -> _StubTracer:
        return self._tracer

    def get_session_id(self) -> str:
        return self._session_id


class _StubCard:
    def __init__(self, name: str = "HelperAgent", card_id: str = "card-abc", description: str = "A helper agent"):
        self.name = name
        self.id = card_id
        self.description = description


class _StubAgentConfig:
    def __init__(self, model_config_obj=None):
        self.model_config_obj = model_config_obj


class _StubAgent:
    def __init__(self, config=None, card=None, llm=None):
        self.config = config
        self.card = card or _StubCard()
        self._llm = llm


class _StubLlm:
    """Minimal stand-in for the agent's Model: carries a ModelRequestConfig."""

    def __init__(self, model_config):
        self.model_config = model_config


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

    async def test_common_info_carries_identity_and_session_facts(self):
        """_build_common_info feeds every span: description, conversation id,
        user id, and raw source metadata (issue #1833 project extensions)."""
        tracer = _StubTracer()
        session = _StubSession(
            tracer, session_id="sess-9", source_metadata={"user_id": "u-1", "channel": "web"}
        )
        agent = _StubAgent(card=_StubCard(name="MyAgent", card_id="id-123", description="Does things"))
        rail = OtelRail()

        await rail.before_invoke(_make_ctx(agent, session, inputs={}))

        _, _, kwargs = tracer.calls[0]
        info = kwargs["instance_info"]
        assert info["agent_description"] == "Does things"
        assert info["agent_name"] == "MyAgent"
        assert info["conversation_id"] == "sess-9"
        assert info["session_id"] == "sess-9"
        assert info["user_id"] == "u-1"
        assert info["metadata"] == {"user_id": "u-1", "channel": "web"}

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
                stop="END",
                reasoning={"effort": "high"},
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
            "stop_sequences": ["END"],
            "reasoning_level": "high",
        }
        assert kwargs["instance_info"]["message_count"] == 3
        assert len(rail._llm_spans) == 1

    async def test_before_model_call_prefers_llm_object_over_config(self):
        """The live LLM object is the authoritative param source — covers
        set_llm-built agents whose config carries no model_config_obj."""
        tracer = _StubTracer()
        session = _StubSession(tracer)
        config = _StubAgentConfig(ModelRequestConfig(model="stale-config-model", temperature=9.9))
        agent = _StubAgent(
            config=config,
            llm=_StubLlm(ModelRequestConfig(model="live-model", temperature=0.2)),
        )
        rail = OtelRail()

        await rail.before_model_call(_make_ctx(agent, session, ModelCallInputs(messages=["m1"])))

        _, _, kwargs = tracer.calls[0]
        assert kwargs["instance_info"]["class_name"] == "live-model"
        assert kwargs["instance_info"]["request_params"] == {"temperature": 0.2}

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
        assert kwargs["instance_info"]["tool_call_id"] == "call-9"
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


class _ExplodingTracerSession(_StubSession):
    """tracer() raises — simulates a broken tracer/session stack."""

    def tracer(self):
        raise RuntimeError("tracer unavailable")


class _BrokenSessionIdSession(_StubSession):
    """get_session_id() raises — injected into _build_common_info."""

    def get_session_id(self) -> str:
        raise RuntimeError("session id unavailable")


class TestOtelRailCallbackHardening:
    """Callbacks must swallow their own failures: the rail dispatch layer
    records a raising before-callback as a business failure (retry history),
    so an OTel-side bug must never escape OtelRail."""

    async def test_all_callbacks_swallow_tracer_failures(self):
        session = _ExplodingTracerSession(_StubTracer())
        # Preset state the before hooks would have produced so the after
        # hooks get past their emptiness guards and reach session.tracer().
        session.agent_span = object()
        rail = OtelRail()
        agent = _StubAgent()
        model_ctx = _make_ctx(agent, session, ModelCallInputs(messages=["m"]))
        tool_ctx = _make_ctx(agent, session, ToolCallInputs(tool_name="echo"))

        await rail.before_invoke(_make_ctx(agent, session, InvokeInputs(query="q")))
        await rail.after_invoke(_make_ctx(agent, session, InvokeInputs(query="q")))
        await rail.before_model_call(model_ctx)
        rail._llm_spans.append(object())
        await rail.after_model_call(model_ctx)
        rail._llm_spans.append(object())
        await rail.on_model_exception(model_ctx)

        await rail.before_tool_call(tool_ctx)
        rail._tool_spans.append(object())
        await rail.after_tool_call(tool_ctx)
        rail._tool_spans.append(object())
        await rail.on_tool_exception(tool_ctx)

        # Reaching here means no callback let the failure escape.
        assert rail._llm_spans == []
        assert rail._tool_spans == []

    async def test_before_invoke_swallows_session_id_failure(self):
        """A raising get_session_id inside _build_common_info must not leak:
        the event simply never fires."""
        tracer = _StubTracer()
        session = _BrokenSessionIdSession(tracer)
        agent = _StubAgent(card=_StubCard(name="MyAgent", card_id="id-1"))
        rail = OtelRail()

        await rail.before_invoke(_make_ctx(agent, session, inputs={}))

        assert tracer.calls == []
        assert session.agent_span is None


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

            session = _StubSession(
                tracer, session_id="e2e-session", source_metadata={"user_id": "u-e2e", "channel": "e2e"}
            )
            config = _StubAgentConfig(
                ModelRequestConfig(
                    model="test-model",
                    temperature=0.3,
                    top_p=0.8,
                    max_tokens=512,
                    stop="END",
                    reasoning={"effort": "low"},
                )
            )
            agent = _StubAgent(
                config=config,
                card=_StubCard(name="E2EAgent", card_id="card-e2e", description="E2E test agent"),
            )
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
                    first_token_time="0.42",
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
            assert list(llm_span.attributes[GEN_AI_REQUEST_STOP_SEQUENCES]) == ["END"]
            assert llm_span.attributes[GEN_AI_REQUEST_REASONING_LEVEL] == "low"
            assert llm_span.attributes[OJ_LLM_PREV_MESSAGE_COUNT] == 2
            assert llm_span.attributes[GEN_AI_RESPONSE_FINISH_REASONS] == ("stop",)
            assert llm_span.attributes[GEN_AI_RESPONSE_MODEL] == "test-model"
            assert llm_span.attributes[GEN_AI_USAGE_INPUT_TOKENS] == 10
            assert llm_span.attributes[GEN_AI_USAGE_OUTPUT_TOKENS] == 20
            assert llm_span.attributes[OJ_GEN_AI_USAGE_TOTAL_COST] == pytest.approx(0.3)
            assert llm_span.attributes[GEN_AI_RESPONSE_TIME_TO_FIRST_CHUNK] == pytest.approx(0.42)
            assert llm_span.attributes[GEN_AI_PROVIDER_NAME] == "openjiuwen"
            assert llm_span.attributes[OJ_GEN_AI_TRACE_NAME] == "E2EAgent"
            # Identity / conversation attributes from _build_common_info
            assert llm_span.attributes[GEN_AI_AGENT_NAME] == "E2EAgent"
            assert llm_span.attributes[GEN_AI_AGENT_DESCRIPTION] == "E2E test agent"
            assert llm_span.attributes[GEN_AI_CONVERSATION_ID] == "e2e-session"
            assert llm_span.attributes[OJ_GEN_AI_USER_ID] == "u-e2e"
            assert json.loads(llm_span.attributes[OJ_GEN_AI_METADATA]) == {"user_id": "u-e2e", "channel": "e2e"}

            tool_span = by_name["execute_tool echo"]
            assert tool_span.attributes[GEN_AI_TOOL_NAME] == "echo"
            assert tool_span.attributes[GEN_AI_TOOL_TYPE] == "function"
            assert tool_span.attributes[GEN_AI_TOOL_CALL_ID] == "call-1"
            assert tool_span.attributes[GEN_AI_AGENT_NAME] == "E2EAgent"
            assert tool_span.attributes[GEN_AI_AGENT_DESCRIPTION] == "E2E test agent"
            assert tool_span.attributes[GEN_AI_CONVERSATION_ID] == "e2e-session"
            assert tool_span.attributes[OJ_GEN_AI_TRACE_NAME] == "E2EAgent"

            # LLM and tool spans share the agent root span's trace
            agent_span_names = [s.name for s in spans if s.name not in ("chat test-model", "execute_tool echo")]
            assert agent_span_names, "agent root span expected"
            root = spans[[s.name for s in spans].index(agent_span_names[0])]
            assert root.attributes[GEN_AI_AGENT_ID] == "card-e2e"
            assert root.attributes[GEN_AI_AGENT_NAME] == "E2EAgent"
            assert root.attributes[GEN_AI_CONVERSATION_ID] == "e2e-session"
            assert llm_span.context.trace_id == root.context.trace_id
            assert tool_span.context.trace_id == root.context.trace_id
        finally:
            TracerHandlerRegistry.clear()
            _EXPORTER.clear()
