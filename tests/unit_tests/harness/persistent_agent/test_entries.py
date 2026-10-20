# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Execution, resume and abort entries with a real DeepAgent, a local model and seam doubles.

The session state store, window assembler and reply adapter doubles stand in for
PA-10 / PA-06 / PA-09 so the orchestration can be verified; they never replace the
formal implementations. Tests using the real_state fixture run the formal PA-10 store and
PA-06 assembler over an in-memory window backend shared by the Executions of one test.
"""

import asyncio
import hashlib
import json
from dataclasses import replace
from datetime import datetime, timezone
from importlib import import_module
from types import SimpleNamespace

import pytest

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import BaseError
from openjiuwen.core.foundation.llm.schema.config import ModelClientConfig, ModelRequestConfig
from openjiuwen.core.foundation.llm.schema.message import AssistantMessage, ToolMessage, UsageMetadata, UserMessage
from openjiuwen.core.foundation.llm.schema.message_chunk import AssistantMessageChunk
from openjiuwen.core.foundation.llm.schema.tool_call import ToolCall
from openjiuwen.core.foundation.store import InMemoryKVStore
from openjiuwen.core.foundation.tool import Tool, ToolCard
from openjiuwen.core.runner.callback.errors import AbortError
from openjiuwen.core.session.checkpointer import CheckpointerFactory, NoopCheckpointer
from openjiuwen.core.session.checkpointer.inmemory import InMemoryCheckpointer
from openjiuwen.core.single_agent import AgentCard, Session, create_agent_session
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext, ToolCallInputs
from openjiuwen.harness.deep_agent import DeepAgent
from openjiuwen.harness.persistent_agent.workspace.access import WorkspaceReadScope
from openjiuwen.harness.rails.base import DeepAgentRail
from openjiuwen.harness.rails.interrupt.confirm_rail import ConfirmInterruptRail, ConfirmPayload
from openjiuwen.harness.schema.deep_agent_spec import (
    _RAIL_PROVIDER_REGISTRY,
    _TOOL_PROVIDER_REGISTRY,
    BuiltinToolSpec,
    ModelSpec,
    RailSpec,
)

pytestmark = pytest.mark.level0

PKG = "openjiuwen.harness.persistent_agent"
STATE_KEYS = ("context", "__react_agent_interruption__", "__interrupt_auto_confirm__", "deepagent")
TOOL_ARGS = '{"value": "x", "api_token": "secret-value"}'


def module(name):
    return import_module(f"{PKG}.{name}")


SEAMS = module("execution.seams")
REAL_STATE_STORE, REAL_ASSEMBLER = SEAMS.session_state_store, SEAMS.window_assembler
UNINTEGRATED_ADAPTER = SEAMS.native_reply_adapter


def kinds(events):
    return [str(event.event_type) for event in events]


def emoji_text(count):
    """Four-byte characters whose period (251) is longer than any repeat the anomaly rail detects."""
    return "".join(chr(0x1F300 + (index * 37) % 251) for index in range(count))


def assert_within_limits(events):
    limits = module("schema.events").EVENT_MAX_BYTES, module("schema.protocol").TEXT_MAX_CHARS
    for event in events:
        assert event.proposal_size() <= limits[0]
        assert len(getattr(event.payload, "text", None) or "") <= limits[1]


def usage(input_tokens, output_tokens):
    return UsageMetadata(input_tokens=input_tokens, output_tokens=output_tokens)


class PermanentModelError(RuntimeError):
    """A provider rejects the request, so a terminal-error test cannot recover through a retry."""

    status_code = 400


class LocalModel:
    """Streams a queued answer, tool call, permanent "fail", or "transient_fail" per call.

    A (tool name, arguments) pair emits one call of that tool; an empty queue returns an answer.
    """

    model_client_config = None
    model_config = None

    def __init__(self):
        self.turns = []
        self.calls = 0
        self.judgments = []
        self.judge_calls = 0
        self.judge_messages = []
        self.judge_kwargs = []
        self.tool_name = "remember"
        self.answer = "answer"
        self.with_usage = True
        self.release = asyncio.Event()
        self.release.set()
        self.streaming = asyncio.Event()
        self.cancelled = asyncio.Event()
        self.on_call = None
        self.seen = []

    async def invoke(self, messages, **kwargs):
        """Participation judgment only. Native execution always streams."""
        self.judge_calls += 1
        self.judge_messages.append(messages)
        self.judge_kwargs.append(kwargs)
        if not self.judgments:
            raise AssertionError("entries always stream the native agent")
        item = self.judgments.pop(0)
        if isinstance(item, BaseException):
            raise item
        return AssistantMessage(content=item, usage_metadata=usage(2, 3))

    async def stream(self, messages, **kwargs):
        self.calls += 1
        self.seen.append([getattr(message, "content", None) for message in messages])
        if self.on_call is not None:
            self.on_call()
        turn = self.turns.pop(0) if self.turns else "answer"
        if turn == "fail":
            raise PermanentModelError("local model failure")
        if turn == "transient_fail":
            raise RuntimeError("local model failure")
        if isinstance(turn, tuple):
            name, arguments = turn
            calls = [ToolCall(id=f"call-{self.calls}", type="function", name=name, arguments=arguments)]
            yield AssistantMessageChunk(content="checking ", tool_calls=calls, usage_metadata=usage(3, 4))
            return
        if turn in ("tool", "two_tools"):
            ids = [f"call-{self.calls}"] if turn == "tool" else ["call-a", "call-b"]
            calls = [ToolCall(id=tool_id, type="function", name=self.tool_name, arguments=TOOL_ARGS) for tool_id in ids]
            yield AssistantMessageChunk(content="checking ", tool_calls=calls, usage_metadata=usage(3, 4))
            return
        try:
            yield AssistantMessageChunk(content="local ")
            self.streaming.set()
            await self.release.wait()
            yield AssistantMessageChunk(
                content=self.answer, finish_reason="stop", usage_metadata=usage(5, 6) if self.with_usage else None
            )
        except asyncio.CancelledError:
            self.cancelled.set()
            raise


class Remember(Tool):
    """Not parallel-safe unless a test says so, so several calls in one turn run one after another."""

    def __init__(self, calls, hooks, name="remember"):
        super().__init__(
            card=ToolCard(
                id=name,
                name=name,
                input_params={"type": "object", "properties": {"value": {"type": "string"}}},
                parallel_safe=hooks.get("parallel_safe", False),
                idempotent=hooks.get("idempotent", False),
            )
        )
        self.calls = calls
        self.hooks = hooks

    async def invoke(self, inputs, **kwargs):
        self.calls.append(inputs)
        if self.hooks.get("on_invoke") is not None:
            self.hooks["on_invoke"]()
        if self.hooks.get("gate") is not None and len(self.calls) == self.hooks.get("gate_call", 1):
            # Holds this call in flight so a control can arrive mid-batch.
            self.hooks["gate_reached"].set()
            await self.hooks["gate"].wait()
        if self.hooks.get("fail_once"):
            self.hooks["fail_once"] = False
            raise TimeoutError("temporary tool timeout")
        return {"remembered": inputs.get("value")}

    async def stream(self, inputs, **kwargs):
        yield await self.invoke(inputs, **kwargs)


class Host:
    """Commit and budget ports; `log` interleaves recorded events with state saves."""

    def __init__(self, log):
        self.log = log
        self.events = []
        self.reports = []
        self.fail_record = set()
        self.fail_once = set()
        self.admission = "admitted"
        self.admit_error = None
        self.report_error = None

    async def record(self, execution, event):
        if event.event_type in self.fail_record:
            raise RuntimeError("commit unavailable")
        if event.event_type in self.fail_once:
            self.fail_once.discard(event.event_type)
            raise RuntimeError("commit briefly unavailable")
        self.events.append(event)
        self.log.append(str(event.event_type))

    async def read_execution_log(self, *args):
        return ()

    def _ref(self, execution, model_call_id):
        ref = module("schema.protocol").InvocationRef.from_execution(execution).model_dump()
        return {**ref, "model_call_id": model_call_id}

    async def admit(self, execution, model_call_id):
        if self.admit_error is not None:
            raise self.admit_error
        if self.admission == "denied":
            return {**self._ref(execution, model_call_id), "status": "denied", "reason": "quota exhausted"}
        return {**self._ref(execution, model_call_id), "status": "admitted", "reservation_ref": "res-1"}

    async def report(self, execution, model_call_id, model_usage):
        self.reports.append(model_usage)
        if self.report_error is not None:
            raise self.report_error
        return {**self._ref(execution, model_call_id), "status": "confirmed", "receipt_ref": "rcpt-1"}


class StateStore:
    """PA-10 double: carries the whitelisted Session state, waiting scene and cursor between Executions."""

    def __init__(self, log):
        self.log = log
        self.record = None
        self.endings = []
        self.loaded_marker = "never loaded"
        self.load_error = None
        self.save_error = None

    async def load(self, context):
        if self.load_error is not None:
            raise self.load_error
        self.loaded_marker = context.session.get_state("marker")
        if self.record is None:
            return SEAMS.LoadedRecord()
        context.session.update_state(dict(self.record["state"]))
        ending = self.record["ending"]
        waiting = None
        if ending.outcome == "awaiting_confirmation":
            waiting = SEAMS.WaitingScene(ending.request_id, ending.reply_target)
        return SEAMS.LoadedRecord(waiting, ending.last_group_message_id)

    async def save(self, context, ending):
        if self.save_error is not None:
            raise self.save_error
        state = context.session.get_state()
        kept = {key: state[key] for key in STATE_KEYS if state.get(key) is not None}
        self.record = {"state": kept, "ending": ending}
        self.endings.append(ending)
        self.log.append("save")


class WindowAssembler:
    """PA-06 double: passes the task text through and reports a fixed group-message cursor."""

    def __init__(self):
        self.calls = []
        self.cursor = "gm-1"
        self.error = None

    async def assemble(self, context, request, record):
        self.calls.append((request.text, record))
        if self.error is not None:
            raise self.error
        return SEAMS.WindowAssembly({"query": request.text}, last_group_message_id=self.cursor)


class ReplyAdapter:
    """PA-09 double for permission replies only."""

    def to_native(self, response, request):
        return ConfirmPayload(approved=response.choice != "reject", feedback=response.feedback or "")


class RewriteAnswer(DeepAgentRail):
    """Replaces the final result in after_invoke, as an outer rail may."""

    def __init__(self, rewrite):
        super().__init__()
        self.rewrite = rewrite

    async def after_invoke(self, ctx) -> None:
        if self.rewrite.get("output") is not None:
            ctx.inputs.result = {"output": self.rewrite["output"], "result_type": "answer"}


class Harness:
    def __init__(self, api, model, store, tool_calls):
        self.api = api
        self.model = model
        self.store = store
        self.tool_calls = tool_calls
        self.log = store.log
        self.tool_hooks = {}
        self.rewrite = {}
        self.assembler = WindowAssembler()
        # Shared by every context of a test once set; each context gets its own backend otherwise.
        self.backend = None

    def spec(self, confirm=False, rewrite=False, **overrides):
        fields = dict(
            card=AgentCard(id="business-agent", name="test"),
            model=ModelSpec(
                model_client_config=ModelClientConfig(
                    client_provider="OpenAI", api_key="mock-api-key", api_base="https://example.invalid/v1"
                ),
                model_request_config=ModelRequestConfig(context_window=1_000_000, max_tokens=4_000),
            ),
            enable_sys_operation=False,
            enable_task_loop=False,
            enable_security_rail=False,
            enable_tool_resilience_rail=False,
            auto_create_workspace=False,
            tools=[BuiltinToolSpec(type="test.entries.remember")],
        )
        rails = [RailSpec(type="test.entries.confirm")] if confirm else []
        if rewrite:
            rails.append(RailSpec(type="test.entries.rewrite"))
        if rails:
            fields["rails"] = rails
        fields.update(overrides)
        return self.api.PersistentAgentSpec(**fields)

    def context(
        self,
        spec,
        *,
        execution_id="execution-1",
        request_id="request-1",
        isolated=True,
        group_chat=None,
        workspace_access=None,
        **changes,
    ):
        execution = self.api.ExecutionContext(
            namespace_id="tenant",
            agent_id=spec.card.id,
            session_id="session-a",
            request_id=request_id,
            execution_id=execution_id,
            config_version="v1",
            config_fingerprint=spec.fingerprint(),
            source_ref="source:message-1",
            principal_ref="user",
            startup_scope=frozenset({"read"}),
            source_scope=frozenset({"read"}),
            policy_revision="p1",
            capability_snapshot=spec.enabled_features,
        )
        checkpointer = NoopCheckpointer() if isolated else None
        host = Host(self.log)
        ctx = self.api.PersistentBuildContext(
            execution=replace(execution, **changes),
            session=create_agent_session(
                session_id=changes.get("session_id", "session-a"), card=spec.card, checkpointer=checkpointer
            ),
            ports=self.api.PersistentPorts(
                commit=host,
                budget=host,
                window_backend=self.backend or InMemoryKVStore(),
                group_chat=group_chat,
                workspace_access=workspace_access,
            ),
        )
        return ctx, host


def request(**overrides):
    return {"kind": "scheduled", "text": "please summarize", "reply_target": "chat-1", **overrides}


def permission(tool_call_id="call-1", choice="approve"):
    return {"responses": [{"kind": "permission", "tool_call_id": tool_call_id, "choice": choice}]}


async def collect(stream):
    return [event async for event in stream]


@pytest.fixture
def harness(monkeypatch, capacity_counter):
    api = import_module(PKG)
    model, tool_calls = LocalModel(), []
    store = StateStore([])
    harness = Harness(api, model, store, tool_calls)
    monkeypatch.setattr(ModelSpec, "build", lambda self: model)
    monkeypatch.setitem(
        _TOOL_PROVIDER_REGISTRY,
        "test.entries.remember",
        lambda params, context: Remember(tool_calls, harness.tool_hooks, name=model.tool_name),
    )
    monkeypatch.setitem(
        _RAIL_PROVIDER_REGISTRY, "test.entries.confirm", lambda params, context: ConfirmInterruptRail(["remember"])
    )
    monkeypatch.setitem(
        _RAIL_PROVIDER_REGISTRY, "test.entries.rewrite", lambda params, context: RewriteAnswer(harness.rewrite)
    )
    monkeypatch.setattr(SEAMS, "session_state_store", lambda context: store)
    monkeypatch.setattr(SEAMS, "window_assembler", lambda context, **_: harness.assembler)
    monkeypatch.setattr(SEAMS, "native_reply_adapter", ReplyAdapter)
    return harness


def assert_rejected(exc_info, code):
    assert isinstance(exc_info.value, BaseError)
    assert exc_info.value.status == code


@pytest.mark.asyncio
async def test_completed_execution_records_events_in_order(harness):
    spec = harness.spec()
    ctx, host = harness.context(spec)
    events = await collect(harness.api.execute_persistent_request(spec, ctx, request()))
    assert kinds(events) == [
        "execution.started",
        "reply.delta",
        "reply.delta",
        "model.completed",
        "output.proposed",
        "execution.final",
    ]
    assert events == host.events, "the host sees exactly the recorded events, in record order"
    assert [event.local_seq for event in events] == list(range(1, 7))
    started, output, final = events[0], events[4], events[5]
    assert started.payload.goal == "please summarize"
    assert events[3].payload.usage.input_tokens == 5 and events[3].payload.config_version == "v1"
    assert output.visibility == "candidate"
    assert (output.payload.text, output.payload.target_ref, output.payload.share_mode) == (
        "local answer",
        "chat-1",
        "message",
    )
    assert final.payload.status == "completed" and final.payload.output_refs == (output.payload.output_id,)
    assert harness.log.index("save") < harness.log.index("execution.final"), "state is saved before the ending"
    assert harness.store.endings[-1].outcome == "completed"
    assert harness.store.endings[-1].output_refs == (output.payload.output_id,)
    assert ctx.binding.runtime.closed


@pytest.mark.asyncio
async def test_window_assembly_runs_after_load_and_feeds_the_native_call(harness):
    spec = harness.spec()
    harness.store.record = {"state": {}, "ending": SEAMS.ExecutionEnding("completed", "request-0", "chat-1")}
    ctx, _ = harness.context(spec)
    harness.assembler.cursor = "gm-9"
    events = await collect(harness.api.execute_persistent_request(spec, ctx, request()))
    ((text, record),) = harness.assembler.calls
    assert text == "please summarize" and record == SEAMS.LoadedRecord(None, None)
    assert events[-1].payload.status == "completed"
    assert harness.store.endings[-1].last_group_message_id == "gm-9", "the ending keeps the new cursor"


@pytest.mark.asyncio
async def test_resume_keeps_the_previous_cursor_without_assembling(harness):
    spec = harness.spec(confirm=True)
    harness.assembler.cursor = "gm-3"
    await pause_for_confirmation(harness, spec)
    assert harness.store.endings[-1].last_group_message_id == "gm-3"
    ctx, _ = harness.context(spec, execution_id="execution-2")
    events = await collect(harness.api.resume_persistent_request(spec, ctx, permission()))
    assert events[-1].payload.status == "completed"
    assert len(harness.assembler.calls) == 1, "a confirmation resume appends nothing to the window"
    assert harness.store.endings[-1].last_group_message_id == "gm-3"


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["assembly_error", "not_a_record"])
async def test_assembly_and_load_contract_failures_are_runtime_errors(harness, monkeypatch, failure):
    spec = harness.spec()
    ctx, host = harness.context(spec)
    if failure == "assembly_error":
        harness.assembler.error = RuntimeError("group chat unavailable")
    else:

        async def no_record(context):
            return None

        monkeypatch.setattr(harness.store, "load", no_record)
    with pytest.raises(BaseError) as exc_info:
        await collect(harness.api.execute_persistent_request(spec, ctx, request()))
    assert_rejected(exc_info, StatusCode.DEEPAGENT_RUNTIME_ERROR)
    assert harness.model.calls == 0
    assert kinds(host.events) == ["execution.started"]
    assert harness.store.endings == []


@pytest.mark.asyncio
async def test_budget_denial_skips_the_model_call(harness):
    spec = harness.spec()
    ctx, host = harness.context(spec)
    host.admission = "denied"
    events = await collect(harness.api.execute_persistent_request(spec, ctx, request()))
    assert harness.model.calls == 0
    assert kinds(events) == ["execution.started", "execution.paused"]
    assert (events[-1].payload.reason, events[-1].payload.detail) == ("budget_denied", "quota exhausted")
    assert harness.store.endings[-1].outcome == "budget_denied"


@pytest.mark.asyncio
async def test_admission_failure_pauses_with_error(harness):
    spec = harness.spec()
    ctx, host = harness.context(spec)
    host.admit_error = RuntimeError("budget service down")
    events = await collect(harness.api.execute_persistent_request(spec, ctx, request()))
    assert harness.model.calls == 0
    assert events[-1].payload.reason == "error"
    assert "budget service down" in events[-1].payload.detail


@pytest.mark.asyncio
async def test_usage_report_failure_is_only_diagnosed(harness):
    spec = harness.spec()
    ctx, host = harness.context(spec)
    host.report_error = RuntimeError("ledger down")
    harness.model.with_usage = False
    events = await collect(harness.api.execute_persistent_request(spec, ctx, request()))
    assert events[-1].payload.status == "completed"
    completed = next(event for event in events if event.event_type == "model.completed")
    assert completed.payload.usage.status == "unknown" and completed.payload.usage.input_tokens is None
    assert host.reports == [completed.payload.usage]


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["model_exception", "max_iterations"])
async def test_native_failures_pause_without_output(harness, failure):
    if failure == "model_exception":
        spec = harness.spec()
        harness.model.turns = ["fail"]
    else:
        spec = harness.spec(max_iterations=1)
        harness.model.turns = ["tool", "tool"]
    ctx, _ = harness.context(spec)
    events = await collect(harness.api.execute_persistent_request(spec, ctx, request()))
    assert "output.proposed" not in kinds(events), "error text is never handed off as an answer"
    assert events[-1].event_type == "execution.paused"
    assert events[-1].payload.reason == "error" and events[-1].payload.detail
    assert harness.store.endings[-1].outcome == "error"


@pytest.mark.asyncio
@pytest.mark.parametrize("recovers", [True, False])
async def test_native_transient_failures_keep_retries_and_account_for_each_call(harness, monkeypatch, recovers):
    """The v0.1.20 native retry remains active; only an exhausted failure pauses without an answer."""
    from openjiuwen.harness.rails.model_anomaly_detection_rail import ModelAnomalyDetectionRail

    original_init = ModelAnomalyDetectionRail.__init__

    def without_delay(self, *args, **kwargs):
        original_init(self, *args, **kwargs)
        self.transient_base_delay_seconds = 0

    monkeypatch.setattr(ModelAnomalyDetectionRail, "__init__", without_delay)
    spec = harness.spec()
    ctx, host = harness.context(spec)
    harness.model.turns = ["transient_fail"] if recovers else ["transient_fail"] * 4
    events = await collect(harness.api.execute_persistent_request(spec, ctx, request()))
    calls = 2 if recovers else 4
    assert harness.model.calls == calls
    completed = [event for event in events if event.event_type == "model.completed"]
    assert len(completed) == len(host.reports) == calls
    assert len({event.payload.model_call_id for event in completed}) == calls
    assert all(event.payload.usage.status == "unknown" for event in completed[: calls - 1])
    if recovers:
        assert events[-1].payload.status == "completed"
        assert len(outputs(events)) == 1
        assert completed[-1].payload.usage.status == "known"
    else:
        assert events[-1].event_type == "execution.paused"
        assert events[-1].payload.reason == "error" and outputs(events) == []
        assert completed[-1].payload.usage.status == "unknown"


def outputs(events):
    return [event for event in events if event.event_type == "output.proposed"]


@pytest.mark.asyncio
@pytest.mark.parametrize("limit", ["characters", "event_bytes"])
async def test_oversized_answer_is_handed_off_in_ordered_segments(harness, limit):
    """PA-08-V-09: the answer is never cut; its segments join back to it and the run completes."""
    spec = harness.spec()
    ctx, host = harness.context(spec)
    if limit == "characters":
        harness.model.answer = "".join(f"{index:06d}" for index in range(11000))
    else:
        # Within the character limit, but four bytes per character exceed the whole-event byte limit.
        harness.model.answer = emoji_text(65500)
    answer = "local " + harness.model.answer
    events = await collect(harness.api.execute_persistent_request(spec, ctx, request()))
    deltas = [event for event in events if event.event_type == "reply.delta"]
    assert "".join(event.payload.text for event in deltas) == answer, "display is split, never cut"
    assert_within_limits(events)
    segments = outputs(events)
    assert len(segments) >= 2 and "".join(event.payload.text for event in segments) == answer
    assert len({event.payload.output_id for event in segments}) == len(segments)
    final = events[-1]
    assert final.payload.status == "completed"
    assert final.payload.output_refs == tuple(event.payload.output_id for event in segments), "delivery order"
    ending = harness.store.endings[-1]
    assert (ending.outcome, ending.answer_segments, ending.answer_text) == ("completed", len(segments), answer)
    assert harness.log.index("save") < harness.log.index("execution.final")
    assert [event.local_seq for event in host.events] == list(range(1, len(host.events) + 1))


@pytest.mark.asyncio
@pytest.mark.parametrize("rewritten", ["short rewritten answer", "oversized"])
async def test_answer_rewritten_in_after_invoke_is_the_one_handed_off(harness, rewritten):
    spec = harness.spec(rewrite=True)
    ctx, _ = harness.context(spec)
    harness.rewrite["output"] = emoji_text(65500) if rewritten == "oversized" else rewritten
    events = await collect(harness.api.execute_persistent_request(spec, ctx, request()))
    assert "".join(event.payload.text for event in outputs(events)) == harness.rewrite["output"]
    assert events[-1].payload.status == "completed"
    assert harness.store.endings[-1].answer_text == harness.rewrite["output"]


@pytest.mark.asyncio
async def test_state_save_failure_after_the_handoff_records_no_ending(harness):
    spec = harness.spec()
    ctx, host = harness.context(spec)
    harness.model.answer = emoji_text(65500)
    harness.store.save_error = RuntimeError("window store down")
    with pytest.raises(BaseError) as exc_info:
        await collect(harness.api.execute_persistent_request(spec, ctx, request()))
    assert_rejected(exc_info, StatusCode.DEEPAGENT_RUNTIME_ERROR)
    assert "output.proposed" in kinds(host.events), "candidates are handed off before the save"
    assert not {"execution.final", "execution.paused"} & set(kinds(host.events)), "unreferenced, never delivered"


@pytest.mark.asyncio
async def test_a_failed_final_hands_its_outputs_in_again_and_completes(harness):
    """PA-08-V-07 at the entry: one hand-in again and one retry; state is saved once, nothing reruns."""
    spec = harness.spec()
    ctx, host = harness.context(spec)
    host.fail_once = {"execution.final"}
    events = await collect(harness.api.execute_persistent_request(spec, ctx, request()))
    assert events[-1].payload.status == "completed"
    recorded = kinds(host.events)
    assert recorded[-2:] == ["output.proposed", "execution.final"]
    proposals = [event for event in host.events if event.event_type == "output.proposed"]
    assert proposals[0] is proposals[1], "handed in again as the same event"
    assert harness.log.count("save") == 1 and harness.model.calls == 1


@pytest.mark.asyncio
async def test_ordinary_record_failure_does_not_stop_execution(harness):
    spec = harness.spec()
    ctx, host = harness.context(spec)
    host.fail_record = {"reply.delta"}
    events = await collect(harness.api.execute_persistent_request(spec, ctx, request()))
    assert "reply.delta" in kinds(events), "display still shows the unrecorded delta"
    assert "reply.delta" not in kinds(host.events)
    assert host.events[-1].payload.status == "completed"


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["final_record", "state_save"])
async def test_ending_failures_raise_runtime_errors(harness, failure):
    spec = harness.spec()
    ctx, host = harness.context(spec)
    if failure == "final_record":
        host.fail_record = {"execution.final"}
    else:
        harness.store.save_error = RuntimeError("window store down")
    with pytest.raises(BaseError) as exc_info:
        await collect(harness.api.execute_persistent_request(spec, ctx, request()))
    assert_rejected(exc_info, StatusCode.DEEPAGENT_RUNTIME_ERROR)
    assert not {"execution.final", "execution.paused"} & set(kinds(host.events))
    assert (harness.store.record is not None) == (failure == "final_record"), "save precedes the ending record"
    assert ctx.binding.runtime.closed


@pytest.mark.asyncio
async def test_critical_failure_swallowed_by_native_code_still_raises(harness):
    spec = harness.spec()
    ctx, host = harness.context(spec)
    critical = RuntimeError("critical record failed inside a tool")
    harness.model.on_call = lambda: ctx.binding.control.mark_fatal(critical)
    with pytest.raises(RuntimeError) as exc_info:
        await collect(harness.api.execute_persistent_request(spec, ctx, request()))
    assert exc_info.value is critical
    assert not {"output.proposed", "execution.final", "execution.paused"} & set(kinds(host.events))
    assert harness.store.record is None, "no state is saved after a critical failure"
    assert ctx.binding.runtime.closed


@pytest.mark.asyncio
@pytest.mark.parametrize("when", ["during_model_call", "between_tools"])
async def test_critical_failure_skips_tools_not_yet_started(harness, when):
    spec = harness.spec()
    ctx, host = harness.context(spec)
    critical = RuntimeError("critical record failed")
    if when == "during_model_call":
        harness.model.turns = ["tool"]
        harness.model.on_call = lambda: ctx.binding.control.mark_fatal(critical)
    else:
        harness.model.turns = ["two_tools"]
        harness.tool_hooks["on_invoke"] = lambda: ctx.binding.control.mark_fatal(critical)
    with pytest.raises(RuntimeError) as exc_info:
        await collect(harness.api.execute_persistent_request(spec, ctx, request()))
    assert exc_info.value is critical, "the registered critical error is raised, not a paused(error)"
    assert len(harness.tool_calls) == (0 if when == "during_model_call" else 1)
    assert harness.model.calls == 1, "no model call follows a critical failure"
    assert not {"output.proposed", "execution.final", "execution.paused"} & set(kinds(host.events))
    assert harness.store.record is None


@pytest.mark.asyncio
async def test_two_tool_calls_run_normally_without_a_critical_failure(harness):
    spec = harness.spec()
    ctx, _ = harness.context(spec)
    harness.model.turns = ["two_tools"]
    events = await collect(harness.api.execute_persistent_request(spec, ctx, request()))
    assert len(harness.tool_calls) == 2 and events[-1].payload.status == "completed"


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["critical", "state_load", "invalid_reply"])
async def test_failed_execution_accepts_no_control_during_cleanup(harness, monkeypatch, failure):
    spec = harness.spec(confirm=failure == "invalid_reply")
    if failure == "invalid_reply":
        await pause_for_confirmation(harness, spec)
    native_cleanup, native_abort = DeepAgent.cleanup_task_resources, DeepAgent.abort
    cleaning, release, native_aborts = asyncio.Event(), asyncio.Event(), []

    async def slow_cleanup(self):
        cleaning.set()
        await release.wait()
        await native_cleanup(self)

    async def spy_abort(self, session=None):
        native_aborts.append(session)
        await native_abort(self, session=session)

    monkeypatch.setattr(DeepAgent, "cleanup_task_resources", slow_cleanup)
    monkeypatch.setattr(DeepAgent, "abort", spy_abort)
    ctx, _ = harness.context(spec, execution_id="execution-2")
    critical = RuntimeError("critical record failed")
    if failure == "critical":
        harness.model.on_call = lambda: ctx.binding.control.mark_fatal(critical)
        stream = harness.api.execute_persistent_request(spec, ctx, request())
    elif failure == "state_load":
        harness.store.load_error = RuntimeError("window store down")
        stream = harness.api.execute_persistent_request(spec, ctx, request())
    else:
        stream = harness.api.resume_persistent_request(spec, ctx, permission("call-9"))
    consumer = asyncio.create_task(collect(stream))
    await asyncio.wait_for(cleaning.wait(), 5)
    for reason in ("cancel", "stop"):
        await harness.api.abort_persistent_request(ctx, reason=reason)
    control = ctx.binding.control
    assert control.outcome_locked and control.abort_reason is None, "no control is accepted during cleanup"
    assert native_aborts == []
    release.set()
    with pytest.raises(Exception) as exc_info:
        await asyncio.wait_for(consumer, 5)
    if failure == "critical":
        assert exc_info.value is critical
    else:
        code = StatusCode.DEEPAGENT_RUNTIME_ERROR if failure == "state_load" else StatusCode.DEEPAGENT_INPUT_PARAM_ERROR
        assert_rejected(exc_info, code)
    assert ctx.binding.runtime.closed


@pytest.mark.asyncio
async def test_state_load_failure_raises_before_the_model(harness):
    spec = harness.spec()
    ctx, host = harness.context(spec)
    harness.store.load_error = RuntimeError("window store down")
    with pytest.raises(BaseError) as exc_info:
        await collect(harness.api.execute_persistent_request(spec, ctx, request()))
    assert_rejected(exc_info, StatusCode.DEEPAGENT_RUNTIME_ERROR)
    assert harness.model.calls == 0
    assert kinds(host.events) == ["execution.started"]


async def pause_for_confirmation(harness, spec):
    harness.model.turns = ["tool"]
    ctx, _ = harness.context(spec)
    events = await collect(harness.api.execute_persistent_request(spec, ctx, request()))
    return events


@pytest.mark.asyncio
async def test_confirmation_pause_then_resume_completes(harness):
    spec = harness.spec(confirm=True)
    events = await pause_for_confirmation(harness, spec)
    assert kinds(events) == [
        "execution.started",
        "reply.delta",
        "model.completed",
        "progress.updated",
        "execution.paused",
    ]
    assert events[3].payload.text == "checking"
    assert "tool.intent" not in kinds(events) and "tool.completed" not in kinds(events)
    paused = events[-1].payload
    assert paused.reason == "awaiting_confirmation" and paused.detail is None
    call = paused.intercepted[0]
    assert (call.tool_call_id, call.tool_name) == ("call-1", "remember")
    assert "secret-value" not in call.arguments and '"api_token": "***"' in call.arguments
    assert harness.tool_calls == [], "the intercepted tool has not run"

    ctx, host = harness.context(spec, execution_id="execution-2")
    harness.tool_hooks["on_invoke"] = lambda: harness.log.append("tool.dispatched")
    resumed = await collect(harness.api.resume_persistent_request(spec, ctx, permission()))
    resumed_kinds = kinds(resumed)
    assert resumed_kinds.count("tool.intent") == 1
    assert resumed_kinds.count("tool.completed") == 1
    assert resumed_kinds.index("tool.intent") < resumed_kinds.index("tool.completed")
    assert resumed == host.events
    assert harness.tool_calls == [{"value": "x", "api_token": "secret-value"}]
    intent = next(event.payload for event in resumed if str(event.event_type) == "tool.intent")
    completed = next(event.payload for event in resumed if str(event.event_type) == "tool.completed")
    assert completed.operation_id == intent.operation_id
    assert harness.log.index("tool.intent") < harness.log.index("tool.dispatched") < harness.log.index("tool.completed")
    assert resumed[-2].payload.target_ref == "chat-1", "the reply target comes from the waiting scene"
    assert resumed[-1].payload.status == "completed"


@pytest.mark.asyncio
async def test_permission_rejection_records_no_write_intent_or_completion(harness):
    spec = harness.spec(confirm=True)
    await pause_for_confirmation(harness, spec)
    ctx, host = harness.context(spec, execution_id="execution-2")

    events = await collect(harness.api.resume_persistent_request(spec, ctx, permission(choice="reject")))

    assert harness.tool_calls == []
    assert "tool.intent" not in kinds(events)
    assert "tool.completed" not in kinds(events)
    assert events == host.events


@pytest.mark.asyncio
async def test_write_intent_is_recorded_before_tool_dispatch(harness):
    spec = harness.spec()
    ctx, host = harness.context(spec)
    harness.model.turns = ["tool"]
    harness.tool_hooks["on_invoke"] = lambda: harness.log.append("tool.dispatched")

    events = await collect(harness.api.execute_persistent_request(spec, ctx, request()))

    assert harness.tool_calls == [{"value": "x", "api_token": "secret-value"}]
    intent_index = harness.log.index("tool.intent")
    dispatch_index = harness.log.index("tool.dispatched")
    completed_index = harness.log.index("tool.completed")
    assert intent_index < dispatch_index < completed_index
    intent = next(event.payload for event in events if str(event.event_type) == "tool.intent")
    completed = next(event.payload for event in events if str(event.event_type) == "tool.completed")
    assert intent.tool_call_id == completed.tool_call_id == "call-1"
    assert completed.operation_id == intent.operation_id
    assert "secret-value" not in intent.args_summary and "secret-value" not in completed.args_summary
    assert events == host.events


@pytest.mark.asyncio
async def test_native_idempotent_retry_reuses_one_intent_and_final_completion(harness):
    harness.tool_hooks.update(idempotent=True, fail_once=True)
    spec = harness.spec(enable_tool_resilience_rail=True)
    ctx, host = harness.context(spec)
    harness.model.turns = ["tool"]

    events = await collect(harness.api.execute_persistent_request(spec, ctx, request()))

    intents = [event.payload for event in events if str(event.event_type) == "tool.intent"]
    completions = [event.payload for event in events if str(event.event_type) == "tool.completed"]
    assert len(harness.tool_calls) == 2, "the explicitly idempotent tool is retried once"
    assert len(intents) == len(completions) == 1
    assert completions[0].operation_id == intents[0].operation_id
    assert completions[0].status == "succeeded"
    assert events == host.events


@pytest.mark.asyncio
async def test_intent_persistence_failure_prevents_tool_and_raises_fatal(harness):
    spec = harness.spec()
    ctx, host = harness.context(spec)
    harness.model.turns = ["tool"]
    host.fail_record.add(module("schema.events").EventType.TOOL_INTENT)

    with pytest.raises(BaseError) as exc_info:
        await collect(harness.api.execute_persistent_request(spec, ctx, request()))

    assert harness.tool_calls == []
    assert ctx.binding.control.fatal is exc_info.value
    assert "tool.completed" not in kinds(host.events)
    assert "execution.paused" not in kinds(host.events)


@pytest.mark.asyncio
async def test_allowlisted_read_tool_records_completion_without_intent(harness):
    harness.model.tool_name = "read_file"
    harness.model.turns = ["tool"]
    spec = harness.spec()
    ctx, host = harness.context(spec)

    events = await collect(harness.api.execute_persistent_request(spec, ctx, request()))

    assert harness.tool_calls == [{"value": "x", "api_token": "secret-value"}]
    assert "tool.intent" not in kinds(events)
    completed = next(event.payload for event in events if str(event.event_type) == "tool.completed")
    assert completed.tool_name == "read_file"
    assert completed.operation_id is None
    assert events == host.events


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid", ["unknown_id", "answer_to_permission", "other_request", "nothing_waiting"])
async def test_invalid_reply_is_rejected_and_scene_keeps_waiting(harness, invalid):
    spec = harness.spec(confirm=True)
    if invalid != "nothing_waiting":
        await pause_for_confirmation(harness, spec)
    record, calls = harness.store.record, harness.model.calls
    reply = permission()
    changes = {}
    if invalid == "unknown_id":
        reply = permission("call-9")
    elif invalid == "answer_to_permission":
        reply = {"responses": [{"kind": "answer", "tool_call_id": "call-1", "answers": {"q": "a"}}]}
    elif invalid == "other_request":
        changes = {"request_id": "request-2"}
    ctx, host = harness.context(spec, execution_id="execution-2", **changes)
    with pytest.raises(BaseError) as exc_info:
        await collect(harness.api.resume_persistent_request(spec, ctx, reply))
    assert_rejected(exc_info, StatusCode.DEEPAGENT_INPUT_PARAM_ERROR)
    assert host.events == []
    assert harness.store.record is record, "nothing is saved for an invalid reply"
    assert harness.model.calls == calls
    assert ctx.binding.runtime.closed


@pytest.mark.asyncio
@pytest.mark.parametrize(("reason", "ending"), [("cancel", "execution.final"), ("stop", "execution.paused")])
async def test_abort_during_native_stream(harness, reason, ending):
    spec = harness.spec()
    ctx, host = harness.context(spec)
    harness.model.release.clear()
    consumer = asyncio.create_task(collect(harness.api.execute_persistent_request(spec, ctx, request())))
    await asyncio.wait_for(harness.model.streaming.wait(), 5)
    await harness.api.abort_persistent_request(ctx, reason=reason)
    events = await asyncio.wait_for(consumer, 5)
    assert harness.model.cancelled.is_set()
    assert kinds(events)[-1] == ending
    if reason == "cancel":
        assert events[-1].payload.status == "cancelled" and events[-1].payload.output_refs == ()
    else:
        assert events[-1].payload.reason == "stopped"
    assert "output.proposed" not in kinds(events)
    assert harness.store.endings[-1].outcome == ("cancelled" if reason == "cancel" else "stopped")
    assert events == host.events
    assert ctx.binding.runtime.closed


@pytest.mark.asyncio
@pytest.mark.parametrize("turns", [[], ["tool"]])
async def test_stop_saves_the_context_reached_so_far(harness, turns):
    """v2 appendix C item 5, partly: a stop during a model call keeps the context reached so far."""
    spec = harness.spec()
    ctx, _ = harness.context(spec)
    harness.model.turns = list(turns)
    harness.model.release.clear()
    consumer = asyncio.create_task(collect(harness.api.execute_persistent_request(spec, ctx, request())))
    await asyncio.wait_for(harness.model.streaming.wait(), 5)
    await harness.api.abort_persistent_request(ctx, reason="stop")
    events = await asyncio.wait_for(consumer, 5)
    assert events[-1].payload.reason == "stopped"
    messages = harness.store.record["state"]["context"]["default_context_id"]["messages"]
    assert messages[0].content == "please summarize"
    if turns:
        assert [type(message).__name__ for message in messages] == ["UserMessage", "AssistantMessage", "ToolMessage"]
        assert messages[1].tool_calls[0].id == "call-1" and messages[2].tool_call_id == "call-1"


@pytest.mark.asyncio
async def test_abort_before_construction_still_finishes(harness):
    spec = harness.spec()
    ctx, host = harness.context(spec)
    await harness.api.abort_persistent_request(ctx, reason="stop")
    await harness.api.abort_persistent_request(ctx, reason="cancel")
    events = await collect(harness.api.execute_persistent_request(spec, ctx, request()))
    assert harness.model.calls == 0
    assert kinds(events) == ["execution.started", "execution.paused"]
    assert events[-1].payload.reason == "stopped", "the first control reason wins"
    await harness.api.abort_persistent_request(ctx, reason="cancel")
    assert kinds(host.events) == kinds(events), "the control entry never records events"


@pytest.mark.asyncio
async def test_repeated_abort_during_stream_keeps_first_reason(harness):
    spec = harness.spec()
    ctx, _ = harness.context(spec)
    harness.model.release.clear()
    consumer = asyncio.create_task(collect(harness.api.execute_persistent_request(spec, ctx, request())))
    await asyncio.wait_for(harness.model.streaming.wait(), 5)
    await asyncio.gather(
        harness.api.abort_persistent_request(ctx, reason="stop"),
        harness.api.abort_persistent_request(ctx, reason="cancel"),
        harness.api.abort_persistent_request(ctx, reason="stop"),
    )
    events = await asyncio.wait_for(consumer, 5)
    assert [event.payload.reason for event in events if event.event_type == "execution.paused"] == ["stopped"]


@pytest.mark.asyncio
@pytest.mark.parametrize(("reason", "save_fails"), [("cancel", False), ("stop", False), ("cancel", True)])
async def test_control_after_the_outcome_is_locked_is_not_accepted(harness, monkeypatch, reason, save_fails):
    from openjiuwen.harness.deep_agent import DeepAgent

    native_aborts = []
    native_abort = DeepAgent.abort

    async def spy_abort(self, session=None):
        native_aborts.append(session)
        await native_abort(self, session=session)

    monkeypatch.setattr(DeepAgent, "abort", spy_abort)
    spec = harness.spec()
    ctx, host = harness.context(spec)
    saving, release, store_save = asyncio.Event(), asyncio.Event(), harness.store.save

    async def slow_save(context, ending):
        saving.set()
        await release.wait()
        if save_fails:
            raise RuntimeError("window store down")
        await store_save(context, ending)

    harness.store.save = slow_save
    consumer = asyncio.create_task(collect(harness.api.execute_persistent_request(spec, ctx, request())))
    await asyncio.wait_for(saving.wait(), 5)
    await harness.api.abort_persistent_request(ctx, reason=reason)
    control = ctx.binding.control
    assert control.outcome_locked and control.abort_reason is None, "the control reason is not modified"
    assert native_aborts == [] and not ctx.binding.runtime.stopped, "abort_bound is not called"
    release.set()
    if save_fails:
        with pytest.raises(BaseError) as exc_info:
            await asyncio.wait_for(consumer, 5)
        assert_rejected(exc_info, StatusCode.DEEPAGENT_RUNTIME_ERROR)
        assert not {"execution.final", "execution.paused"} & set(kinds(host.events)), "locking is not persistence"
        return
    events = await asyncio.wait_for(consumer, 5)
    assert events[-1].payload.status == "completed" and events[-1].payload.output_refs
    assert harness.store.endings[-1].outcome == "completed"


@pytest.mark.asyncio
async def test_cleanup_waits_for_a_slow_native_stop(harness, monkeypatch):
    from openjiuwen.harness.deep_agent import DeepAgent

    native_abort, entered, release = DeepAgent.abort, asyncio.Event(), asyncio.Event()

    async def slow_abort(self, session=None):
        await native_abort(self, session=session)
        entered.set()
        await release.wait()

    monkeypatch.setattr(DeepAgent, "abort", slow_abort)
    spec = harness.spec()
    ctx, _ = harness.context(spec)
    harness.model.release.clear()
    consumer = asyncio.create_task(collect(harness.api.execute_persistent_request(spec, ctx, request())))
    await asyncio.wait_for(harness.model.streaming.wait(), 5)
    control = asyncio.create_task(harness.api.abort_persistent_request(ctx, reason="cancel"))
    await asyncio.wait_for(entered.wait(), 5)
    await asyncio.sleep(0.05)
    assert not consumer.done(), "cleanup must not overlap the in-flight native stop"
    release.set()
    await control
    events = await asyncio.wait_for(consumer, 5)
    assert events[-1].payload.status == "cancelled"
    assert ctx.binding.runtime.closed


@pytest.mark.asyncio
async def test_closing_the_stream_early_drains_the_runner_without_ending(harness):
    spec = harness.spec()
    ctx, host = harness.context(spec)
    harness.model.release.clear()
    stream = harness.api.execute_persistent_request(spec, ctx, request())
    first = await stream.__anext__()
    assert first.event_type == "execution.started"
    await asyncio.wait_for(harness.model.streaming.wait(), 5)
    await stream.aclose()
    assert harness.model.cancelled.is_set()
    assert not {"execution.final", "execution.paused"} & set(kinds(host.events))
    assert harness.store.record is None
    assert ctx.binding.runtime.active_task is None and ctx.binding.runtime.closed


@pytest.mark.asyncio
async def test_old_checkpointer_state_is_neither_restored_nor_saved(harness, monkeypatch):
    monkeypatch.setattr(CheckpointerFactory, "_default_checkpointer", None)
    CheckpointerFactory.set_default_checkpointer(InMemoryCheckpointer())
    spec = harness.spec()
    earlier = Session(session_id="session-a", card=spec.card)
    await earlier.pre_run()
    earlier.update_state({"marker": "earlier"})
    await earlier.commit()

    ctx, _ = harness.context(spec)
    events = await collect(harness.api.execute_persistent_request(spec, ctx, request()))
    assert events[-1].payload.status == "completed"
    assert harness.store.loaded_marker is None, "global checkpointer state never reaches the Execution"
    later = Session(session_id="session-a", card=spec.card)
    await later.pre_run()
    assert later.get_state("marker") == "earlier"
    assert later.get_state("context") is None, "the Execution leaves no checkpointer snapshot"


@pytest.mark.parametrize(
    ("change", "code"),
    [
        ({"spec": {"enable_task_loop": True}}, StatusCode.DEEPAGENT_CONFIG_PARAM_ERROR),
        ({"spec": {"enable_subagent_runtime": True}}, StatusCode.DEEPAGENT_CONFIG_PARAM_ERROR),
        ({"spec": {"add_general_purpose_agent": True}}, StatusCode.DEEPAGENT_CONFIG_PARAM_ERROR),
        ({"context": {"isolated": False}}, StatusCode.DEEPAGENT_CONTEXT_PARAM_ERROR),
        ({"context": {"recovery_ref": "not an identifier"}}, StatusCode.DEEPAGENT_CONTEXT_PARAM_ERROR),
        ({"request": {"continue_instruction": "go on"}}, StatusCode.DEEPAGENT_INPUT_PARAM_ERROR),
        ({"request": {"reply_target": ""}}, StatusCode.DEEPAGENT_INPUT_PARAM_ERROR),
        (
            {
                "request": {
                    "kind": "group_message",
                    "source": {
                        "message_id": "m-1",
                        "sender_id": "u-1",
                        "sender_name": "李四",
                        "sender_kind": "human",
                        "received_at": "2026-10-07T08:00:00+08:00",
                    },
                }
            },
            StatusCode.DEEPAGENT_CONFIG_PARAM_ERROR,
        ),
    ],
)
@pytest.mark.asyncio
async def test_execute_rejects_before_any_event(harness, monkeypatch, change, code):
    spec = harness.spec(**change.get("spec", {}))
    ctx, host = harness.context(spec, **change.get("context", {}))
    with pytest.raises(BaseError) as exc_info:
        await collect(harness.api.execute_persistent_request(spec, ctx, request(**change.get("request", {}))))
    assert_rejected(exc_info, code)
    assert host.events == [] and harness.model.calls == 0
    assert not ctx.binding.attempted, "nothing was constructed"


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["invalid_reply", "adapter_not_integrated"])
async def test_resume_rejects_before_any_event(harness, monkeypatch, case):
    spec = harness.spec(confirm=True)
    ctx, host = harness.context(spec)
    reply, code = {"responses": []}, StatusCode.DEEPAGENT_INPUT_PARAM_ERROR
    if case == "adapter_not_integrated":
        monkeypatch.setattr(SEAMS, "native_reply_adapter", UNINTEGRATED_ADAPTER)
        reply, code = permission(), StatusCode.DEEPAGENT_CONFIG_PARAM_ERROR
    with pytest.raises(BaseError) as exc_info:
        await collect(harness.api.resume_persistent_request(spec, ctx, reply))
    assert_rejected(exc_info, code)
    assert host.events == [] and harness.model.calls == 0
    assert not ctx.binding.attempted


def test_redaction_masks_sensitive_keys_at_any_depth():
    redact = module("execution.entries").redact_arguments
    masked = redact({"path": "a.txt", "auth": {"Authorization": "Bearer x", "items": [{"apiKey": "k"}]}})
    assert "Bearer x" not in masked and '"apiKey": "***"' in masked and '"path": "a.txt"' in masked
    assert redact('{"password": "p"}') == '{"password": "***"}'
    assert redact("not json") == "not json"
    assert redact(None) == "{}"


class GroupChat:
    """PA-23 double: returns a fixed recent-message page for participation."""

    def __init__(self, messages, status="found"):
        self.messages = messages
        self.status = status
        self.calls = []

    async def recent_messages(self, execution, cursor=None, limit=20):
        self.calls.append((cursor, limit))
        ref = module("schema.protocol").InvocationRef.from_execution(execution).model_dump()
        payload = {**ref, "status": self.status, "messages": ()}
        if self.status == "found":
            payload["messages"] = tuple(self.messages)
        elif self.status == "incomplete":
            payload["reason"] = "group history unavailable"
        return payload

    async def search_messages(self, execution, query):
        raise AssertionError("participation does not search group history")


def group_message(**overrides):
    message = {
        "message_id": "m-0",
        "sender_id": "u-0",
        "sender_name": "王五",
        "sender_kind": "human",
        "received_at": "2026-10-07T07:00:00+08:00",
        "text": "EARLIER-CONTEXT",
    }
    message.update(overrides)
    return message


def group_request(**source_overrides):
    source = {
        "message_id": "m-1",
        "sender_id": "u-1",
        "sender_name": "李四",
        "sender_kind": "human",
        "received_at": "2026-10-07T08:00:00+08:00",
        "text": "大家好",
        "mention_ids": (),
        "reply_to": "m-0",
        "attachment_refs": ("att-1",),
    }
    source.update(source_overrides)
    return request(kind="group_message", source=source)


def judgment(decision, reason="需要处理"):
    return json.dumps({"decision": decision, "reason": reason}, ensure_ascii=False)


def prompt_text(model):
    parts = []
    for messages in model.judge_messages:
        for message in messages:
            parts.append(message["content"] if isinstance(message, dict) else message.content)
    return "\n".join(parts)


def direct_request():
    return request(
        kind="direct_message",
        source={
            "message_id": "dm-1",
            "sender_id": "u-1",
            "sender_name": "李四",
            "received_at": "2026-10-07T08:00:00+08:00",
        },
    )


@pytest.mark.asyncio
async def test_group_message_without_port_is_rejected_before_events(harness):
    spec = harness.spec()
    ctx, host = harness.context(spec)
    with pytest.raises(BaseError) as exc_info:
        await collect(harness.api.execute_persistent_request(spec, ctx, group_request()))
    assert_rejected(exc_info, StatusCode.DEEPAGENT_CONFIG_PARAM_ERROR)
    assert "group chat port is required for group participation" in str(exc_info.value)
    assert host.events == [] and harness.model.calls == 0 and harness.model.judge_calls == 0
    assert not ctx.binding.attempted


@pytest.mark.asyncio
async def test_mentioned_group_message_bypasses_judgment_and_enters_execution(harness):
    spec = harness.spec(system_prompt="你是团队里的调研专家。")
    chat = GroupChat(
        (
            group_message(message_id="m-1", text="TRIGGER-SHOULD-BE-EXCLUDED"),
            group_message(),
        )
    )
    ctx, host = harness.context(spec, group_chat=chat)
    harness.model.judgments = [judgment("ignore", "与当前职责无关")]
    events = await collect(
        harness.api.execute_persistent_request(spec, ctx, group_request(mention_ids=("business-agent",)))
    )
    decided = next(event for event in events if event.event_type == "participation.decided")
    started = next(event for event in events if event.event_type == "execution.started")
    final = next(event for event in events if event.event_type == "execution.final")

    assert decided.payload.decision == "respond"
    assert decided.payload.evidence_refs == ("m-1",)
    assert decided.payload.model_call_id is None
    assert started.payload.decision_event_id == decided.event_id
    assert final.payload.status == "completed"
    assert harness.model.calls == 1 and harness.model.judge_calls == 0
    assert chat.calls == []
    assert harness.assembler.calls and ctx.binding.attempted
    assert host.events == events


@pytest.mark.asyncio
async def test_participation_prompt_tells_an_unreadable_body_from_a_recall(harness):
    """PA-07-V-09: M103's body is only unavailable, so the judgment must not be told it was recalled."""
    spec = harness.spec()
    chat = GroupChat(
        (
            group_message(message_id="M103", sender_name="张三", status="unavailable", text=None),
            group_message(message_id="M104", sender_name="赵六", status="recalled", text=None),
            group_message(message_id="M105", sender_name="钱七", text=None),
        )
    )
    ctx, _ = harness.context(spec, group_chat=chat)
    harness.model.judgments = [judgment("ignore", "与当前职责无关")]
    await collect(harness.api.execute_persistent_request(spec, ctx, group_request()))
    text = prompt_text(harness.model)
    assert "- 群消息 张三: 正文暂缺" in text
    assert "- 群消息 赵六: 已撤回" in text
    assert "- 群消息 钱七: （无正文）" in text
    assert text.count("已撤回") == 1, "only the recalled message is reported as recalled"


