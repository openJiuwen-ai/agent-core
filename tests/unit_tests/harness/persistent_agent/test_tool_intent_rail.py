"""Behavior tests for the Persistent write-intent execution barrier."""

import asyncio
from importlib import import_module
from types import SimpleNamespace

import pytest

from openjiuwen.core.common.exception.errors import BaseError
from openjiuwen.core.foundation.llm.schema.tool_call import ToolCall
from openjiuwen.core.foundation.tool import ToolCard
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext, AgentCallbackEvent, ToolCallInputs
from openjiuwen.harness.persistent_agent.persistent_agent.execution.emitter import EventEmitter
from openjiuwen.harness.persistent_agent.persistent_agent.schema.build_context import ConstructionBinding
from openjiuwen.harness.persistent_agent.persistent_agent.schema.events import EventType
from openjiuwen.harness.persistent_agent.persistent_agent.schema.execution import ExecutionContext
from openjiuwen.harness.rails.tool_call_resilience_rail import ToolCallResilienceRail

pytestmark = pytest.mark.level0

_RAIL_MODULE = "openjiuwen.harness.persistent_agent.persistent_agent.execution.tool_intent_rail"


def _rail(binding):
    try:
        rail_module = import_module(_RAIL_MODULE)
    except ModuleNotFoundError as exc:
        if exc.name == _RAIL_MODULE:
            pytest.fail("PersistentToolIntentRail is not implemented")
        raise
    return rail_module.PersistentToolIntentRail(binding)


def _execution(*, request_id="request-1", execution_id="execution-1"):
    return ExecutionContext(
        namespace_id="tenant",
        agent_id="agent-a",
        session_id="session-a",
        request_id=request_id,
        execution_id=execution_id,
        source_ref="source:message-1",
        config_version="v1",
        config_fingerprint="a" * 64,
        principal_ref="principal-a",
        startup_scope=frozenset({"read"}),
        source_scope=frozenset({"read"}),
        policy_revision="policy-1",
        capability_snapshot=frozenset({"construction"}),
    )


class RecordingCommit:
    def __init__(self, *, hold_intent=False, fail_on=None):
        self.events = []
        self.fail_on = fail_on
        self.intent_started = asyncio.Event()
        self.release_intent = asyncio.Event()
        if not hold_intent:
            self.release_intent.set()

    async def record(self, execution, event):
        del execution
        if event.event_type == EventType.TOOL_INTENT:
            self.intent_started.set()
            await self.release_intent.wait()
        if event.event_type == self.fail_on:
            raise RuntimeError("host persistence failed")
        self.events.append(event)


def _binding(*, execution=None, commit=None, attach_emitter=True):
    execution = execution or _execution()
    commit = commit or RecordingCommit()
    binding = ConstructionBinding(execution=execution, ports=SimpleNamespace(commit=commit))
    if attach_emitter:
        binding.control.emitter = EventEmitter(execution, commit, binding.control)
    return binding, commit


def _context(tool_name, *, tool_call_id="call-1", args='{"path":"notes.txt"}', extra=None, result=None, exception=None):
    call = ToolCall(id=tool_call_id, type="function", name=tool_name, arguments=args)
    return AgentCallbackContext(
        agent=SimpleNamespace(ability_manager=None),
        inputs=ToolCallInputs(
            tool_call=call,
            tool_name=tool_name,
            tool_args=args,
            tool_result=result,
        ),
        extra=dict(extra or {}),
        exception=exception,
    )


@pytest.mark.asyncio
async def test_read_only_dispatch_emits_completion_without_intent_and_redacts_data():
    binding, commit = _binding()
    rail = _rail(binding)
    ctx = _context("read_file", args='{"path":"notes.txt","api_token":"arg-secret"}')

    await rail.before_tool_call(ctx)
    ctx.inputs.tool_result = {"text": "hello", "password": "result-secret"}
    await rail.after_tool_call(ctx)

    assert [event.event_type for event in commit.events] == [EventType.TOOL_COMPLETED]
    completed = commit.events[0].payload
    assert completed.operation_id is None
    assert completed.status == "succeeded"
    assert "arg-secret" not in completed.args_summary
    assert "result-secret" not in completed.result_summary


