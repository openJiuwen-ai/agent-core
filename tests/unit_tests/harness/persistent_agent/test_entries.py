# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Execution, resume and abort entries with a real DeepAgent, a local model and seam doubles.

The session state store and reply adapter doubles stand in for PA-09 / PA-08 so the
orchestration can be verified; they never replace the formal implementations.
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


def kinds(events):
    return [str(event.event_type) for event in events]


def usage(input_tokens, output_tokens):
    return UsageMetadata(input_tokens=input_tokens, output_tokens=output_tokens)


class LocalModel:
    """Streams one queued turn per call: an answer by default, or "tool" / "fail"."""

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
        if turn == "tool":
            call = ToolCall(id=f"call-{self.calls}", type="function", name="remember", arguments=TOOL_ARGS)
            yield AssistantMessageChunk(content="checking ", tool_calls=[call], usage_metadata=usage(3, 4))
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
    def __init__(self, calls):
        super().__init__(
            card=ToolCard(
                id="remember",
                name="remember",
                input_params={"type": "object", "properties": {"value": {"type": "string"}}},
            )
        )
        self.calls = calls

    async def invoke(self, inputs, **kwargs):
        self.calls.append(inputs)
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
    """PA-09 double: carries the whitelisted Session state and the waiting scene between Executions."""

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
            return None
        context.session.update_state(dict(self.record["state"]))
        waiting = self.record["ending"]
        return SEAMS.WaitingScene(waiting.request_id, waiting.reply_target)

    async def save(self, context, ending):
        if self.save_error is not None:
            raise self.save_error
        state = context.session.get_state()
        kept = {key: state[key] for key in STATE_KEYS if state.get(key) is not None}
        self.record = {"state": kept, "ending": ending}
        self.endings.append(ending)
        self.log.append("save")


class ReplyAdapter:
    """PA-08 double for permission replies only."""

    def to_native(self, response, request):
        return ConfirmPayload(approved=response.choice != "reject", feedback=response.feedback or "")


class Harness:
    def __init__(self, api, model, store, tool_calls):
        self.api = api
        self.model = model
        self.store = store
        self.tool_calls = tool_calls
        self.log = store.log

    def spec(self, confirm=False, **overrides):
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
        if confirm:
            fields["rails"] = [RailSpec(type="test.entries.confirm")]
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
    monkeypatch.setattr(ModelSpec, "build", lambda self: model)
    monkeypatch.setitem(_TOOL_PROVIDER_REGISTRY, "test.entries.remember", lambda params, context: Remember(tool_calls))
    monkeypatch.setitem(
        _RAIL_PROVIDER_REGISTRY, "test.entries.confirm", lambda params, context: ConfirmInterruptRail(["remember"])
    )
    monkeypatch.setattr(SEAMS, "session_state_store", lambda context: store)
    monkeypatch.setattr(SEAMS, "native_reply_adapter", ReplyAdapter)
    return Harness(api, model, store, tool_calls)


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
async def test_oversized_answer_pauses_instead_of_truncating(harness):
    spec = harness.spec()
    ctx, _ = harness.context(spec)
    harness.model.answer = "".join(f"{index:06d}" for index in range(11000))
    events = await collect(harness.api.execute_persistent_request(spec, ctx, request()))
    deltas = [event.payload.text for event in events if event.event_type == "reply.delta"]
    assert "".join(deltas) == "local " + harness.model.answer, "long chunks are split, not cut"
    assert "output.proposed" not in kinds(events)
    assert events[-1].payload.reason == "error" and "65536" in events[-1].payload.detail


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
        ({"seams": True}, StatusCode.DEEPAGENT_CONFIG_PARAM_ERROR),
    ],
)
@pytest.mark.asyncio
async def test_execute_rejects_before_any_event(harness, monkeypatch, change, code):
    spec = harness.spec(**change.get("spec", {}))
    ctx, host = harness.context(spec, **change.get("context", {}))
    if change.get("seams"):
        monkeypatch.setattr(SEAMS, "session_state_store", UNINTEGRATED_STORE)
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