@pytest.mark.asyncio
@pytest.mark.parametrize("decision", ["respond", "intervene"])
async def test_unmentioned_group_message_can_continue_into_execution(harness, decision):
    spec = harness.spec(system_prompt="你是团队里的调研专家。")
    chat = GroupChat((group_message(),))
    ctx, _ = harness.context(spec, group_chat=chat)
    harness.model.judgments = [judgment(decision)]
    events = await collect(harness.api.execute_persistent_request(spec, ctx, group_request(mention_ids=())))
    decided = next(event for event in events if event.event_type == "participation.decided")
    started = next(event for event in events if event.event_type == "execution.started")
    assert decided.payload.decision == decision
    assert started.payload.decision_event_id == decided.event_id
    assert "被提及: 否" in prompt_text(harness.model)
    assert events[-1].payload.status == "completed"
    assert "output.proposed" in kinds(events)
    assert harness.model.judge_calls == 1 and harness.model.calls == 1
    assert len(harness.assembler.calls) == 1
    assert kinds(events).index("participation.decided") < kinds(events).index("execution.started")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "judgments",
    [["nope", "{", "[]"], [RuntimeError("down"), RuntimeError("down"), RuntimeError("down")]],
)
async def test_participation_failure_after_retries_is_not_ignored(harness, judgments):
    spec = harness.spec(system_prompt="你是团队里的调研专家。")
    chat = GroupChat((group_message(),))
    ctx, _ = harness.context(spec, group_chat=chat)
    harness.model.judgments = list(judgments)
    events = await collect(harness.api.execute_persistent_request(spec, ctx, group_request()))
    assert events[-1].event_type == "execution.final" and events[-1].payload.status == "failed"
    assert "participation.decided" not in kinds(events) and "ignored" not in {
        getattr(event.payload, "status", None) for event in events
    }
    assert harness.model.judge_calls == 3 and harness.model.calls == 0
    assert harness.assembler.calls == [] and harness.store.loaded_marker == "never loaded"
    assert not ctx.binding.attempted