@pytest.mark.asyncio
async def test_write_dispatch_waits_until_intent_is_durably_recorded():
    commit = RecordingCommit(hold_intent=True)
    binding, _ = _binding(commit=commit)
    rail = _rail(binding)
    ctx = _context("remember", args='{"value":"x","api_token":"arg-secret"}')
    dispatched = asyncio.Event()

    async def guarded_dispatch():
        await rail.before_tool_call(ctx)
        dispatched.set()

    dispatch_task = asyncio.create_task(guarded_dispatch())
    await asyncio.wait_for(commit.intent_started.wait(), timeout=2)
    await asyncio.sleep(0)
    assert not dispatched.is_set()
    assert commit.events == []

    commit.release_intent.set()
    await asyncio.wait_for(dispatch_task, timeout=2)
    assert dispatched.is_set()
    assert [event.event_type for event in commit.events] == [EventType.TOOL_INTENT]
    intent = commit.events[0].payload
    assert intent.tool_call_id == "call-1"
    assert intent.tool_name == "remember"
    assert len(intent.params_digest) == 64
    assert "arg-secret" not in intent.args_summary


@pytest.mark.asyncio
async def test_operation_id_is_stable_for_same_request_across_executions():
    intents = []
    for execution_id in ("execution-1", "execution-2"):
        binding, commit = _binding(execution=_execution(execution_id=execution_id))
        rail = _rail(binding)
        await rail.before_tool_call(_context("remember"))
        intents.append(next(event.payload for event in commit.events if event.event_type == EventType.TOOL_INTENT))

    assert intents[0].operation_id == intents[1].operation_id


@pytest.mark.asyncio
async def test_reentering_same_write_call_reuses_intent_without_duplicate_record():
    binding, commit = _binding()
    rail = _rail(binding)
    ctx = _context("remember")

    await rail.before_tool_call(ctx)
    await rail.before_tool_call(ctx)

    assert [event.event_type for event in commit.events] == [EventType.TOOL_INTENT]


@pytest.mark.asyncio
async def test_retry_with_reordered_json_arguments_reuses_the_same_digest():
    binding, commit = _binding()
    rail = _rail(binding)
    ctx = _context("remember", args='{"first":1,"second":2}')

    await rail.before_tool_call(ctx)
    ctx.inputs.tool_args = '{"second": 2, "first": 1}'
    await rail.before_tool_call(ctx)

    assert binding.control.fatal is None
    assert [event.event_type for event in commit.events] == [EventType.TOOL_INTENT]


@pytest.mark.asyncio
async def test_retry_with_changed_arguments_is_fatal_and_does_not_record_another_intent():
    binding, commit = _binding()
    rail = _rail(binding)
    ctx = _context("remember")
    await rail.before_tool_call(ctx)

    ctx.inputs.tool_args = '{"path":"different.txt"}'
    with pytest.raises(BaseError):
        await rail.before_tool_call(ctx)

    assert binding.control.fatal is not None
    assert [event.event_type for event in commit.events] == [EventType.TOOL_INTENT]


@pytest.mark.asyncio
async def test_retry_with_changed_tool_name_is_fatal_and_does_not_record_another_intent():
    binding, commit = _binding()
    rail = _rail(binding)
    ctx = _context("remember")
    await rail.before_tool_call(ctx)

    ctx.inputs.tool_name = "renamed_tool"
    ctx.inputs.tool_call.name = "renamed_tool"
    with pytest.raises(BaseError):
        await rail.before_tool_call(ctx)

    assert binding.control.fatal is not None
    assert [event.event_type for event in commit.events] == [EventType.TOOL_INTENT]


@pytest.mark.asyncio
async def test_missing_emitter_blocks_write_but_allows_allowlisted_read_without_events():
    write_binding, write_commit = _binding(attach_emitter=False)
    with pytest.raises(BaseError):
        await _rail(write_binding).before_tool_call(_context("remember"))
    assert write_binding.control.fatal is not None
    assert write_commit.events == []

    read_binding, read_commit = _binding(attach_emitter=False)
    read_rail = _rail(read_binding)
    read_ctx = _context("read_file")
    await read_rail.before_tool_call(read_ctx)
    await read_rail.after_tool_call(read_ctx)
    assert read_commit.events == []


@pytest.mark.asyncio
async def test_missing_call_identity_fails_closed_even_for_read_only_tool():
    binding, commit = _binding()
    ctx = _context("read_file", tool_call_id=None)

    with pytest.raises(BaseError):
        await _rail(binding).before_tool_call(ctx)

    assert binding.control.fatal is not None
    assert commit.events == []


@pytest.mark.asyncio
async def test_unprocessable_deep_arguments_fail_closed_before_intent():
    binding, commit = _binding()
    rail = _rail(binding)
    deeply_nested = "[" * 1100 + "0" + "]" * 1100
    ctx = _context("remember", args=deeply_nested)

    with pytest.raises(BaseError):
        await rail.before_tool_call(ctx)

    assert binding.control.fatal is not None
    assert ctx.has_force_finish_request
    assert commit.events == []


