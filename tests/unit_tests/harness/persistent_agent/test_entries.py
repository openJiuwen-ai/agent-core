# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Execution, resume and abort entries with a real DeepAgent, a local model and seam doubles.

The session state store, window assembler and reply adapter doubles stand in for
PA-10 / PA-06 / PA-09 so the orchestration can be verified; they never replace the
formal implementations. Tests using the real_state fixture run the formal PA-10 store and
PA-06 assembler over an in-memory window backend shared by the Executions of one test.
"""

import asyncio
import json
from dataclasses import replace
from importlib import import_module
from types import SimpleNamespace

import pytest

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import BaseError
<<<<<<< HEAD
from openjiuwen.core.foundation.llm.schema.message import AssistantMessage, UsageMetadata, UserMessage
=======
from openjiuwen.core.foundation.llm.schema.message import AssistantMessage, ToolMessage, UsageMetadata
>>>>>>> 5800f8e1d (test(harness): cover tool calls refused by execution control)
from openjiuwen.core.foundation.llm.schema.message_chunk import AssistantMessageChunk
from openjiuwen.core.foundation.llm.schema.tool_call import ToolCall
from openjiuwen.core.foundation.store import InMemoryKVStore
from openjiuwen.core.foundation.tool import Tool, ToolCard
from openjiuwen.core.session.checkpointer import CheckpointerFactory, NoopCheckpointer
from openjiuwen.core.session.checkpointer.inmemory import InMemoryCheckpointer
from openjiuwen.core.single_agent import AgentCard, Session, create_agent_session
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext, ToolCallInputs
from openjiuwen.harness.deep_agent import DeepAgent
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


class LocalModel:
    """Streams one queued turn per call: an answer by default, or "tool" / "two_tools" / "fail"."""

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
            raise RuntimeError("local model failure")
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
    """Not parallel-safe, so several calls in one turn run one after another."""

    def __init__(self, calls, hooks, name="remember"):
        super().__init__(
            card=ToolCard(
                id=name,
                name=name,
                input_params={"type": "object", "properties": {"value": {"type": "string"}}},
                parallel_safe=False,
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
        self.admission = "admitted"
        self.admit_error = None
        self.report_error = None

    async def record(self, execution, event):
        if event.event_type in self.fail_record:
            raise RuntimeError("commit unavailable")
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
                model_client_config={
                    "client_provider": "OpenAI",
                    "api_key": "mock-api-key",
                    "api_base": "https://example.invalid/v1",
                }
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
        self, spec, *, execution_id="execution-1", request_id="request-1", isolated=True, group_chat=None, **changes
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
                commit=host, budget=host, window_backend=self.backend or InMemoryKVStore(), group_chat=group_chat
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
def harness(monkeypatch):
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
    monkeypatch.setattr(SEAMS, "window_assembler", lambda context: harness.assembler)
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


CAPACITY_DETAIL = "final answer exceeds the event size limit; kept in the session state record for handoff"


@pytest.mark.asyncio
@pytest.mark.parametrize("limit", ["characters", "event_bytes"])
async def test_oversized_answer_pauses_and_is_kept_for_handoff(harness, limit):
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
    assert len(deltas) >= 3
    assert_within_limits(events)
    assert "output.proposed" not in kinds(events)
    assert (events[-1].payload.reason, events[-1].payload.detail) == ("error", CAPACITY_DETAIL)
    ending = harness.store.endings[-1]
    assert (ending.outcome, ending.output_refs, ending.pending_answer) == ("error", (), answer)
    assert harness.log.index("save") < harness.log.index("execution.paused")
    assert [event.local_seq for event in host.events] == list(range(1, len(host.events) + 1))


@pytest.mark.asyncio
@pytest.mark.parametrize("rewritten", ["short rewritten answer", "oversized"])
async def test_answer_rewritten_in_after_invoke_is_the_one_checked(harness, rewritten):
    spec = harness.spec(rewrite=True)
    ctx, _ = harness.context(spec)
    harness.rewrite["output"] = emoji_text(65500) if rewritten == "oversized" else rewritten
    events = await collect(harness.api.execute_persistent_request(spec, ctx, request()))
    if rewritten == "oversized":
        assert events[-1].payload.detail == CAPACITY_DETAIL
        assert harness.store.endings[-1].pending_answer == harness.rewrite["output"]
    else:
        output = next(event for event in events if event.event_type == "output.proposed")
        assert output.payload.text == rewritten and events[-1].payload.status == "completed"


@pytest.mark.asyncio
async def test_state_save_failure_is_not_absorbed_by_capacity_handling(harness):
    spec = harness.spec()
    ctx, host = harness.context(spec)
    harness.model.answer = emoji_text(65500)
    harness.store.save_error = RuntimeError("window store down")
    with pytest.raises(BaseError) as exc_info:
        await collect(harness.api.execute_persistent_request(spec, ctx, request()))
    assert_rejected(exc_info, StatusCode.DEEPAGENT_RUNTIME_ERROR)
    assert not {"output.proposed", "execution.final", "execution.paused"} & set(kinds(host.events))


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
        ({"context": {"recovery_ref": "recovery-1"}}, StatusCode.DEEPAGENT_CONFIG_PARAM_ERROR),
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
async def test_mentioned_group_message_can_be_ignored_without_opening_a_task(harness):
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
    assert kinds(events) == ["model.completed", "participation.decided", "execution.final"]
    assert events[1].payload.decision == "ignore"
    assert events[1].payload.model_call_id == events[0].payload.model_call_id
    assert events[2].payload.status == "ignored" and events[2].payload.output_refs == ()
    assert "output.proposed" not in kinds(events) and "execution.started" not in kinds(events)
    assert harness.model.calls == 0 and harness.model.judge_calls == 1
    assert harness.model.judge_kwargs[0]["tools"] is None
    assert chat.calls == [(None, 8)]
    text = prompt_text(harness.model)
    assert "你是团队里的调研专家。" in text
    assert "被提及: 是" in text and "李四" in text and "att-1" in text and "reply_to: m-0" in text
    assert "EARLIER-CONTEXT" in text and "TRIGGER-SHOULD-BE-EXCLUDED" not in text
    assert harness.assembler.calls == [] and harness.store.loaded_marker == "never loaded"
    assert harness.store.endings == [] and not ctx.binding.attempted
    assert host.reports[0].status == "known"


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


<<<<<<< HEAD
# --- PA-10: the formal session state store ---------------------------------------------------------

STATE = module("execution.session_state")
RECORD = module("schema.session_record")
PLACEHOLDER = "结果未取得，实际效果需核实"


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
        "【执行结束】request_id：request-1；execution_id：execution-1；结局：已完成；交出候选：out_"
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
async def test_pending_answer_is_saved_verbatim(real_state):
    """PA-10-V-13: kept under the current contract until PA-08 settles its lifecycle."""
    harness = real_state
    harness.model.answer = emoji_text(65500)
    spec = harness.spec()
    ctx, _ = harness.context(spec)
    events = await collect(harness.api.execute_persistent_request(spec, ctx, request()))
    assert events[-1].payload.detail == CAPACITY_DETAIL
    assert (await saved_record(harness))["ending"]["pending_answer"] == "local " + harness.model.answer


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
=======
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
>>>>>>> 5800f8e1d (test(harness): cover tool calls refused by execution control)