@pytest.mark.asyncio
async def test_budget_denial_skips_the_participation_model(harness):
    spec = harness.spec(system_prompt="你是团队里的调研专家。")
    chat = GroupChat((group_message(),))
    ctx, host = harness.context(spec, group_chat=chat)
    host.admission = "denied"
    harness.model.judgments = [judgment("ignore")]
    events = await collect(harness.api.execute_persistent_request(spec, ctx, group_request()))
    assert kinds(events) == ["execution.paused"]
    assert events[0].payload.reason == "budget_denied"
    assert harness.model.judge_calls == 0 and host.reports == []
    assert harness.store.loaded_marker == "never loaded"


@pytest.mark.asyncio
async def test_incomplete_group_history_is_not_treated_as_ignore(harness):
    spec = harness.spec(system_prompt="你是团队里的调研专家。")
    chat = GroupChat((), status="incomplete")
    ctx, _ = harness.context(spec, group_chat=chat)
    events = await collect(harness.api.execute_persistent_request(spec, ctx, group_request()))
    assert events[-1].payload.status == "failed"
    assert "participation.decided" not in kinds(events)
    assert harness.model.judge_calls == 0 and len(chat.calls) == 3
    assert harness.store.loaded_marker == "never loaded"


@pytest.mark.asyncio
@pytest.mark.parametrize("payload", ["direct", "scheduled"])
async def test_direct_and_scheduled_requests_skip_participation(harness, payload):
    spec = harness.spec()
    chat = GroupChat((group_message(),))
    ctx, _ = harness.context(spec, group_chat=chat)
    incoming = direct_request() if payload == "direct" else request()
    events = await collect(harness.api.execute_persistent_request(spec, ctx, incoming))
    assert chat.calls == [] and harness.model.judge_calls == 0
    assert "participation.decided" not in kinds(events)
    assert events[0].event_type == "execution.started" and events[0].payload.decision_event_id is None
    assert events[-1].payload.status == "completed"


