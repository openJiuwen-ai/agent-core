# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Execution, resume and abort entries with a real DeepAgent, a local model and seam doubles.

The session state store, window assembler and reply adapter doubles stand in for
PA-10 / PA-06 / PA-09 so the orchestration can be verified; they never replace the
formal implementations.
"""

import asyncio
from dataclasses import replace
from importlib import import_module

import pytest

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import BaseError
from openjiuwen.core.foundation.llm.schema.message import UsageMetadata
from openjiuwen.core.foundation.llm.schema.message_chunk import AssistantMessageChunk
from openjiuwen.core.foundation.llm.schema.tool_call import ToolCall
from openjiuwen.core.foundation.store import InMemoryKVStore
from openjiuwen.core.foundation.tool import Tool, ToolCard
from openjiuwen.core.session.checkpointer import CheckpointerFactory, NoopCheckpointer
from openjiuwen.core.session.checkpointer.inmemory import InMemoryCheckpointer
from openjiuwen.core.single_agent import AgentCard, Session, create_agent_session
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
    return import_module(f"{PKG}.persistent_agent.{name}")


SEAMS = module("execution.seams")
UNINTEGRATED_STORE, UNINTEGRATED_ADAPTER = SEAMS.session_state_store, SEAMS.native_reply_adapter
UNINTEGRATED_ASSEMBLER = SEAMS.window_assembler


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
        self.answer = "answer"
        self.with_usage = True
        self.release = asyncio.Event()
        self.release.set()
        self.streaming = asyncio.Event()
        self.cancelled = asyncio.Event()
        self.on_call = None

    async def invoke(self, messages, **kwargs):
        raise AssertionError("entries always stream the native agent")

    async def stream(self, messages, **kwargs):
        self.calls += 1
        if self.on_call is not None:
            self.on_call()
        turn = self.turns.pop(0) if self.turns else "answer"
        if turn == "fail":
            raise RuntimeError("local model failure")
        if turn in ("tool", "two_tools"):
            ids = [f"call-{self.calls}"] if turn == "tool" else ["call-a", "call-b"]
            calls = [ToolCall(id=tool_id, type="function", name="remember", arguments=TOOL_ARGS) for tool_id in ids]
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

    def __init__(self, calls, hooks):
        super().__init__(
            card=ToolCard(
                id="remember",
                name="remember",
                input_params={"type": "object", "properties": {"value": {"type": "string"}}},
                parallel_safe=False,
            )
        )
        self.calls = calls
        self.hooks = hooks

    async def invoke(self, inputs, **kwargs):
        self.calls.append(inputs)
        if self.hooks.get("on_invoke") is not None:
            self.hooks["on_invoke"]()
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

    def context(self, spec, *, execution_id="execution-1", request_id="request-1", isolated=True, **changes):
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
            session=create_agent_session(session_id="session-a", card=spec.card, checkpointer=checkpointer),
            ports=self.api.PersistentPorts(commit=host, budget=host, window_backend=InMemoryKVStore()),
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
        lambda params, context: Remember(tool_calls, harness.tool_hooks),
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
    paused = events[-1].payload
    assert paused.reason == "awaiting_confirmation" and paused.detail is None
    call = paused.intercepted[0]
    assert (call.tool_call_id, call.tool_name) == ("call-1", "remember")
    assert "secret-value" not in call.arguments and '"api_token": "***"' in call.arguments
    assert harness.tool_calls == [], "the intercepted tool has not run"

    ctx, host = harness.context(spec, execution_id="execution-2")
    resumed = await collect(harness.api.resume_persistent_request(spec, ctx, permission()))
    assert kinds(resumed) == ["reply.delta", "reply.delta", "model.completed", "output.proposed", "execution.final"]
    assert resumed == host.events
    assert harness.tool_calls == [{"value": "x", "api_token": "secret-value"}]
    assert resumed[-2].payload.target_ref == "chat-1", "the reply target comes from the waiting scene"
    assert resumed[-1].payload.status == "completed"


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
        ({"seams": "session_state_store"}, StatusCode.DEEPAGENT_CONFIG_PARAM_ERROR),
        ({"seams": "window_assembler"}, StatusCode.DEEPAGENT_CONFIG_PARAM_ERROR),
    ],
)
@pytest.mark.asyncio
async def test_execute_rejects_before_any_event(harness, monkeypatch, change, code):
    spec = harness.spec(**change.get("spec", {}))
    ctx, host = harness.context(spec, **change.get("context", {}))
    unintegrated = {"session_state_store": UNINTEGRATED_STORE, "window_assembler": UNINTEGRATED_ASSEMBLER}
    if change.get("seams"):
        monkeypatch.setattr(SEAMS, change["seams"], unintegrated[change["seams"]])
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