@pytest.mark.asyncio
async def test_intent_record_failure_is_fatal_and_prevents_dispatch():
    commit = RecordingCommit(fail_on=EventType.TOOL_INTENT)
    binding, _ = _binding(commit=commit)
    dispatched = False

    with pytest.raises(BaseError):
        await _rail(binding).before_tool_call(_context("remember"))
    assert binding.control.fatal is not None
    assert not dispatched
    assert commit.events == []


@pytest.mark.asyncio
async def test_sibling_waiting_to_record_intent_does_not_dispatch_after_fatal_failure():
    class FirstIntentFailsCommit:
        def __init__(self):
            self.events = []
            self.intent_count = 0
            self.first_started = asyncio.Event()
            self.release_first = asyncio.Event()

        async def record(self, _execution, event):
            if event.event_type == EventType.TOOL_INTENT:
                self.intent_count += 1
                if self.intent_count == 1:
                    self.first_started.set()
                    await self.release_first.wait()
                    raise RuntimeError("first intent write failed")
            self.events.append(event)

    commit = FirstIntentFailsCommit()
    binding, _ = _binding(commit=commit)
    rail = _rail(binding)
    contexts = [_context("write_one", tool_call_id="call-1"), _context("write_two", tool_call_id="call-2")]
    dispatched = []

    async def invoke(ctx):
        try:
            await rail.before_tool_call(ctx)
        except BaseError:
            return
        if not ctx.has_force_finish_request:
            dispatched.append(ctx.inputs.tool_call.id)

    first = asyncio.create_task(invoke(contexts[0]))
    await asyncio.wait_for(commit.first_started.wait(), timeout=2)
    second = asyncio.create_task(invoke(contexts[1]))
    await asyncio.sleep(0)
    commit.release_first.set()
    await asyncio.gather(first, second)

    assert binding.control.fatal is not None
    assert dispatched == []
    assert contexts[1].has_force_finish_request
    assert len(commit.events) == 1 and commit.events[0].event_type == EventType.TOOL_INTENT
    assert commit.events[0].payload.tool_call_id == "call-2"


@pytest.mark.parametrize("extra", [{"_skip_tool": True}, {"_skip_tool_calls": {"call-1": True}}])
@pytest.mark.asyncio
async def test_skipped_or_force_finished_call_emits_no_tool_events(extra):
    binding, commit = _binding()
    rail = _rail(binding)
    ctx = _context("remember", extra=extra)

    await rail.before_tool_call(ctx)
    await rail.after_tool_call(ctx)

    assert commit.events == []


@pytest.mark.asyncio
async def test_force_finished_call_emits_no_tool_events():
    binding, commit = _binding()
    rail = _rail(binding)
    ctx = _context("remember")
    ctx.request_force_finish({"result_type": "blocked"})

    await rail.before_tool_call(ctx)
    await rail.after_tool_call(ctx)

    assert commit.events == []


@pytest.mark.asyncio
async def test_uncertain_write_exception_emits_unknown_completion():
    binding, commit = _binding()
    rail = _rail(binding)
    ctx = _context("remember")
    await rail.before_tool_call(ctx)
    ctx.exception = TimeoutError("connection timed out")
    await rail.on_tool_exception(ctx)
    await rail.after_tool_call(ctx)

    completed = next(event.payload for event in commit.events if event.event_type == EventType.TOOL_COMPLETED)
    assert completed.status == "unknown"
    assert completed.operation_id is not None
    assert completed.reason
    assert completed.result_summary


@pytest.mark.asyncio
async def test_exception_completion_summary_never_includes_exception_or_error_text():
    binding, commit = _binding()
    rail = _rail(binding)
    ctx = _context("remember")
    await rail.before_tool_call(ctx)
    ctx.exception = TimeoutError("api_token=exception-secret")
    ctx.inputs.tool_result = {"success": False, "error": "api_token=result-secret"}

    await rail.after_tool_call(ctx)

    completed = next(event.payload for event in commit.events if event.event_type == EventType.TOOL_COMPLETED)
    assert completed.status == "unknown"
    assert "exception-secret" not in completed.result_summary
    assert "result-secret" not in completed.result_summary


@pytest.mark.asyncio
async def test_explicit_business_failure_emits_failed_completion():
    binding, commit = _binding()
    rail = _rail(binding)
    ctx = _context("remember", result={"success": False, "error": "invalid input"})
    await rail.before_tool_call(ctx)
    await rail.after_tool_call(ctx)

    completed = next(event.payload for event in commit.events if event.event_type == EventType.TOOL_COMPLETED)
    assert completed.status == "failed"
    assert completed.reason
    assert completed.result_summary