# --- PA-10: the formal session state store ---------------------------------------------------------

STATE = module("execution.session_state")
RECORD = module("schema.session_record")
PLACEHOLDER = "结果未取得，实际效果需核实"
RETURNED_NOTE = "[Result not included]"
RAISED_NOTE = "[Error not included]"
CHECKED = "checked by the host"
BLOCKED = "blocked by the host: the output is withheld"
SKIPPED = "本次执行正在结束，该工具未调用。"


class KeepPlanSlug(DeepAgentRail):
    """Changes the cached DeepAgent state only; DeepAgent itself writes it back on a normal end."""

    def init(self, agent):
        super().init(agent)
        self.agent = agent

    async def before_model_call(self, ctx) -> None:
        self.agent.load_state(ctx.session).plan_mode.plan_slug = "kept-slug"


class LateNote(DeepAgentRail):
    """Adds to the live window after the native agent has written it back, as an outer rail may."""

    def init(self, agent):
        super().init(agent)
        self.agent = agent

    async def after_invoke(self, ctx) -> None:
        engine = self.agent.react_agent.context_engine
        await engine.get_context(session_id=ctx.session.get_session_id()).add_messages(UserMessage(content="late note"))


class FailWritingSecond(DeepAgentRail):
    """Makes writing call-b's result into the context fail, after both tools have finished."""

    def init(self, agent):
        super().init(agent)
        self.agent = agent

    async def before_tool_call(self, ctx) -> None:
        if ctx.inputs.tool_call.id != "call-b":
            return
        context = self.agent.react_agent.context_engine.get_context(session_id=ctx.session.get_session_id())
        write = context.add_messages

        async def add_messages(message, *args, **kwargs):
            if getattr(message, "tool_call_id", None) == "call-b":
                raise RuntimeError("context write failed")
            return await write(message, *args, **kwargs)

        context.add_messages = add_messages


