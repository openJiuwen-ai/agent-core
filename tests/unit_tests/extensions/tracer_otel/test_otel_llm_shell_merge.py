# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""LLM-component-shell merge: one operation=chat span per real model call.

Hotfix twin of the develop-branch merge tests, adapted to this branch:
agent-handler LLM spans are named ``llm.<model>`` (not ``chat <model>``)
and 0.1.17 UsageMetadata has no ttft_seconds field, so the ttft reading
here consumes the traced-model decorator's usage snapshot (raw dict).

Contract pinned (mirrors the merge design):

- first on_llm_start under an unclaimed LLM shell MERGES: the shell IS the
  llm span — usage / ttft / response.model are appended (allowlist +
  respect_existing), the shell keeps every attribute it owns, and its
  lifecycle stays with on_call_done;
- a second call under the same shell (Questioner reflection is 1+N) opens
  a normal child span nested under the shell, keeping the count invariant:
  chat spans == real calls;
- a failed first call records an ``llm.error`` event and keeps the claim
  (the retry becomes a child span) — the shell is never error-stated or
  ended from the LLM side;
- non-LLM shells (tool / retrieval) never merge: inner LLM calls become
  child spans as before;
- ttft_seconds is read off the usage snapshot (this branch's addition to
  _set_llm_response_attrs), with first_token_time as the legacy fallback.