@pytest.mark.asyncio
async def test_unserializable_result_still_emits_bounded_completion_summary():
    binding, commit = _binding()
    rail = _rail(binding)
    ctx = _context("remember")
    await rail.before_tool_call(ctx)
    cyclic_result = {}
    cyclic_result["self"] = cyclic_result
    ctx.inputs.tool_result = cyclic_result

    await rail.after_tool_call(ctx)

    completed = next(event.payload for event in commit.events if event.event_type == EventType.TOOL_COMPLETED)
    assert completed.status == "succeeded"
    assert completed.result_summary == "Tool result summary unavailable"
    assert len(completed.result_summary) <= 500


@pytest.mark.asyncio
async def test_event_argument_and_result_summaries_are_bounded():
    binding, commit = _binding()
    rail = _rail(binding)
    oversized = "x" * 1200
    ctx = _context("remember", args='{"content":"' + oversized + '"}')

    await rail.before_tool_call(ctx)
    ctx.inputs.tool_result = {"content": oversized}
    await rail.after_tool_call(ctx)

    intent, completed = (event.payload for event in commit.events)
    assert len(intent.args_summary) <= 500
    assert len(completed.args_summary) <= 500
    assert len(completed.result_summary) <= 500


@pytest.mark.asyncio
async def test_completion_record_failure_does_not_change_tool_result():
    commit = RecordingCommit(fail_on=EventType.TOOL_COMPLETED)
    binding, _ = _binding(commit=commit)
    rail = _rail(binding)
    result = {"success": True, "message": "done"}
    ctx = _context("read_file", result=result)
    await rail.before_tool_call(ctx)

    await rail.after_tool_call(ctx)

    assert ctx.inputs.tool_result is result
    assert binding.control.fatal is None


def _retry_case(card):
    binding, _ = _binding()
    rail = _rail(binding)
    ctx = _context("remember")
    ctx.agent.ability_manager = SimpleNamespace(get=lambda tool_name: card)
    ctx.exception = TimeoutError("connection timed out")
    return binding, rail, ctx


@pytest.mark.asyncio
async def test_explicitly_idempotent_tool_keeps_retry_requested_by_resilience_rail():
    card = ToolCard(id="remember-id", name="remember", description="test", idempotent=True)
    _, rail, ctx = _retry_case(card)
    resilience = ToolCallResilienceRail()

    await resilience.on_tool_exception(ctx)
    await rail.on_tool_exception(ctx)

    assert ctx.consume_retry_request() is not None


@pytest.mark.asyncio
async def test_non_idempotent_card_cannot_retain_a_retry_requested_by_another_rail():
    card = ToolCard(id="remember-id", name="remember", description="test", idempotent=False)
    _, rail, ctx = _retry_case(card)
    ctx.request_retry()

    await rail.on_tool_exception(ctx)

    assert ctx.consume_retry_request() is None


@pytest.mark.asyncio
async def test_missing_tool_card_cannot_retain_resilience_retry():
    _, rail, ctx = _retry_case(None)
    await ToolCallResilienceRail().on_tool_exception(ctx)
    assert ctx.consume_retry_request() is not None, "the shared rail currently allows unknown cards to retry"

    ctx.request_retry()
    await rail.on_tool_exception(ctx)

    assert ctx.consume_retry_request() is None


@pytest.mark.asyncio
async def test_non_toolcard_ability_cannot_retain_resilience_retry():
    _, rail, ctx = _retry_case(object())
    await ToolCallResilienceRail().on_tool_exception(ctx)
    assert ctx.consume_retry_request() is not None

    ctx.request_retry()
    await rail.on_tool_exception(ctx)

    assert ctx.consume_retry_request() is None


@pytest.mark.asyncio
async def test_fatal_execution_clears_even_an_idempotent_retry_request():
    binding, rail, ctx = _retry_case(ToolCard(id="remember-id", name="remember", description="test", idempotent=True))
    binding.control.mark_fatal(RuntimeError("intent persistence failed"))
    await ToolCallResilienceRail().on_tool_exception(ctx)
    await rail.on_tool_exception(ctx)

    assert ctx.consume_retry_request() is None


def test_persistent_exception_guard_runs_after_shared_resilience_policy():
    rail = _rail(_binding()[0])
    resilience = ToolCallResilienceRail()

    assert rail.callback_priority(AgentCallbackEvent.ON_TOOL_EXCEPTION) < resilience.callback_priority(
        AgentCallbackEvent.ON_TOOL_EXCEPTION
    )