class HostOutputCheck(DeepAgentRail):
    """Acts as a host security rail: replaces every tool output before it may be used.

    ``control`` can hold one (event, call id) until released, make one abort its processing, or block
    one as the host contract asks: replace it with a safe note first, then request a forced finish.
    An ordinary exception would not stop anything: the callback framework logs it and runs the rest.
    """

    def __init__(self, control):
        super().__init__()
        self.control = control

    async def on_tool_exception(self, ctx) -> None:
        await self._check(ctx, "on_tool_exception")

    async def after_tool_call(self, ctx) -> None:
        await self._check(ctx, "after_tool_call")

    async def _check(self, ctx, event):
        call_id = ctx.inputs.tool_call.id
        if self.control.get("hold") == (event, call_id):
            self.control["reached"].set()
            await self.control["gate"].wait()
        if self.control.get("fail") == (event, call_id):
            raise AbortError("output check failed")
        if self.control.get("block") == (event, call_id):
            ctx.inputs.tool_result = BLOCKED
            ctx.inputs.tool_msg = ToolMessage(content=BLOCKED, tool_call_id=call_id)
            ctx.request_force_finish({"output": "output withheld by the host", "result_type": "error"})
            return
        if event == "after_tool_call":
            ctx.inputs.tool_result = CHECKED
            ctx.inputs.tool_msg = ToolMessage(content=CHECKED, tool_call_id=call_id)


class RewriteArgs(DeepAgentRail):
    """Rewrites every call's arguments before the confirmation rail looks at it."""

    priority = 95

    async def before_tool_call(self, ctx) -> None:
        ctx.inputs.tool_args = '{"value": "rewritten"}'


class AddOffloader(DeepAgentRail):
    """Adds an offloading context processor while rails register, after construction checks."""

    def init(self, agent):
        super().init(agent)
        react_agent = getattr(agent, "react_agent", None) or agent
        react_agent.config.context_processors = [("MessageOffloader", None)]


class Backend(InMemoryKVStore):
    """In-memory window backend that counts writes and can fail on demand."""

    def __init__(self):
        super().__init__()
        self.sets = 0
        self.get_error = None
        self.set_error = None

    async def get(self, key):
        if self.get_error is not None:
            raise self.get_error
        return await super().get(key)

    async def set(self, key, value):
        if self.set_error is not None:
            raise self.set_error
        self.sets += 1
        await super().set(key, value)


@pytest.fixture
def real_state(harness, monkeypatch):
    monkeypatch.setattr(SEAMS, "session_state_store", REAL_STATE_STORE)
    monkeypatch.setattr(SEAMS, "window_assembler", REAL_ASSEMBLER)
    monkeypatch.setitem(_RAIL_PROVIDER_REGISTRY, "test.entries.keep_plan_slug", lambda params, context: KeepPlanSlug())
    monkeypatch.setitem(_RAIL_PROVIDER_REGISTRY, "test.entries.add_offloader", lambda params, context: AddOffloader())
    monkeypatch.setitem(_RAIL_PROVIDER_REGISTRY, "test.entries.late_note", lambda params, context: LateNote())
    monkeypatch.setitem(
        _RAIL_PROVIDER_REGISTRY, "test.entries.fail_writing_second", lambda params, context: FailWritingSecond()
    )
    monkeypatch.setitem(_RAIL_PROVIDER_REGISTRY, "test.entries.rewrite_args", lambda params, context: RewriteArgs())
    harness.output_check = {"reached": asyncio.Event(), "gate": asyncio.Event()}
    monkeypatch.setitem(
        _RAIL_PROVIDER_REGISTRY,
        "test.entries.host_output_check",
        lambda params, context: HostOutputCheck(harness.output_check),
    )
    harness.backend = Backend()
    return harness


def state_key(session_id="session-a"):
    return STATE.WindowStore.state_key("business-agent", session_id)


async def saved_record(harness, session_id="session-a"):
    raw = await harness.backend.get(state_key(session_id))
    return None if raw is None else json.loads(raw)


def window_of(record):
    return RECORD.decode_messages(record["messages"])


def parts(messages):
    return [(message.metadata or {}).get(SEAMS.WINDOW_PART_KEY) for message in messages]


@pytest.mark.asyncio
async def test_a_waiting_scene_keeps_the_call_as_the_model_made_it(real_state):
    """A rewrite before the confirmation does not reach the saved call; the resume applies it again."""
    harness = real_state
    spec = harness.spec(rails=[RailSpec(type="test.entries.confirm"), RailSpec(type="test.entries.rewrite_args")])
    await pause_for_confirmation(harness, spec)
    record = await saved_record(harness)
    scene = RECORD.decode_interruption(record["interruption"])
    shown = [message for message in window_of(record) if getattr(message, "tool_calls", None)][-1].tool_calls
    assert [call.arguments for call in shown] == [TOOL_ARGS]
    assert [call.arguments for call in scene.ai_message.tool_calls] == [TOOL_ARGS]
    assert scene.interrupted_tools["call-1"].tool_call.arguments == TOOL_ARGS

    ctx, _ = harness.context(spec, execution_id="execution-2")
    resumed = await collect(harness.api.resume_persistent_request(spec, ctx, permission()))
    assert resumed[-1].payload.status == "completed", "the saved scene is consistent and loads"
    assert harness.tool_calls == [{"value": "rewritten"}], "the resume runs the rewritten call"


@pytest.mark.asyncio
async def test_record_carries_the_window_to_the_next_request_of_the_same_session(real_state):
    """PA-10-V-01 / V-02."""
    harness = real_state
    spec = harness.spec()
    ctx, _ = harness.context(spec)
    first = await collect(harness.api.execute_persistent_request(spec, ctx, request(text="first task")))
    assert first[-1].payload.status == "completed"
    record = await saved_record(harness)
    assert harness.backend.sets == 1, "one record, one write"
    assert record["schema_version"] == 1 and (record["agent_id"], record["session_id"]) == (
        "business-agent",
        "session-a",
    )
    assert record["interruption"] is None and record["ending"]["outcome"] == "completed"

    ctx, _ = harness.context(spec, execution_id="execution-2", request_id="request-2")
    await collect(harness.api.execute_persistent_request(spec, ctx, request(text="second task")))
    seen = harness.model.seen[-1]
    assert "first task" in seen and "local answer" in seen, "the second Request sees the first one's window"

    other, _ = harness.context(spec, execution_id="execution-3", session_id="session-b")
    await collect(harness.api.execute_persistent_request(spec, other, request(text="other session")))
    assert "first task" not in harness.model.seen[-1], "another Session starts from its own record"
    assert (await saved_record(harness, "session-b"))["session_id"] == "session-b"


@pytest.mark.asyncio
async def test_end_marker_records_how_the_execution_ended(real_state):
    """PA-10-V-03: header kept as one line, background dropped, one end marker per Execution."""
    harness = real_state
    spec = harness.spec()
    ctx, _ = harness.context(spec)
    await collect(harness.api.execute_persistent_request(spec, ctx, request()))
    messages = window_of(await saved_record(harness))
    assert parts(messages) == ["execution_header", None, None, "execution_end"]
    assert messages[-1].content.startswith(
        "【执行结束】request_id：request-1；execution_id：execution-1；结局：已完成；"
        "交出候选：最终回答 1 段（以结束记录为准）"
    )

    ctx, host = harness.context(spec, execution_id="execution-2", request_id="request-2")
    host.admission = "denied"
    events = await collect(harness.api.execute_persistent_request(spec, ctx, request()))
    assert events[-1].payload.reason == "budget_denied"
    messages = window_of(await saved_record(harness))
    assert parts(messages).count("execution_end") == 2 and parts(messages).count("execution_header") == 2
    assert "结局：暂停（budget_denied）；交出候选：无" in messages[-1].content


@pytest.mark.asyncio
async def test_confirmation_scene_is_saved_without_a_marker_and_resumed(real_state):
    """PA-10-V-04: the waiting call stays last; the scene comes back for the reply."""
    harness = real_state
    spec = harness.spec(confirm=True)
    await pause_for_confirmation(harness, spec)
    record = await saved_record(harness)
    assert record["ending"]["outcome"] == "awaiting_confirmation" and record["interruption"] is not None
    messages = window_of(record)
    assert "execution_end" not in parts(messages)
    assert messages[-1].tool_calls[0].id == "call-1", "the call awaiting its result is left untouched"
    request_type = next(iter(record["interruption"]["interrupted_tools"]["call-1"]["interrupt_requests"].values()))
    assert request_type["type"] == "interrupt_request"

    ctx, _ = harness.context(spec, execution_id="execution-2")
    resumed = await collect(harness.api.resume_persistent_request(spec, ctx, permission()))
    assert resumed[-1].payload.status == "completed"
    record = await saved_record(harness)
    assert record["interruption"] is None and record["ending"]["execution_id"] == "execution-2"
    messages = window_of(record)
    assert STATE.unpaired_tool_calls(messages) == [] and parts(messages)[-1] == "execution_end"


@pytest.mark.asyncio
async def test_stop_during_a_model_call_is_saved_and_the_next_task_runs(real_state):
    """PA-10-V-06, and stop → save → load → next task for a stop during a model call."""
    harness = real_state
    spec = harness.spec()
    ctx, _ = harness.context(spec)
    harness.model.release.clear()
    consumer = asyncio.create_task(collect(harness.api.execute_persistent_request(spec, ctx, request())))
    await asyncio.wait_for(harness.model.streaming.wait(), 5)
    await harness.api.abort_persistent_request(ctx, reason="stop")
    events = await asyncio.wait_for(consumer, 5)
    assert events[-1].payload.reason == "stopped"
    messages = window_of(await saved_record(harness))
    assert [message.content for message in messages[1:3]] == ["please summarize", "[Request cancelled by user]"]
    assert "结局：暂停（stopped）" in messages[-1].content

    harness.model.release.set()
    ctx, _ = harness.context(spec, execution_id="execution-2", request_id="request-2")
    events = await collect(harness.api.execute_persistent_request(spec, ctx, request(text="next task")))
    assert events[-1].payload.status == "completed"
    assert "[Request cancelled by user]" in harness.model.seen[-1]


@pytest.mark.asyncio
async def test_save_takes_the_live_native_window(real_state):
    """PA-10-V-15: what changed after the native write-back is still saved."""
    harness = real_state
    spec = harness.spec(rails=[RailSpec(type="test.entries.late_note")])
    ctx, _ = harness.context(spec)
    events = await collect(harness.api.execute_persistent_request(spec, ctx, request()))
    assert events[-1].payload.status == "completed"
    contents = [message.content for message in window_of(await saved_record(harness))]
    assert contents[-2:] == ["late note", contents[-1]] and contents[-1].startswith("【执行结束】")