"""

import pytest

from openjiuwen.core.foundation.llm.schema.message import AssistantMessage, UsageMetadata
from openjiuwen.core.session.tracer.decorator import (
    _llm_call_info,
    _llm_end_payload,
    _llm_usage_snapshot,
)
from openjiuwen.core.session.tracer.span import SpanManager
from openjiuwen.extensions.tracer_otel.config import OtelTracerConfig
from openjiuwen.extensions.tracer_otel.handler import (
    OtelAgentHandler,
    OtelWorkflowHandler,
    _set_llm_response_attrs,
)
from openjiuwen.extensions.tracer_otel.semconv import (
    GEN_AI_OPERATION_NAME,
    GEN_AI_OUTPUT_MESSAGES,
    GEN_AI_REQUEST_MODEL,
    GEN_AI_RESPONSE_MODEL,
    GEN_AI_RESPONSE_TIME_TO_FIRST_CHUNK,
    GEN_AI_USAGE_INPUT_TOKENS,
    GEN_AI_USAGE_OUTPUT_TOKENS,
    OJ_AGENT_ERROR_MESSAGE,
    OJ_WORKFLOW_COMPONENT_ID,
    OJ_WORKFLOW_OUTPUTS,
)

from tests.conftest_otel import _EXPORTER, _OTEL_TRACER


def _usage(**overrides) -> UsageMetadata:
    params = dict(model_name="qwen-max", input_tokens=10, output_tokens=5)
    params.update(overrides)
    return UsageMetadata(**params)


def _assistant(**usage_overrides) -> AssistantMessage:
    return AssistantMessage(content="ok", usage_metadata=_usage(**usage_overrides))


def _decorator_end_payload(result, ttft=None, preset_ttft=None):
    """Payload exactly as the traced-model decorator builds it. ``preset_ttft``
    simulates a producer-stamped ttft already present in the snapshot."""
    usage = _llm_usage_snapshot(result)
    if usage is not None and preset_ttft is not None:
        usage["ttft_seconds"] = preset_ttft
    return _llm_end_payload(result, ttft, usage=usage)


class _MergeScenario:
    """Workflow shell + agent handler sharing one OTel tracer/exporter."""

    def __init__(self) -> None:
        self.config = OtelTracerConfig(redaction_enabled=False)
        self.workflow = OtelWorkflowHandler(_OTEL_TRACER, self.config)
        self.agent = OtelAgentHandler(_OTEL_TRACER, self.config)
        self.spans = SpanManager("merge-test-trace")

    async def start_llm_shell(self, invoke_id: str, model_name: str = "qwen-max") -> None:
        await self.workflow.on_call_start(
            invoke_id,
            metadata={
                "workflow_id": "wf-1",
                "component_id": f"comp-{invoke_id}",
                "component_type": "LLMComponent",
                "component_name": "generator",
                "model_name": model_name,
            },
            inputs={"prompt": "hi"},
        )

    async def llm_call(self, *, usage_overrides=None, error=None) -> None:
        span = self.spans.create_agent_span()
        await self.agent.on_llm_start(
            span=span,
            inputs={"inputs": [{"role": "user", "content": "hi"}]},
            instance_info={"class_name": "qwen-max", "type": "llm"},
        )
        if error is not None:
            await self.agent.on_llm_error(span=span, error=error)
            return
        result = _assistant(**(usage_overrides or {}))
        await self.agent.on_llm_end(span=span, outputs=_decorator_end_payload(result, ttft=0.42))


@pytest.fixture(autouse=True)
def _clean_exporter():
    _EXPORTER.clear()
    yield
    _EXPORTER.clear()


def _finished():
    return _EXPORTER.get_finished_spans()


def _shell(spans):
    return next(s for s in spans if OJ_WORKFLOW_COMPONENT_ID in (s.attributes or {}))


def _children(spans):
    return [s for s in spans if OJ_WORKFLOW_COMPONENT_ID not in (s.attributes or {})]


@pytest.mark.asyncio
class TestFirstCallMergesIntoShell:
    async def test_single_span_carries_usage_and_ttft(self):
        scenario = _MergeScenario()
        await scenario.start_llm_shell("node-1")
        await scenario.llm_call()
        await scenario.workflow.on_call_done("node-1", outputs={"answer": "ok"})

        finished = _finished()
        assert len(finished) == 1, "merge must not add a second chat span"
        shell = finished[0]
        # This branch names component shells "component.<invoke_id>"; the
        # LLM identity rides on gen_ai.operation.name == "chat".
        assert shell.name == "component.node-1"
        # Shell-owned attributes keep their own writer's values.
        assert shell.attributes[GEN_AI_REQUEST_MODEL] == "qwen-max"
        assert shell.attributes[GEN_AI_OPERATION_NAME] == "chat"
        assert OJ_WORKFLOW_OUTPUTS in shell.attributes
        # Appended call-level truths, ttft_seconds read off the snapshot.
        assert shell.attributes[GEN_AI_RESPONSE_MODEL] == "qwen-max"
        assert shell.attributes[GEN_AI_USAGE_INPUT_TOKENS] == 10
        assert shell.attributes[GEN_AI_USAGE_OUTPUT_TOKENS] == 5
        assert shell.attributes[GEN_AI_RESPONSE_TIME_TO_FIRST_CHUNK] == pytest.approx(0.42)
        # Excluded by design: content already lives in workflow outputs.
        assert GEN_AI_OUTPUT_MESSAGES not in shell.attributes

    async def test_failed_call_records_event_and_shell_survives(self):
        scenario = _MergeScenario()
        await scenario.start_llm_shell("node-1")
        await scenario.llm_call(error=RuntimeError("boom"))

        assert _finished() == [], "LLM error must not end the shell span"
        # Retry after the failure: claim held -> normal child span, and the
        # shell still finishes cleanly with its own lifecycle.
        await scenario.llm_call()
        await scenario.workflow.on_call_done("node-1", outputs={"answer": "ok"})

        finished = _finished()
        assert len(finished) == 2  # shell + retry child
        shell = _shell(finished)
        event_names = [event.name for event in shell.events]
        assert "llm.error" in event_names
        assert OJ_AGENT_ERROR_MESSAGE not in (shell.attributes or {})
        assert shell.status.status_code.name != "ERROR"

    async def test_request_event_lands_on_claimed_shell(self):
        """Merge mode keeps llm.request parity: the mid-call request event
        is appended to the claimed shell instead of being dropped."""
        scenario = _MergeScenario()
        await scenario.start_llm_shell("node-1")
        span = scenario.spans.create_agent_span()
        await scenario.agent.on_llm_start(
            span=span,
            inputs={"inputs": [{"role": "user", "content": "hi"}]},
            instance_info={"class_name": "qwen-max", "type": "llm"},
        )
        await scenario.agent.on_llm_request(span=span, llm_params="stop=END&temperature=0.3")
        await scenario.agent.on_llm_end(span=span, outputs=_decorator_end_payload(_assistant(), ttft=0.42))
        await scenario.workflow.on_call_done("node-1", outputs={"answer": "ok"})

        finished = _finished()
        assert len(finished) == 1  # request event must not spawn a second span
        shell = _shell(finished)
        request_events = [e for e in shell.events if e.name == "llm.request"]
        assert len(request_events) == 1
        assert (request_events[0].attributes or {}).get("llm_params") == "stop=END&temperature=0.3"


@pytest.mark.asyncio
class TestSecondCallUnderClaimedShell:
    async def test_becomes_child_span_nested_under_shell(self):
        scenario = _MergeScenario()
        await scenario.start_llm_shell("node-1")
        await scenario.llm_call(usage_overrides={"output_tokens": 5})
        await scenario.llm_call(usage_overrides={"output_tokens": 7})
        await scenario.workflow.on_call_done("node-1", outputs={"answer": "ok"})

        finished = _finished()
        assert len(finished) == 2, "two real calls == shell + one child"
        shell = _shell(finished)
        children = _children(finished)
        assert len(children) == 1
        child = children[0]
        assert child.name == "llm.qwen-max"
        assert child.parent.span_id == shell.context.span_id
        # Invariant: first call's usage stays on the shell (append-only),
        # the second call's usage lives on its own child span.
        assert shell.attributes[GEN_AI_USAGE_OUTPUT_TOKENS] == 5
        assert child.attributes[GEN_AI_USAGE_OUTPUT_TOKENS] == 7

    async def test_new_component_execution_claims_fresh_shell(self):
        scenario = _MergeScenario()
        await scenario.start_llm_shell("node-1")
        await scenario.llm_call()
        await scenario.workflow.on_call_done("node-1", outputs={"answer": "ok"})

        await scenario.start_llm_shell("node-2")
        await scenario.llm_call()
        await scenario.workflow.on_call_done("node-2", outputs={"answer": "ok"})

        finished = _finished()
        assert len(finished) == 2, "ended shells are pruned; each node merges again"
        for span in finished:
            assert OJ_WORKFLOW_COMPONENT_ID in span.attributes
            assert span.attributes[GEN_AI_RESPONSE_MODEL] == "qwen-max"


@pytest.mark.asyncio
class TestNonLlmShellNeverMerges:
    async def test_tool_shell_gets_child_span_without_merge(self):
        scenario = _MergeScenario()
        await scenario.workflow.on_call_start(
            "tool-node",
            metadata={
                "workflow_id": "wf-1",
                "component_id": "comp-tool",
                "component_type": "ToolExecutable",
                "component_name": "get_weather",
            },
            inputs={"city": "Beijing"},
        )
        await scenario.llm_call()
        await scenario.workflow.on_call_done("tool-node", outputs="22C")

        finished = _finished()
        assert len(finished) == 2
        tool_shell = _shell(finished)
        # This branch names plain/tool components "component.<invoke_id>"
        # and tags them via operation.name instead.
        assert tool_shell.name == "component.tool-node"
        assert tool_shell.attributes[GEN_AI_OPERATION_NAME] == "execute_tool"
        # Tool shell keeps no usage; the llm span nested under it.
        child = _children(finished)[0]
        assert child.name == "llm.qwen-max"
        assert child.parent.span_id == tool_shell.context.span_id
        assert GEN_AI_USAGE_OUTPUT_TOKENS in child.attributes
        assert GEN_AI_USAGE_OUTPUT_TOKENS not in (tool_shell.attributes or {})


class TestResponseAttrs:
    def test_sibling_usage_fallback_for_stream_shape(self):
        """{"outputs": [chunks], "usage_metadata": {...}} used to lose usage
        (unwrapped payload is a list). The sibling fallback restores it."""
        scenario = _MergeScenario()
        otel_span = scenario.agent._otel_tracer.start_span("probe")
        chunks = [AssistantMessage(content="a"), AssistantMessage(content="b")]
        outputs = {"outputs": chunks}
        _set_llm_response_attrs(otel_span, outputs)
        assert GEN_AI_USAGE_INPUT_TOKENS not in otel_span.attributes

        sibling = dict(_usage().model_dump(), ttft_seconds=0.3)
        _set_llm_response_attrs(otel_span, {"outputs": chunks, "usage_metadata": sibling})
        assert otel_span.attributes[GEN_AI_USAGE_INPUT_TOKENS] == 10
        assert otel_span.attributes[GEN_AI_RESPONSE_TIME_TO_FIRST_CHUNK] == pytest.approx(0.3)
        otel_span.end()

    def test_first_token_time_remains_legacy_fallback(self):
        scenario = _MergeScenario()
        otel_span = scenario.agent._otel_tracer.start_span("probe")
        sibling = dict(_usage().model_dump(), first_token_time="0.77")
        _set_llm_response_attrs(otel_span, {"outputs": [AssistantMessage(content="a")],
                                            "usage_metadata": sibling})
        assert otel_span.attributes[GEN_AI_RESPONSE_TIME_TO_FIRST_CHUNK] == pytest.approx(0.77)
        otel_span.end()

    def test_respect_existing_appends_only(self):
        scenario = _MergeScenario()
        otel_span = scenario.agent._otel_tracer.start_span("probe")
        otel_span.set_attribute(GEN_AI_RESPONSE_MODEL, "configured-name")
        _set_llm_response_attrs(
            otel_span,
            {"outputs": _assistant()},
            respect_existing=True,
        )
        assert otel_span.attributes[GEN_AI_RESPONSE_MODEL] == "configured-name"
        assert otel_span.attributes[GEN_AI_USAGE_INPUT_TOKENS] == 10
        otel_span.end()


class TestDecoratorPayload:
    def test_invoke_payload_gets_ttft_sibling(self):
        result = _assistant()
        payload = _decorator_end_payload(result, ttft=1.5)
        assert set(payload) == {"outputs", "usage_metadata"}
        assert payload["outputs"] is result
        assert payload["usage_metadata"]["ttft_seconds"] == pytest.approx(1.5)
        assert payload["usage_metadata"]["model_name"] == "qwen-max"

    def test_producer_stamped_ttft_not_overwritten(self):
        result = _assistant()
        payload = _decorator_end_payload(result, ttft=1.5, preset_ttft=0.1)
        assert payload["usage_metadata"]["ttft_seconds"] == pytest.approx(0.1)

    def test_no_usage_no_sibling(self):
        result = AssistantMessage(content="plain")
        assert _decorator_end_payload(result, ttft=1.0) == {"outputs": result}

    def test_usage_snapshot_is_copy_and_duck_typed(self):
        result = _assistant()
        snapshot = _llm_usage_snapshot(result)
        assert snapshot is not result.usage_metadata
        snapshot["input_tokens"] = 999
        assert result.usage_metadata.input_tokens == 10
        assert _llm_usage_snapshot("plain string") is None

    def test_call_info_maps_params_and_message_count(self):
        info = _llm_call_info(
            {"class_name": "m", "type": "llm"},
            [{"role": "user", "content": "hi"}, {"role": "user", "content": "again"}],
            {"temperature": 0.2, "top_p": None, "stop": "END", "max_tokens": 512},
        )
        assert info["class_name"] == "m"
        # stop_sequences keeps the OtelRail shape contract: always a str list,
        # even when the caller passed a single stop string.
        assert info["request_params"] == {"temperature": 0.2, "max_tokens": 512, "stop_sequences": ["END"]}
        assert info["message_count"] == 2
        # Non-list prompts carry no invented count.
        assert "message_count" not in _llm_call_info({"class_name": "m"}, "one prompt", {})
        # A list-valued stop passes through normalized to str elements.
        list_info = _llm_call_info({"class_name": "m"}, [], {"stop": ["END", "STOP"]})
        assert list_info["request_params"]["stop_sequences"] == ["END", "STOP"]


def test_capability_marker_probeable():
    """agent-studio probes getattr on the class / an instance; absence means
    an old core without merge support."""
    assert OtelAgentHandler.LLM_SPAN_MERGE_CAPABILITY == "1"
    assert getattr(OtelAgentHandler(_OTEL_TRACER, OtelTracerConfig()), "LLM_SPAN_MERGE_CAPABILITY") == "1"