@pytest.mark.asyncio
async def test_stop_before_any_native_context_keeps_the_assembled_window(real_state):
    """PA-10-V-16: no native context exists, so no empty snapshot replaces the window."""
    harness = real_state
    spec = harness.spec()
    ctx, _ = harness.context(spec)
    await harness.api.abort_persistent_request(ctx, reason="stop")
    events = await collect(harness.api.execute_persistent_request(spec, ctx, request()))
    assert events[-1].payload.reason == "stopped" and harness.model.calls == 0
    assert parts(window_of(await saved_record(harness))) == ["execution_header", "execution_end"]


@pytest.mark.asyncio
async def test_an_invalid_record_is_rejected_before_the_model_and_left_as_it_is(real_state):
    """PA-10-V-10 at the entry: a validation rejection, not a runtime error."""
    harness = real_state
    await harness.backend.set(state_key(), '{"schema_version": 2}')
    spec = harness.spec()
    ctx, host = harness.context(spec)
    with pytest.raises(BaseError) as exc_info:
        await collect(harness.api.execute_persistent_request(spec, ctx, request()))
    assert_rejected(exc_info, StatusCode.DEEPAGENT_CONTEXT_PARAM_ERROR)
    assert harness.model.calls == 0 and kinds(host.events) == ["execution.started"]
    assert await harness.backend.get(state_key()) == '{"schema_version": 2}'


@pytest.mark.asyncio
@pytest.mark.parametrize("failing", ["get", "set"])
async def test_backend_faults_are_runtime_errors_at_the_entry(real_state, failing):
    """PA-10-V-09: a failed read never calls the model; a failed write records no ending."""
    harness = real_state
    setattr(harness.backend, f"{failing}_error", ValueError("backend down"))
    spec = harness.spec()
    ctx, host = harness.context(spec)
    with pytest.raises(BaseError) as exc_info:
        await collect(harness.api.execute_persistent_request(spec, ctx, request()))
    assert_rejected(exc_info, StatusCode.DEEPAGENT_RUNTIME_ERROR)
    assert "execution.final" not in kinds(host.events) and "execution.paused" not in kinds(host.events)
    assert harness.model.calls == (0 if failing == "get" else 1)


@pytest.mark.asyncio
async def test_a_failed_save_after_a_stop_is_still_a_runtime_error(real_state):
    """PA-10-V-25: placeholders do not turn a save failure into an ordinary ending."""
    harness = real_state
    harness.backend.set_error = ValueError("backend down")
    spec = harness.spec()
    ctx, host = harness.context(spec)
    harness.model.release.clear()
    consumer = asyncio.create_task(collect(harness.api.execute_persistent_request(spec, ctx, request())))
    await asyncio.wait_for(harness.model.streaming.wait(), 5)
    await harness.api.abort_persistent_request(ctx, reason="stop")
    with pytest.raises(BaseError) as exc_info:
        await asyncio.wait_for(consumer, 5)
    assert_rejected(exc_info, StatusCode.DEEPAGENT_RUNTIME_ERROR)
    assert "execution.paused" not in kinds(host.events)


@pytest.mark.asyncio
async def test_an_oversized_answer_completes_and_stays_in_the_window(real_state):
    """PA-08-V-09: the answer is handed off in segments; the record keeps it as the native reply in the
    window and writes no ending.pending_answer."""
    harness = real_state
    harness.model.answer = emoji_text(65500)
    spec = harness.spec()
    ctx, _ = harness.context(spec)
    events = await collect(harness.api.execute_persistent_request(spec, ctx, request()))
    assert events[-1].payload.status == "completed"
    record = await saved_record(harness)
    assert "pending_answer" not in record["ending"]
    messages = window_of(record)
    assert "handoff_text" not in parts(messages), "the native reply already is the answer handed off"
    assert any(message.content == "local " + harness.model.answer for message in messages), "the full answer stays"
    assert f"交出候选：最终回答 {len(outputs(events))} 段" in messages[-1].content


@pytest.mark.asyncio
async def test_a_rewritten_answer_is_kept_in_the_window_as_handed_off(real_state):
    """PA-08-V-10: the window keeps the text the group received, not only the native reply."""
    harness = real_state
    harness.rewrite["output"] = "rewritten answer"
    spec = harness.spec(rewrite=True)
    ctx, _ = harness.context(spec)
    await collect(harness.api.execute_persistent_request(spec, ctx, request()))
    messages = window_of(await saved_record(harness))
    assert parts(messages)[-2:] == ["handoff_text", "execution_end"]
    assert messages[-2].content == "【实际交出的最终回答】\nrewritten answer"
    assert messages[-3].content == "local answer", "the native reply stays as it was"


@pytest.mark.asyncio
@pytest.mark.parametrize("stop", [False, True], ids=["completed", "stopped"])
async def test_deepagent_state_is_saved_even_when_the_native_end_skips_it(real_state, stop):
    """PA-10-V-18: a stop skips DeepAgent's own write-back; PA-10 flushes the cached state first."""
    harness = real_state
    spec = harness.spec(rails=[RailSpec(type="test.entries.keep_plan_slug")])
    ctx, _ = harness.context(spec)
    if stop:
        harness.model.release.clear()
    consumer = asyncio.create_task(collect(harness.api.execute_persistent_request(spec, ctx, request())))
    if stop:
        await asyncio.wait_for(harness.model.streaming.wait(), 5)
        await harness.api.abort_persistent_request(ctx, reason="stop")
    await asyncio.wait_for(consumer, 5)
    record = await saved_record(harness)
    assert record["deepagent_state"]["plan_mode"]["plan_slug"] == "kept-slug"

    harness.model.release.set()
    ctx, _ = harness.context(spec, execution_id="execution-2", request_id="request-2")
    await collect(harness.api.execute_persistent_request(spec, ctx, request()))
    assert ctx.session.get_state("deepagent")["plan_mode"]["plan_slug"] == "kept-slug"


@pytest.mark.asyncio
async def test_offloading_added_while_rails_register_is_refused_before_the_model(real_state):
    """PA-10-V-21: a rail's init can add processors after construction; they are refused at invoke."""
    harness = real_state
    spec = harness.spec(rails=[RailSpec(type="test.entries.add_offloader")])
    ctx, host = harness.context(spec)
    with pytest.raises(BaseError) as exc_info:
        await collect(harness.api.execute_persistent_request(spec, ctx, request()))
    assert_rejected(exc_info, StatusCode.DEEPAGENT_CONFIG_PARAM_ERROR)
    assert "MessageOffloader" in str(exc_info.value)
    assert harness.model.calls == 0 and await saved_record(harness) is None
    assert "execution.final" not in kinds(host.events) and "execution.paused" not in kinds(host.events)


@pytest.mark.asyncio
async def test_offloading_found_at_construction_is_refused_before_any_event(real_state, monkeypatch):
    """PA-10-V-21: the construction check reads the assembled native configuration."""
    harness = real_state
    monkeypatch.setattr(module("factory"), "offloading_processors", lambda agent: ["MessageOffloader"])
    spec = harness.spec()
    ctx, host = harness.context(spec)
    with pytest.raises(BaseError) as exc_info:
        await collect(harness.api.execute_persistent_request(spec, ctx, request()))
    assert_rejected(exc_info, StatusCode.DEEPAGENT_CONFIG_PARAM_ERROR)
    assert host.events == [] and harness.model.calls == 0


def call_group(messages):
    """The assistant message carrying call-a and call-b, followed by its results."""
    index = next(i for i, message in enumerate(messages) if getattr(message, "tool_calls", None))
    return messages[index], messages[index + 1 : index + 3]


async def next_task_sees(harness, spec, *texts):
    harness.model.release.set()
    ctx, _ = harness.context(spec, execution_id="execution-2", request_id="request-2")
    events = await collect(harness.api.execute_persistent_request(spec, ctx, request(text="next task")))
    assert events[-1].payload.status == "completed", "the next task runs on the saved window"
    for text in texts:
        assert any(text in str(content) for content in harness.model.seen[-1]), text


@pytest.mark.asyncio
async def test_persistent_construction_turns_on_tool_fact_retention(harness):
    spec = harness.spec()
    ctx, _ = harness.context(spec)
    agent = harness.api.create_persistent_agent(spec, context=ctx)
    assert agent.react_agent.config.retain_tool_facts_on_abort is True


@pytest.mark.asyncio
async def test_new_context_switches_only_after_the_tool_result_and_commits_archive_before_answer(real_state):
    harness = real_state
    spec = harness.spec(
        enabled_features=frozenset({"construction", "window_management"}),
        model_input_limit_tokens=100_000,
        capacity_safety_margin_tokens=1_000,
    )
    ctx, _ = harness.context(spec)
    seed = {
        "objective": "整理当前任务并继续",
        "completed": ["已完成本轮工具检查"],
        "next_steps": ["给出最终答复"],
    }
    harness.model.turns = [("new_context", json.dumps(seed, ensure_ascii=False)), "answer"]

    events = await collect(harness.api.execute_persistent_request(spec, ctx, request()))

    assert events[-1].payload.status == "completed"
    assert harness.model.calls == 2, "the candidate window is rebuilt before the second call"
    assert "window.switched" in kinds(events)
    record = await saved_record(harness)
    assert record["window_seq"] == 2 and record["last_rollover"]["old_window_seq"] == 1
    archive = (
        await module("execution.session_state")
        .WindowStore(harness.backend)
        .load_archive("business-agent", "session-a", 1)
    )
    archived = module("schema.session_record").decode_messages(archive.messages)
    assert any(getattr(message, "tool_calls", None) for message in archived)
    assert any(getattr(message, "tool_call_id", None) == "call-1" for message in archived)


@pytest.mark.asyncio
@pytest.mark.parametrize("reason", ["stop", "cancel"])
async def test_tool_facts_survive_a_control_mid_batch(real_state, reason):
    """PA-10-V-07 / V-14 / V-23: call-a finished, call-b in flight; the next task still sees call-a.

    Exclusive calls absorb the cancel, so the batch ends on its own and call-b carries the native result.
    """
    harness = real_state
    spec = harness.spec()
    ctx, _ = harness.context(spec)
    harness.model.turns = ["two_tools"]
    harness.tool_hooks.update(gate=asyncio.Event(), gate_call=2, gate_reached=asyncio.Event())
    consumer = asyncio.create_task(collect(harness.api.execute_persistent_request(spec, ctx, request())))
    await asyncio.wait_for(harness.tool_hooks["gate_reached"].wait(), 5)
    await harness.api.abort_persistent_request(ctx, reason=reason)
    events = await asyncio.wait_for(consumer, 5)
    if reason == "stop":
        assert events[-1].payload.reason == "stopped"
    else:
        assert events[-1].payload.status == "cancelled"
    group, results = call_group(window_of(await saved_record(harness)))
    assert [call.id for call in group.tool_calls] == ["call-a", "call-b"]
    assert [result.tool_call_id for result in results] == ["call-a", "call-b"]
    assert "remembered" in results[0].content, "call-a's real result is kept"
    # The in-flight call carries the native interruption result, so no placeholder is added.
    assert results[1].content == "[Interrupted] Tool 'remember' execution was cancelled by user."
    assert SEAMS.WINDOW_PART_KEY not in (results[1].metadata or {})
    harness.tool_hooks.pop("gate")
    await next_task_sees(harness, spec, "remembered", "[Interrupted]")


@pytest.mark.asyncio
async def test_tool_facts_survive_an_error_while_writing_results(real_state):
    """PA-10-V-24: both tools finished; writing call-b's result fails, yet both real results are saved."""
    harness = real_state
    spec = harness.spec(rails=[RailSpec(type="test.entries.fail_writing_second")])
    ctx, _ = harness.context(spec)
    harness.model.turns = ["two_tools"]
    events = await collect(harness.api.execute_persistent_request(spec, ctx, request()))
    assert events[-1].payload.reason == "error"
    group, results = call_group(window_of(await saved_record(harness)))
    assert [result.tool_call_id for result in results] == ["call-a", "call-b"]
    assert all("remembered" in result.content for result in results), "the journal kept call-b's real result"
    assert PLACEHOLDER not in [result.content for result in results]
    clean = harness.spec()
    await next_task_sees(harness, clean, "remembered")


@pytest.mark.asyncio
@pytest.mark.parametrize("reason", ["stop", "cancel"])
async def test_parallel_tool_facts_survive_a_control_and_get_a_placeholder(real_state, reason):
    """PA-10-V-07 / V-14 / V-23 on the parallel path: the cancel leaves the batch and the abort cleanup keeps call-a."""
    harness = real_state
    harness.tool_hooks.update(parallel_safe=True, gate=asyncio.Event(), gate_call=2, gate_reached=asyncio.Event())
    spec = harness.spec()
    ctx, _ = harness.context(spec)
    harness.model.turns = ["two_tools"]
    consumer = asyncio.create_task(collect(harness.api.execute_persistent_request(spec, ctx, request())))
    await asyncio.wait_for(harness.tool_hooks["gate_reached"].wait(), 5)
    await harness.api.abort_persistent_request(ctx, reason=reason)
    await asyncio.wait_for(consumer, 5)
    group, results = call_group(window_of(await saved_record(harness)))
    assert [call.id for call in group.tool_calls] == ["call-a", "call-b"], "the group is kept, not dropped"
    assert "remembered" in results[0].content
    assert results[1].content == PLACEHOLDER
    assert results[1].metadata == {SEAMS.WINDOW_PART_KEY: "stopped_tool_placeholder"}
    harness.tool_hooks.pop("gate")
    await next_task_sees(harness, spec, "remembered", PLACEHOLDER)


@pytest.mark.asyncio
@pytest.mark.parametrize("reason", ["stop", "cancel"])
async def test_calls_refused_after_a_control_say_they_did_not_run(real_state, reason):
    """PA-10 Q-03: the control rail's refusal reads as a plain note, not as the internal stop signal."""
    harness = real_state
    spec = harness.spec()
    ctx, _ = harness.context(spec)
    harness.model.turns = ["two_tools"]
    harness.tool_hooks.update(gate=asyncio.Event(), gate_call=1, gate_reached=asyncio.Event())
    consumer = asyncio.create_task(collect(harness.api.execute_persistent_request(spec, ctx, request())))
    await asyncio.wait_for(harness.tool_hooks["gate_reached"].wait(), 5)
    await harness.api.abort_persistent_request(ctx, reason=reason)
    events = await asyncio.wait_for(consumer, 5)
    if reason == "stop":
        assert events[-1].payload.reason == "stopped"
    else:
        assert events[-1].payload.status == "cancelled"
    record = await saved_record(harness)
    _, results = call_group(window_of(record))
    assert results[0].content == "[Interrupted] Tool 'remember' execution was cancelled by user."
    assert results[1].content == SKIPPED and len(harness.tool_calls) == 1, "call-b was refused, not started"
    assert_kept_out(record, "persistent_pause", "execution is stopping")


@pytest.mark.asyncio
@pytest.mark.parametrize("cause", ["stop", "cancel", "critical"])
async def test_the_control_rail_refuses_every_call_with_the_same_note(cause):
    """A stop, a cancel and a critical failure share the refusal; a critical failure saves no record."""
    control = SimpleNamespace(emitter=object(), fatal=None, abort_reason=None)
    if cause == "critical":
        control.fatal = RuntimeError("critical record failed")
    else:
        control.abort_reason = cause
    rail = module("execution.model_rail").PersistentModelCallRail(SimpleNamespace(control=control))
    call = ToolCall(id="call-b", type="function", name="remember", arguments=TOOL_ARGS)
    inputs = ToolCallInputs(tool_call=call, tool_name="remember", tool_args=TOOL_ARGS)
    ctx = AgentCallbackContext(agent=None, inputs=inputs)
    await rail.before_tool_call(ctx)
    assert (
        ctx.inputs.tool_msg.content == "本次执行正在结束，该工具未调用。"
        and ctx.inputs.tool_msg.tool_call_id == "call-b"
    )
    assert ctx.consume_force_finish().result["result_type"] == "persistent_pause", "the entry still reads the signal"


@pytest.mark.asyncio
async def test_a_host_rail_blocking_a_result_keeps_its_note_and_ends_the_task(real_state):
    """The host contract: replace the output with a safe note, then request a forced finish."""
    harness = real_state
    spec = harness.spec(rails=[RailSpec(type="test.entries.host_output_check")])
    ctx, _ = harness.context(spec)
    harness.model.turns = ["tool"]
    harness.output_check["block"] = ("after_tool_call", "call-1")
    events = await collect(harness.api.execute_persistent_request(spec, ctx, request()))
    assert events[-1].payload.reason == "error" and harness.model.calls == 1, "the task ends at once"
    record = await saved_record(harness)
    messages = window_of(record)
    result = next(message for message in messages if getattr(message, "tool_call_id", None) == "call-1")
    assert result.content == BLOCKED
    assert_kept_out(record, "remembered")
    assert "结局：暂停（error）" in messages[-1].content


def assert_kept_out(record, *raw):
    text = json.dumps(record, ensure_ascii=False)
    for item in raw:
        assert item not in text, f"unprocessed content {item!r} reached the saved record"


async def stop_while_the_host_checks(harness, spec, reason):
    ctx, _ = harness.context(spec)
    consumer = asyncio.create_task(collect(harness.api.execute_persistent_request(spec, ctx, request())))
    await asyncio.wait_for(harness.output_check["reached"].wait(), 5)
    await harness.api.abort_persistent_request(ctx, reason=reason)
    events = await asyncio.wait_for(consumer, 5)
    if reason == "stop":
        assert events[-1].payload.reason == "stopped"
    else:
        assert events[-1].payload.status == "cancelled"
    harness.output_check.pop("hold")
    record = await saved_record(harness)
    return record, call_group(window_of(record))[1]


@pytest.mark.asyncio
@pytest.mark.parametrize("reason", ["stop", "cancel"])
async def test_a_result_cut_off_while_the_host_rail_checks_it_stays_out(real_state, reason):
    """PA-10 D-06 (2): call-a returned; the control arrived before the host rail finished checking it."""
    harness = real_state
    spec = harness.spec(rails=[RailSpec(type="test.entries.host_output_check")])
    harness.model.turns = ["two_tools"]
    harness.output_check["hold"] = ("after_tool_call", "call-a")
    record, results = await stop_while_the_host_checks(harness, spec, reason)
    assert results[0].content.startswith(RETURNED_NOTE), "the call returned; only that fact is kept"
    assert results[1].tool_call_id == "call-b" and len(harness.tool_calls) == 1, "call-b was refused, not started"
    assert_kept_out(record, "remembered")
    await next_task_sees(harness, spec, RETURNED_NOTE)
    assert not any("remembered" in str(content) for content in harness.model.seen[-1])


@pytest.mark.asyncio
async def test_an_error_cut_off_while_the_host_rail_checks_it_stays_out(real_state):
    """PA-10 D-06 (3): call-a raised; the stop arrived before the host rail finished with the error."""
    harness = real_state
    spec = harness.spec(rails=[RailSpec(type="test.entries.host_output_check")])
    harness.model.turns = ["two_tools"]
    harness.tool_hooks["fail_once"] = True
    harness.output_check["hold"] = ("on_tool_exception", "call-a")
    record, results = await stop_while_the_host_checks(harness, spec, "stop")
    assert results[0].content.startswith(RAISED_NOTE), "the call raised; it is not reported as finished"
    assert_kept_out(record, "temporary tool timeout", "remembered")


@pytest.mark.asyncio
@pytest.mark.parametrize("event", ["on_tool_exception", "after_tool_call"])
async def test_a_host_rail_failing_on_an_error_keeps_the_error_content_out(real_state, event):
    """PA-10 D-06 (3): call-a raised and the host rail aborted while handling the error; the wrapper only logs it."""
    harness = real_state
    spec = harness.spec(rails=[RailSpec(type="test.entries.host_output_check")])
    ctx, _ = harness.context(spec)
    harness.model.turns = ["two_tools"]
    harness.tool_hooks["fail_once"] = True
    harness.output_check["fail"] = (event, "call-a")
    events = await collect(harness.api.execute_persistent_request(spec, ctx, request()))
    assert events[-1].payload.status == "completed"
    record = await saved_record(harness)
    _, results = call_group(window_of(record))
    assert results[0].content.startswith(RAISED_NOTE), "not reported as finished, and without the error"
    assert results[1].content == CHECKED
    assert_kept_out(record, "temporary tool timeout", "remembered")


@pytest.mark.asyncio
async def test_a_host_rail_failing_on_a_result_keeps_the_raw_content_out(real_state):
    """PA-10 D-06 (2): the host rail aborted while checking call-a; call-b was checked normally."""
    harness = real_state
    spec = harness.spec(rails=[RailSpec(type="test.entries.host_output_check")])
    ctx, _ = harness.context(spec)
    harness.model.turns = ["two_tools"]
    harness.output_check["fail"] = ("after_tool_call", "call-a")
    events = await collect(harness.api.execute_persistent_request(spec, ctx, request()))
    assert events[-1].payload.status == "completed"
    record = await saved_record(harness)
    _, results = call_group(window_of(record))
    assert results[0].content.startswith(RETURNED_NOTE)
    assert results[1].content == CHECKED
    assert_kept_out(record, "remembered")


@pytest.mark.asyncio
async def test_a_checked_result_survives_a_later_write_failure(real_state):
    """PA-10 D-06 (1): both results were checked; writing call-b fails, and the checked result is saved."""
    harness = real_state
    spec = harness.spec(
        rails=[RailSpec(type="test.entries.host_output_check"), RailSpec(type="test.entries.fail_writing_second")]
    )
    ctx, _ = harness.context(spec)
    harness.model.turns = ["two_tools"]
    events = await collect(harness.api.execute_persistent_request(spec, ctx, request()))
    assert events[-1].payload.reason == "error"
    record = await saved_record(harness)
    _, results = call_group(window_of(record))
    assert [result.content for result in results] == [CHECKED, CHECKED]
    assert_kept_out(record, "remembered")


@pytest.mark.asyncio
@pytest.mark.parametrize("reason", ["stop", "cancel"])
async def test_refused_call_note_reaches_the_native_window(harness, reason):
    """PA-05 independently keeps the refusal note, with no PA-10 implementation installed."""
    spec = harness.spec()
    ctx, _ = harness.context(spec)
    harness.model.turns = ["two_tools"]
    harness.tool_hooks.update(gate=asyncio.Event(), gate_call=1, gate_reached=asyncio.Event())
    consumer = asyncio.create_task(collect(harness.api.execute_persistent_request(spec, ctx, request())))
    await asyncio.wait_for(harness.tool_hooks["gate_reached"].wait(), 5)
    await harness.api.abort_persistent_request(ctx, reason=reason)
    events = await asyncio.wait_for(consumer, 5)
    if reason == "stop":
        assert events[-1].payload.reason == "stopped"
    else:
        assert events[-1].payload.status == "cancelled"
    contexts = harness.store.record["state"]["context"]
    messages = next(iter(contexts.values()))["messages"]
    result = next(m for m in messages if isinstance(m, ToolMessage) and m.tool_call_id == "call-b")
    assert result.content == "本次执行正在结束，该工具未调用。"
    assert len(harness.tool_calls) == 1, "call-b was refused before invocation"
    assert "persistent_pause" not in result.content and "execution is stopping" not in result.content


# PA-08: listed files with a real DeepAgent, a temporary workspace and the formal share_file tool.


class WorkspaceAccess:
    def __init__(self, root):
        self.root = root

    async def resolve_read_scope(self, context):
        return WorkspaceReadScope(
            status="allowed",
            agent_id=context.agent_id,
            session_id=context.session_id,
            workspace_root=self.root,
            allowed_roots=(self.root,),
            private_sessions_root=self.root / "sessions",
            current_session_root=self.root / "sessions" / context.session_id,
            source_ref="workspace://business-agent",
            policy_revision=context.policy_revision,
        )


def shared(path, mode="upload", caption=None):
    arguments = {"file_path": path, "share_mode": mode}
    if caption is not None:
        arguments["caption"] = caption
    return ("share_file", json.dumps(arguments, ensure_ascii=False))


def digest(data):
    return "sha256:" + hashlib.sha256(data).hexdigest()


@pytest.fixture
def files(harness, tmp_path):
    root = tmp_path / "workspace"
    (root / "reports").mkdir(parents=True)
    (root / "reports" / "q3.md").write_bytes(b"third quarter")
    (root / "notes.txt").write_bytes(b"notes")
    harness.workspace = root
    harness.access = WorkspaceAccess(root)
    return harness


def file_spec(harness, **overrides):
    return harness.spec(
        enable_sys_operation=True, enabled_features=frozenset({"construction", "workspace_read"}), **overrides
    )


def on_second_call(harness, action):
    harness.model.on_call = lambda: action() if harness.model.calls == 2 else None


@pytest.mark.asyncio
async def test_listed_files_are_handed_off_after_the_answer_in_listed_order(files):
    """PA-08-V-01 / V-03 / V-05: the answer first, then each file in listed order, all to the reply target."""
    harness = files
    spec = file_spec(harness)
    ctx, _ = harness.context(spec, workspace_access=harness.access)
    harness.model.turns = [shared("notes.txt"), shared("reports/q3.md", "attachment", "第三季度"), "answer"]
    events = await collect(harness.api.execute_persistent_request(spec, ctx, request()))
    answer, notes, report = (event.payload for event in outputs(events))
    assert (answer.share_mode, answer.text) == ("message", "local answer")
    assert (notes.share_mode, notes.file_path, notes.file_size, notes.file_ref, notes.text) == (
        "upload",
        "notes.txt",
        5,
        digest(b"notes"),
        None,
    )
    assert (report.share_mode, report.file_path, report.text) == ("attachment", "reports/q3.md", "第三季度")
    assert {answer.target_ref, notes.target_ref, report.target_ref} == {"chat-1"}
    assert events[-1].payload.status == "completed"
    assert events[-1].payload.output_refs == (answer.output_id, notes.output_id, report.output_id)
    assert "tool.intent" not in kinds(events) and kinds(events).count("tool.completed") == 2
    assert harness.store.endings[-1].shared_files == (
        ("notes.txt", "upload", notes.output_id),
        ("reports/q3.md", "attachment", report.output_id),
    )


@pytest.mark.asyncio
async def test_a_file_is_handed_off_as_it_is_at_the_end_and_unlisted_files_never(files):
    """PA-08-V-04."""
    harness = files
    spec = file_spec(harness)
    ctx, _ = harness.context(spec, workspace_access=harness.access)
    (harness.workspace / "unlisted.md").write_bytes(b"draft")
    harness.model.turns = [shared("notes.txt"), "answer"]
    on_second_call(harness, lambda: (harness.workspace / "notes.txt").write_bytes(b"final notes"))
    events = await collect(harness.api.execute_persistent_request(spec, ctx, request()))
    handed = [event.payload for event in outputs(events) if event.payload.share_mode != "message"]
    assert [(item.file_path, item.file_ref, item.file_size) for item in handed] == [
        ("notes.txt", digest(b"final notes"), 11)
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("ending", ["error", "stopped", "cancelled"])
async def test_pauses_hand_off_listed_files_and_controls_hand_off_nothing(files, ending):
    """PA-08-V-05 / V-13: a stop or cancel accepted before the lock hands off nothing."""
    harness = files
    spec = file_spec(harness)
    ctx, _ = harness.context(spec, workspace_access=harness.access)
    harness.model.turns = [shared("notes.txt"), "fail" if ending == "error" else "answer"]
    if ending == "error":
        events = await collect(harness.api.execute_persistent_request(spec, ctx, request()))
        (handed,) = outputs(events)
        assert handed.payload.file_path == "notes.txt"
        paused = events[-1].payload
        assert (paused.reason, paused.output_refs) == ("error", (handed.payload.output_id,))
        return
    harness.model.release.clear()
    consumer = asyncio.create_task(collect(harness.api.execute_persistent_request(spec, ctx, request())))
    await asyncio.wait_for(harness.model.streaming.wait(), 5)
    await harness.api.abort_persistent_request(ctx, reason="stop" if ending == "stopped" else "cancel")
    events = await asyncio.wait_for(consumer, 5)
    assert outputs(events) == [] and events[-1].payload.output_refs == ()
    if ending == "stopped":
        assert events[-1].payload.reason == "stopped"
    else:
        assert events[-1].payload.status == "cancelled"


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["completed", "awaiting_confirmation"])
async def test_a_file_that_cannot_be_prepared_stops_the_whole_handoff(files, outcome):
    """PA-08-V-06: nothing is handed off, not even the answer; the pause says which file and why."""
    harness = files
    waiting = outcome == "awaiting_confirmation"
    spec = file_spec(harness, confirm=waiting)
    ctx, _ = harness.context(spec, workspace_access=harness.access)
    harness.model.turns = [shared("notes.txt"), "tool" if waiting else "answer"]
    on_second_call(harness, lambda: (harness.workspace / "notes.txt").unlink())
    events = await collect(harness.api.execute_persistent_request(spec, ctx, request()))
    assert outputs(events) == []
    paused = events[-1].payload
    assert "handoff not prepared: notes.txt: not_found" in paused.detail and paused.output_refs == ()
    ending = harness.store.endings[-1]
    assert ending.handoff_failure == "notes.txt: not_found" and ending.output_refs == ()
    if waiting:
        assert paused.reason == "awaiting_confirmation" and paused.intercepted, "the scene keeps waiting"
    else:
        assert paused.reason == "error" and ending.answer_text == "local answer", "kept for a later handoff"


@pytest.mark.asyncio
@pytest.mark.parametrize("changed", [False, True], ids=["unchanged", "changed"])
async def test_a_confirmation_resume_hands_off_the_files_of_its_chain(files, real_state, changed):
    """PA-08-V-11: listings before the pause are rebuilt; an unchanged file keeps its identity."""
    harness = files
    spec = file_spec(harness, confirm=True)
    harness.model.turns = [shared("reports/q3.md"), "tool", "answer"]
    ctx, _ = harness.context(spec, workspace_access=harness.access)
    events = await collect(harness.api.execute_persistent_request(spec, ctx, request()))
    (before,) = outputs(events)
    assert events[-1].payload.reason == "awaiting_confirmation"
    assert events[-1].payload.output_refs == (before.payload.output_id,), "a confirmation pause hands off files"
    if changed:
        (harness.workspace / "reports" / "q3.md").write_bytes(b"revised after confirmation")
    ctx, _ = harness.context(spec, execution_id="execution-2", workspace_access=harness.access)
    resumed = await collect(harness.api.resume_persistent_request(spec, ctx, permission("call-2")))
    answer, after = (event.payload for event in outputs(resumed))
    assert answer.share_mode == "message" and after.file_path == "reports/q3.md"
    assert (after.output_id == before.payload.output_id) is not changed, "a changed file is a new version"
    assert resumed[-1].payload.output_refs == (answer.output_id, after.output_id)


@pytest.mark.asyncio
async def test_long_confirmation_arguments_are_cut_but_the_resume_runs_the_full_call(harness):
    """PA-08-V-12 at the entry."""
    value = "v" * 300000
    spec = harness.spec(confirm=True)
    harness.model.turns = [("remember", json.dumps({"value": value}))]
    ctx, _ = harness.context(spec)
    events = await collect(harness.api.execute_persistent_request(spec, ctx, request()))
    paused = events[-1]
    assert paused.payload.reason == "awaiting_confirmation" and paused.proposal_size() <= 262144
    (call,) = paused.payload.intercepted
    assert call.arguments_truncated is True and json.dumps({"value": value}).startswith(call.arguments)
    ctx, _ = harness.context(spec, execution_id="execution-2")
    resumed = await collect(harness.api.resume_persistent_request(spec, ctx, permission("call-1")))
    assert resumed[-1].payload.status == "completed" and harness.tool_calls == [{"value": value}]


def intercepted_call(index, size):
    return {"tool_call_id": f"call-{index}", "tool_name": "remember", "arguments": json.dumps({"value": "x" * size})}


def test_confirmation_arguments_share_the_room_and_are_marked_when_cut():
    """PA-08-V-12: 64 calls fit one event; a short call keeps its arguments whole."""
    entries = module("execution.entries")
    events = module("schema.events")
    execution = module("schema.execution").ExecutionContext(
        namespace_id="tenant",
        agent_id="business-agent",
        session_id="session-a",
        request_id="request-1",
        execution_id="execution-1",
        config_version="v1",
        config_fingerprint="0" * 64,
        source_ref="source:message-1",
        principal_ref="user",
        startup_scope=frozenset({"read"}),
        source_scope=frozenset({"read"}),
        policy_revision="p1",
        capability_snapshot=frozenset({"construction"}),
    )
    context = SimpleNamespace(execution=execution)
    calls = tuple(intercepted_call(index, 20000) for index in range(63)) + (intercepted_call(63, 10),)
    paused = {"reason": "awaiting_confirmation", "intercepted": calls, "detail": None, "output_refs": ()}
    fitted = entries._fit_intercepted(context, paused)
    event = events.AgentEvent.create(
        execution,
        event_type=events.EventType.EXECUTION_PAUSED,
        local_seq=10**6,
        occurred_at=datetime.now(timezone.utc),
        payload=fitted,
    )
    assert event.proposal_size() <= events.EVENT_MAX_BYTES
    shown = event.payload.intercepted
    assert shown[-1].arguments == calls[-1]["arguments"] and shown[-1].arguments_truncated is False
    for original, call in zip(calls[:-1], shown[:-1]):
        assert call.arguments_truncated is True and original["arguments"].startswith(call.arguments)
    assert len({len(call.arguments) for call in shown[:-1]}) <= 2, "the room is shared evenly"

    whole = {**paused, "intercepted": (intercepted_call(0, 100),)}
    assert entries._fit_intercepted(context, whole) == whole, "arguments that fit are left as they are"
    long_text = {**paused, "intercepted": (intercepted_call(0, 70000),)}
    (call,) = entries._fit_intercepted(context, long_text)["intercepted"]
    assert call["arguments_truncated"] is True and len(call["arguments"]) == 65536, "the character limit too"


@pytest.mark.asyncio
@pytest.mark.parametrize("host", ["rewrites_and_ends", "rewrites", "fails_while_checking"])
async def test_a_listing_whose_result_a_host_rail_withheld_is_not_handed_off(files, real_state, host):
    """PA-08-V-17: the final window decides; a rewritten result or a note in its place lists nothing."""
    harness = files
    spec = file_spec(harness, rails=[RailSpec(type="test.entries.host_output_check")])
    ctx, _ = harness.context(spec, workspace_access=harness.access)
    if host == "rewrites_and_ends":
        harness.model.turns = [shared("notes.txt")]
        harness.output_check["block"] = ("after_tool_call", "call-1")
    elif host == "rewrites":
        harness.model.turns = [shared("notes.txt"), "answer"]
    else:
        # The host rail aborts while checking the result, so PA-10 keeps only a note; the run then fails.
        harness.model.turns = [shared("notes.txt"), "fail"]
        harness.output_check["fail"] = ("after_tool_call", "call-1")
    events = await collect(harness.api.execute_persistent_request(spec, ctx, request()))
    assert [event.payload.share_mode for event in outputs(events)] == (["message"] if host == "rewrites" else [])
    result = next(
        message
        for message in window_of(await saved_record(harness))
        if getattr(message, "tool_call_id", None) == "call-1"
    )
    expected = {"rewrites_and_ends": BLOCKED, "rewrites": CHECKED, "fails_while_checking": RETURNED_NOTE}[host]
    assert result.content.startswith(expected), "the window shows why nothing was listed"
    if host == "rewrites":
        assert events[-1].payload.status == "completed"
        assert events[-1].payload.output_refs == (outputs(events)[0].payload.output_id,)
    else:
        assert (events[-1].payload.reason, events[-1].payload.output_refs) == ("error", ())


class LateListing(DeepAgentRail):
    """Adds a listed share_file result to the live window after the native write-back, as an outer rail may."""

    def init(self, agent):
        super().init(agent)
        self.agent = agent

    async def after_invoke(self, ctx) -> None:
        handoff = module("execution.handoff")
        context = self.agent.react_agent.context_engine.get_context(session_id=ctx.session.get_session_id())
        call = ToolCall(id="late-1", type="function", name="share_file", arguments="{}")
        await context.add_messages(AssistantMessage(content="", tool_calls=[call]))
        listed = handoff.registration_result(handoff.Registration("notes.txt", "upload"))
        await context.add_messages(ToolMessage(content=listed, tool_call_id="late-1"))


@pytest.mark.asyncio
async def test_the_ending_reads_the_live_window_not_a_stale_snapshot(files, real_state, monkeypatch):
    """PA-08-V-17: the window is synced before the listings are read from it."""
    harness = files
    monkeypatch.setitem(_RAIL_PROVIDER_REGISTRY, "test.entries.late_listing", lambda params, context: LateListing())
    spec = file_spec(harness, rails=[RailSpec(type="test.entries.late_listing")])
    ctx, _ = harness.context(spec, workspace_access=harness.access)
    events = await collect(harness.api.execute_persistent_request(spec, ctx, request()))
    assert [event.payload.share_mode for event in outputs(events)] == ["message", "upload"]
    assert events[-1].payload.status == "completed"


@pytest.mark.asyncio
async def test_a_confirmation_chain_counts_its_listings_toward_the_limit(files, real_state, monkeypatch):
    """Q-01: the limit applies to the chain's listings merged with the resumed run's."""
    monkeypatch.setattr(module("execution.handoff"), "MAX_REGISTRATIONS", 1)
    harness = files
    spec = file_spec(harness, confirm=True)
    harness.model.turns = [shared("reports/q3.md"), "tool", shared("notes.txt"), "answer"]
    ctx, _ = harness.context(spec, workspace_access=harness.access)
    await collect(harness.api.execute_persistent_request(spec, ctx, request()))
    ctx, _ = harness.context(spec, execution_id="execution-2", workspace_access=harness.access)
    resumed = await collect(harness.api.resume_persistent_request(spec, ctx, permission("call-2")))
    assert [event.payload.file_path for event in outputs(resumed)] == [None, "reports/q3.md"]
    refused = next(
        message
        for message in window_of(await saved_record(harness))
        if getattr(message, "tool_call_id", None) == "call-3"
    )
    assert refused.content.startswith("本次交付最多登记"), "the resumed listing is refused at call time"


PARTICIPATION_ENDINGS = {
    "ignored": ("execution.final", "ignored"),
    "failed": ("execution.final", "failed"),
    "budget_denied": ("execution.paused", "budget_denied"),
    "admission_error": ("execution.paused", "error"),
}


def end_participation(harness, host, ending):
    if ending == "ignored":
        harness.model.judgments = [judgment("ignore")]
    elif ending == "failed":
        harness.model.judgments = ["not json"] * 3
    elif ending == "budget_denied":
        host.admission = "denied"
    else:
        host.admit_error = RuntimeError("budget port down")


def ending_value(event):
    return getattr(event.payload, "status", None) or event.payload.reason


@pytest.mark.asyncio
@pytest.mark.parametrize("ending", list(PARTICIPATION_ENDINGS))
async def test_a_participation_ending_is_retried_once_after_a_record_failure(harness, ending):
    """PA-08-V-07 on the participation path: the same event is recorded again; nothing reruns."""
    spec = harness.spec(system_prompt="你是团队里的调研专家。")
    ctx, host = harness.context(spec, group_chat=GroupChat((group_message(),)))
    end_participation(harness, host, ending)
    kind, value = PARTICIPATION_ENDINGS[ending]
    host.fail_once = {kind}
    events = await collect(harness.api.execute_persistent_request(spec, ctx, group_request()))
    assert (str(events[-1].event_type), ending_value(events[-1])) == (kind, value)
    recorded = [event for event in host.events if event.event_type == kind]
    assert recorded == [events[-1]], "recorded once, on the retry"
    assert ctx.binding.control.fatal is None
    assert harness.model.judge_calls == {"ignored": 1, "failed": 3}.get(ending, 0), "the judgment is not rerun"
    assert harness.model.calls == 0 and harness.store.loaded_marker == "never loaded"


@pytest.mark.asyncio
@pytest.mark.parametrize("ending", ["ignored", "budget_denied"])
async def test_a_participation_ending_that_fails_twice_stops_the_execution(harness, ending):
    spec = harness.spec(system_prompt="你是团队里的调研专家。")
    ctx, host = harness.context(spec, group_chat=GroupChat((group_message(),)))
    end_participation(harness, host, ending)
    kind, _ = PARTICIPATION_ENDINGS[ending]
    host.fail_record = {kind}
    with pytest.raises(BaseError) as exc_info:
        await collect(harness.api.execute_persistent_request(spec, ctx, group_request()))
    assert_rejected(exc_info, StatusCode.DEEPAGENT_RUNTIME_ERROR)
    assert kind not in kinds(host.events) and ctx.binding.control.fatal is not None
    assert harness.model.judge_calls == (1 if ending == "ignored" else 0) and harness.model.calls == 0


class RedactListingCaption(DeepAgentRail):
    """Acts as a host rail that changes a share_file result but keeps its success form."""

    def __init__(self, end):
        super().__init__()
        self.end = end

    async def after_tool_call(self, ctx) -> None:
        if ctx.inputs.tool_call.name != "share_file":
            return
        result = json.loads(ctx.inputs.tool_msg.content)
        content = json.dumps({**result, "caption": "redacted"}, ensure_ascii=False)
        ctx.inputs.tool_result = content
        ctx.inputs.tool_msg = ToolMessage(content=content, tool_call_id=ctx.inputs.tool_call.id)
        if self.end:
            ctx.request_force_finish({"output": "host withheld this listing", "result_type": "error"})


@pytest.mark.asyncio
@pytest.mark.parametrize("end", [True, False], ids=["ends", "continues"])
async def test_a_listing_a_host_rail_changed_in_the_same_form_is_not_handed_off(files, real_state, monkeypatch, end):
    """PA-08-V-17: the check value no longer matches, so the changed result lists nothing."""
    harness = files
    monkeypatch.setitem(
        _RAIL_PROVIDER_REGISTRY, "test.entries.redact_listing", lambda params, context: RedactListingCaption(end)
    )
    spec = file_spec(harness, rails=[RailSpec(type="test.entries.redact_listing")])
    ctx, _ = harness.context(spec, workspace_access=harness.access)
    harness.model.turns = [shared("notes.txt", caption="original caption"), "answer"]
    events = await collect(harness.api.execute_persistent_request(spec, ctx, request()))
    assert [event.payload.share_mode for event in outputs(events)] == ([] if end else ["message"])
    if end:
        assert (events[-1].payload.reason, events[-1].payload.output_refs) == ("error", ())
    else:
        assert events[-1].payload.status == "completed"
