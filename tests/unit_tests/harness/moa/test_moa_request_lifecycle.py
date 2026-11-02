# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Proposal ownership through real passive interaction and request boundaries."""

from __future__ import annotations

import asyncio
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest

from openjiuwen.core.common.exception.errors import BaseError
from openjiuwen.core.foundation.llm import AssistantMessage
from openjiuwen.core.foundation.llm.schema.message_chunk import AssistantMessageChunk
from openjiuwen.core.foundation.llm.schema.tool_call import ToolCall
from openjiuwen.core.foundation.tool import Tool, ToolCard
from openjiuwen.core.runner import Runner
from openjiuwen.core.session.agent import Session
from openjiuwen.core.session import InteractiveInput
from openjiuwen.core.single_agent.rail.base import ModelCallInputs
from openjiuwen.harness.moa import MoAConfig, VerifierConfig
from openjiuwen.harness.moa.consultation import prepare_reference
from openjiuwen.harness.moa.integration import MoAProposalOwner
from openjiuwen.harness.moa.proposal import ProposalModel
from openjiuwen.harness.moa.state import MoAState, ProposalBatch, ProposalSource, ProposalStore, active_moa
from openjiuwen.harness.rails.interrupt.confirm_rail import ConfirmInterruptRail
from openjiuwen.harness.rails.moa_rail import MoARail
from openjiuwen.harness.schema.interaction import RoundWorkItem, SendInputRequest
from openjiuwen.harness.schema.task import TaskPlan, TodoItem, TodoStatus
from tests.unit_tests.harness.moa.helpers import Echo, agent, model
from tests.unit_tests.harness.moa.test_moa_proposal import FixedBudgetContext, request_messages


@pytest.mark.asyncio
@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("conversation_id", [None, "moa-loop-missing-session"])
async def test_task_loop_without_session_retains_framework_error(stream, conversation_id):
    primary_model = model()
    primary_model.invoke = AsyncMock()
    reference = model()
    reference.invoke = AsyncMock()
    deep = agent(primary_model, MoAConfig(
        mode="verifier_routed", reference_models=(reference,), scorer=lambda *_: 0.5,
    ), enable_task_loop=True)
    inputs = {"query": "task", "conversation_id": conversation_id}

    with pytest.raises(BaseError, match="session is required for task-loop mode"):
        if stream:
            _ = [chunk async for chunk in deep.stream(inputs)]
        else:
            await deep.invoke(inputs)

    primary_model.invoke.assert_not_awaited()
    reference.invoke.assert_not_awaited()
    assert active_moa.get() is None
    assert not deep._invoke_active
    assert deep._proposal_owner()._bindings == {}
    assert deep._proposal_owner()._scopes == {}


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["epoch", "replacement", "closed"])
async def test_model_callback_rechecks_state_after_skill_validation(monkeypatch, change):
    reference = model()
    reference.invoke = AsyncMock()
    rail = MoARail(MoAConfig(
        mode="verifier_routed", reference_models=(reference,), scorer=lambda *_: 0.5,
    ))
    state = MoAState()
    started, release = asyncio.Event(), asyncio.Event()
    host = SimpleNamespace(get_llm=Mock(), set_llm=Mock())
    ctx = SimpleNamespace(inputs=ModelCallInputs(), session=None, extra={}, context=None, agent=host)

    async def validate(_ctx):
        started.set()
        await release.wait()
        if change == "replacement":
            active_moa.set(MoAState())

    monkeypatch.setattr(rail, "_validate_skill_callback", validate)
    token = active_moa.set(state)
    try:
        task = asyncio.create_task(rail.before_model_call(ctx))
        await asyncio.wait_for(started.wait(), timeout=2)
        if change == "epoch":
            state.proposals.invalidate("new_input")
        elif change == "closed":
            state.proposals.close("task_end")
        release.set()
        await asyncio.wait_for(task, timeout=2)
    finally:
        release.set()
        active_moa.reset(token)

    assert state.active_key is None
    assert ctx.extra == {}
    host.get_llm.assert_not_called()
    host.set_llm.assert_not_called()
    reference.invoke.assert_not_awaited()


@pytest.mark.asyncio
async def test_ready_refresh_does_not_replace_active_during_same_decision_retry():
    key = (object(), 2)
    first = ProposalBatch((ProposalSource(1, "active-A"),), key)
    ready = ProposalBatch((ProposalSource(1, "ready-B"),), (key[0], 3), origin=key)
    store = ProposalStore(active=first, ready=ready)
    state = MoAState(proposals=store, active_key=key, context=FixedBudgetContext())
    primary_model = model()
    primary_model.invoke = AsyncMock(return_value=AssistantMessage(content="ok"))
    wrapper = ProposalModel(primary_model)
    token = active_moa.set(state)
    try:
        await wrapper.invoke(request_messages())
        await wrapper.invoke(request_messages())
        assert store.active is first and store.ready is ready
        state.active_key = (object(), 1)
        await wrapper.invoke(request_messages())
    finally:
        active_moa.reset(token)
    requests = [str(call.kwargs["messages"]) for call in primary_model.invoke.await_args_list]
    assert ["active-A" in request for request in requests] == [True, True, False]
    assert ["ready-B" in request for request in requests] == [False, False, True]
    assert store.active is ready and store.ready is None
    assert ready.target == (key[0], 3)
    assert ready.attempts[0].key == state.active_key


@pytest.mark.asyncio
@pytest.mark.parametrize("outputs", [("new-B", RuntimeError("offline")), (" ", ""), (RuntimeError("offline"), "")])
async def test_reference_refresh_partial_success_replaces_and_empty_failure_preserves(outputs):
    key = (object(), 1)
    old = ProposalBatch((ProposalSource(1, "old-A"),), key)
    state = MoAState(proposals=ProposalStore(active=old), active_key=key)
    references = []
    for output in outputs:
        reference = model()
        reference.invoke = AsyncMock(side_effect=output) if isinstance(output, Exception) else AsyncMock(
            return_value=AssistantMessage(content=output),
        )
        references.append(reference)
    config = MoAConfig(reference_models=tuple(references))
    count = await prepare_reference(config, state, request_messages(), (key[0], 2))
    assert state.reference_calls == 1
    assert state.proposals.active is old
    if outputs[0] == "new-B":
        assert count == 1
        assert [source.text for source in state.proposals.ready.sources] == ["new-B"]
        state.proposals.activate((key[0], 2))
        assert state.proposals.active is not old
    else:
        assert count == 0
        assert state.proposals.ready is None


@pytest.mark.asyncio
@pytest.mark.parametrize("fence", ["new_input", "work_end", "task_end"])
async def test_late_reference_result_cannot_publish_after_owner_changes(fence):
    key = (object(), 1)
    old = ProposalBatch((ProposalSource(1, "old-A"),), key)
    store = ProposalStore(active=old)
    state = MoAState(proposals=store, active_key=key)
    started, release = asyncio.Event(), asyncio.Event()
    reference = model()

    async def response(**kwargs):
        del kwargs
        started.set()
        await release.wait()
        return AssistantMessage(content="late-B")

    reference.invoke = response
    pending = asyncio.create_task(prepare_reference(
        MoAConfig(reference_models=(reference,)), state, request_messages(), (key[0], 2),
    ))
    await asyncio.wait_for(started.wait(), timeout=2)
    if fence == "new_input":
        store.invalidate("new_input")
    elif fence == "work_end":
        state.closed = True
    else:
        store.close("task_end")
    release.set()
    assert await asyncio.wait_for(pending, timeout=2) == 0
    assert store.ready is None
    if fence == "work_end":
        assert store.active is old
    else:
        assert store.active is None


@pytest.mark.asyncio
async def test_concurrent_reference_refresh_keeps_latest_successful_started_batch():
    key = (object(), 1)
    state = MoAState(active_key=key)
    started = [asyncio.Event(), asyncio.Event()]
    release = [asyncio.Event(), asyncio.Event()]
    calls = 0
    reference = model()

    async def response(**kwargs):
        nonlocal calls
        del kwargs
        index = calls
        calls += 1
        started[index].set()
        await release[index].wait()
        return AssistantMessage(content=f"refresh-{index}")

    reference.invoke = response
    config = MoAConfig(reference_models=(reference,))
    first = asyncio.create_task(prepare_reference(config, state, request_messages(), (key[0], 2)))
    await asyncio.wait_for(started[0].wait(), timeout=2)
    second = asyncio.create_task(prepare_reference(config, state, request_messages(), (key[0], 2)))
    await asyncio.wait_for(started[1].wait(), timeout=2)
    release[1].set()
    assert await asyncio.wait_for(second, timeout=2) == 1
    release[0].set()
    assert await asyncio.wait_for(first, timeout=2) == 1
    assert state.proposals.ready.sources[0].text == "refresh-1"
    assert state.reference_calls == 2


@pytest.mark.asyncio
async def test_temporary_budget_skip_can_recover_but_disabled_batch_cannot():
    key = (object(), 1)
    batch = ProposalBatch((ProposalSource(1, "budget-private-proposal"),), key)
    store = ProposalStore(active=batch)
    context = FixedBudgetContext(limit=1)
    state = MoAState(proposals=store, active_key=key, context=context)
    primary_model = model()
    primary_model.invoke = AsyncMock(return_value=AssistantMessage(content="ok"))
    wrapper = ProposalModel(primary_model)
    token = active_moa.set(state)
    try:
        await wrapper.invoke(request_messages())
        assert batch.attempts[-1].injected is False and batch.disabled_reason is None
        context.limit = 100_000
        state.active_key = (object(), 1)
        await wrapper.invoke(request_messages())
        assert batch.attempts[-1].injected is True
        batch.disabled_reason = "context_overflow"
        store.invalidate("interaction_response", clear=False)
        state.active_key = (object(), 1)
        await wrapper.invoke(request_messages())
        assert batch.attempts[-1].reason == "batch_disabled"
    finally:
        active_moa.reset(token)
    assert ["budget-private-proposal" in str(call.kwargs["messages"])
            for call in primary_model.invoke.await_args_list] == [False, True, False]


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["verifier", "jev"])
async def test_routing_await_does_not_consult_after_input_epoch_changes(mode):
    started, release = asyncio.Event(), asyncio.Event()
    reference = model()
    reference.invoke = AsyncMock(return_value=AssistantMessage(content="must not be consulted"))

    async def decision(*args, **kwargs):
        del args, kwargs
        started.set()
        await release.wait()
        if mode == "jev":
            from tests.unit_tests.harness.moa.test_moa_jev import _response

            return _response(True)
        return 0.2

    if mode == "verifier":
        config = MoAConfig(
            mode="verifier_routed", reference_models=(reference,), scorer=decision,
            verifier=VerifierConfig(interval=1),
        )
    else:
        from tests.unit_tests.harness.moa.test_moa_jev import _client, _config

        client = _client()
        client.system_one = decision
        config = _config(client, reference, interval=1)
    key = (object(), 1)
    state = MoAState(active_key=key, previous_score=0.8)
    ctx = SimpleNamespace(
        context=SimpleNamespace(get_messages=lambda: [AssistantMessage(content="step")]),
        agent=SimpleNamespace(ability_manager=SimpleNamespace(list=lambda: [])),
        session=SimpleNamespace(get_session_id=lambda: "epoch-routing"),
    )
    token = active_moa.set(state)
    try:
        task = asyncio.create_task(MoARail(config).after_react_iteration(ctx))
        await asyncio.wait_for(started.wait(), timeout=2)
        state.proposals.invalidate("new_input")
        release.set()
        await asyncio.wait_for(task, timeout=2)
    finally:
        release.set()
        active_moa.reset(token)
    reference.invoke.assert_not_awaited()
    assert state.proposals.ready is None and state.proposals.active is None
    assert state.previous_score == 0.8


@pytest.mark.parametrize("result,succeeded", [
    ({"result_type": "answer"}, True),
    ({"result_type": "error"}, True),
    (None, False),
])
def test_request_owner_retires_terminal_and_failed_work(result, succeeded):
    owner = MoAProposalOwner()
    work = RoundWorkItem.user(request_id="same-id", inputs={"query": "task"})
    binding = owner.begin_work("session-a", work=work)
    binding.store.active = ProposalBatch((ProposalSource(1, "private-A"),), (object(), 1))
    owner.finish_work(binding.identity, result=result, succeeded=succeeded)
    assert binding.store.closed and binding.store.active is None
    next_binding = owner.begin_work("session-a", work=RoundWorkItem.user(
        request_id="same-id", inputs={"query": "task"},
    ))
    assert next_binding.store is not binding.store
    assert next_binding.store.active is None


def test_late_old_work_cleanup_cannot_close_replacement_or_another_session():
    owner = MoAProposalOwner()
    old_work = RoundWorkItem.user(request_id="old", inputs={"query": "task"})
    old = owner.begin_work("session-a", work=old_work)
    current = owner.begin_work("session-a", work=RoundWorkItem.user(
        request_id="new", inputs={"query": "replacement"},
    ))
    other = owner.begin_work("session-b", work=RoundWorkItem.user(
        request_id="other", inputs={"query": "unrelated"},
    ))
    owner.finish_work(old.identity, result=None, succeeded=False)
    owner.close_work(old_work, "late_cleanup")
    assert old.store.closed
    assert not current.store.closed and not other.store.closed
    owner.close_all("shutdown")
    assert current.store.closed and other.store.closed


def test_owner_discards_unstarted_continuation_without_closing_other_sessions():
    owner = MoAProposalOwner()
    work = RoundWorkItem.user(request_id="parent", inputs={"query": "task"})
    binding = owner.begin_work("session-a", work=work)
    child = RoundWorkItem.user(request_id="parent", inputs={"query": "task"}, reset_loop=False)
    owner.inherit_work(work, child)
    owner.finish_work(binding.identity, result={"result_type": "answer"}, succeeded=True, continuing=True)
    other = owner.begin_work("session-b", work=RoundWorkItem.user(
        request_id="other", inputs={"query": "other"},
    ))
    assert not binding.store.closed
    owner.discard_queued("session-a", "output_detached")
    assert binding.store.closed and not other.store.closed
    stale_child = owner.begin_work("session-a", work=child)
    assert stale_child.store.closed


def test_independent_goal_work_does_not_inherit_or_close_suspended_user_advice():
    owner = MoAProposalOwner()
    user = owner.begin_work("session-a", work=RoundWorkItem.user(
        request_id="paused-user", inputs={"query": "User task"},
    ))
    user.store.active = ProposalBatch((ProposalSource(1, "user-private-advice"),), (object(), 1))
    owner.finish_work(user.identity, result={"result_type": "interrupt"}, succeeded=True)
    goal = owner.begin_work("session-a", work=RoundWorkItem.goal(
        inputs={"query": "Independent goal"}, goal_id="goal-a", revision=1, session_id="session-a",
    ))
    assert goal.store is not user.store and goal.store.active is None
    assert not user.store.closed
    owner.finish_work(goal.identity, result={"result_type": "answer"}, succeeded=True)
    assert goal.store.closed and not user.store.closed
    resumed = owner.begin_work("session-a", interaction_response=True)
    assert resumed.store is user.store
    assert resumed.store.active.sources[0].text == "user-private-advice"
    owner.finish_work(resumed.identity, result={"result_type": "answer"}, succeeded=True)
    assert user.store.closed


def test_late_failed_stream_work_cannot_retire_already_started_same_scope_resume():
    owner = MoAProposalOwner()
    old_work = RoundWorkItem.user(request_id="old-stream", inputs={"query": "task"})
    old = owner.begin_work("session-a", work=old_work)
    old.store.active = ProposalBatch((ProposalSource(1, "retained-advice"),), (object(), 1))
    owner.suspend_work(old.identity)
    resumed = owner.begin_work("session-a", interaction_response=True)
    assert resumed.store is old.store
    owner.finish_work(old.identity, result=None, succeeded=False)
    owner.close_work(old_work, "late_stream_cleanup")
    assert not resumed.store.closed
    assert resumed.store.active.sources[0].text == "retained-advice"
    owner.finish_work(resumed.identity, result={"result_type": "answer"}, succeeded=True)
    assert resumed.store.closed


@pytest.mark.asyncio
async def test_old_after_invoke_cannot_remove_a_replacement_work_state():
    reference = model()
    rail = MoARail(MoAConfig(mode="verifier_routed", reference_models=(reference,), scorer=lambda *_: 0.5))
    store = ProposalStore()
    old = MoAState(proposals=store, owns_proposals=False)
    current = MoAState(proposals=store, owns_proposals=False)
    rail._states["same-session"] = current
    ctx = SimpleNamespace(
        extra={"_moa_state": old}, session=SimpleNamespace(get_session_id=lambda: "same-session"),
    )
    token = active_moa.set(current)
    try:
        await rail.after_invoke(ctx)
        assert active_moa.get() is current
    finally:
        active_moa.reset(token)
    assert rail._states["same-session"] is current
    assert old.closed and not current.closed and not store.closed


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [None, "save_state", "enqueue"])
async def test_passive_internal_work_reuses_proposal_but_next_user_request_starts_fresh(monkeypatch, failure):
    """Exercise supervisor, scheduler and real ReAct; only model/tool I/O is fake."""
    await Runner.start()
    primary_model = model()
    requests = []
    states = []
    outputs = [
        AssistantMessageChunk(tool_calls=[ToolCall(
            id="plan-open", type="function", name="set_test_plan", arguments='{"complete": false}',
        )]),
        AssistantMessageChunk(content="stage one complete"),
        AssistantMessageChunk(tool_calls=[ToolCall(
            id="plan-close", type="function", name="set_test_plan", arguments='{"complete": true}',
        )]),
        AssistantMessageChunk(content="request complete"),
        AssistantMessageChunk(content="new request complete"),
    ]

    async def stream(messages, **kwargs):
        del kwargs
        requests.append(deepcopy(messages))
        states.append(active_moa.get())
        assert outputs, "Unexpected extra model request"
        yield outputs.pop(0)

    primary_model.stream = stream
    reference = model()
    reference.invoke = AsyncMock(side_effect=[
        AssistantMessage(content="request-A-private-proposal"),
        RuntimeError("second work reference unavailable"),
        AssistantMessage(content="request-B-private-proposal"),
    ])
    session = None
    deep = None

    class SetTestPlan(Tool):
        def __init__(self):
            super().__init__(ToolCard(
                id=f"plan-{uuid4().hex}", name="set_test_plan", description="Update the test plan",
                input_params={"type": "object", "properties": {"complete": {"type": "boolean"}}},
            ))

        async def invoke(self, inputs, **kwargs):
            del kwargs
            state = deep.load_state(session)
            state.task_plan = TaskPlan(tasks=[TodoItem(
                id="remaining", content="Finish next work",
                status=TodoStatus.COMPLETED if inputs["complete"] else TodoStatus.PENDING,
            )])
            deep.save_state(session, state)
            return {"updated": True}

        async def stream(self, inputs, **kwargs):
            yield await self.invoke(inputs, **kwargs)

    deep = agent(primary_model, MoAConfig(
        mode="verifier_routed", reference_models=(reference,), scorer=lambda *_: 0.5,
        verifier=VerifierConfig(interval=10),
    ), tools=[SetTestPlan()], enable_task_loop=True)
    session = Session(session_id=f"moa-passive-{uuid4().hex}", card=deep.card)
    await session.pre_run(inputs={})
    try:
        await deep.start(session=session)
        if failure == "save_state":
            original_save = deep.save_state

            def save_state(target_session, state=None):
                if state is None and states:
                    raise RuntimeError("injected final state save failure")
                return original_save(target_session, state)

            monkeypatch.setattr(deep, "save_state", save_state)
        elif failure == "enqueue":
            original_push = deep._event_manager.push_user

            def push_user(work):
                if work.context.get("_moa_input_origin") == "internal":
                    raise RuntimeError("injected continuation enqueue failure")
                original_push(work)

            monkeypatch.setattr(deep._event_manager, "push_user", push_user)
        first_stream = await deep.attach_output()
        assert first_stream is not None
        await deep.send_input(SendInputRequest(request_id="request-A", inputs={"query": "Finish both works"}))
        first_output = await asyncio.wait_for(_collect_output(first_stream), timeout=10)
        if failure is not None:
            assert len(requests) == 2
            assert states[0].proposals.closed and states[0].proposals.active is None
            assert any("error" in str(item.payload) for item in first_output)
            return
        assert any("request complete" in str(item.payload) for item in first_output)
        assert len(requests) == 4
        assert all("request-A-private-proposal" in str(request) for request in requests)
        assert states[0] is states[1]
        assert states[2] is states[3] and states[0] is not states[2]
        assert states[0].proposals is states[2].proposals
        assert states[0].proposals.closed and states[0].proposals.active is None
        assert reference.invoke.await_count == 2
        assert states[0].reference_calls == states[2].reference_calls == 1
        context = deep.react_agent.context_engine.get_context(session_id=session.get_session_id())
        assert "request-A-private-proposal" not in str(context.get_messages())

        second_stream = await deep.attach_output()
        assert second_stream is not None
        await deep.send_input(SendInputRequest(request_id="request-B", inputs={"query": "A different user task"}))
        await asyncio.wait_for(_collect_output(second_stream), timeout=10)
        assert len(requests) == 5
        assert "request-B-private-proposal" in str(requests[-1])
        assert "request-A-private-proposal" not in str(requests[-1])
        assert "private-proposal" not in str(context.get_messages())
    finally:
        await deep.stop()
        await Runner.stop()


async def _collect_output(output):
    return [item async for item in output]


@pytest.mark.asyncio
async def test_passive_permission_reply_preserves_request_proposal_until_completion():
    await Runner.start()
    primary_model = model()
    requests = []
    outputs = [
        AssistantMessageChunk(tool_calls=[ToolCall(
            id="confirm-echo", type="function", name="echo", arguments="{}",
        )]),
        AssistantMessageChunk(content="approved work complete"),
        AssistantMessageChunk(content="fresh work complete"),
    ]

    async def stream(messages, **kwargs):
        del kwargs
        requests.append(deepcopy(messages))
        assert outputs, "Unexpected extra model request"
        yield outputs.pop(0)

    primary_model.stream = stream
    reference = model()
    reference.invoke = AsyncMock(side_effect=[
        AssistantMessage(content="permission-private-proposal"),
        RuntimeError("resume reference unavailable"),
        AssistantMessage(content="fresh-private-proposal"),
    ])
    deep = agent(primary_model, MoAConfig(
        mode="verifier_routed", reference_models=(reference,), scorer=lambda *_: 0.5,
    ), tools=[Echo()], rails=[ConfirmInterruptRail(tool_names=["echo"])], enable_task_loop=True)
    session = Session(session_id=f"moa-permission-{uuid4().hex}", card=deep.card)
    await session.pre_run(inputs={})
    try:
        await deep.start(session=session)
        output = await deep.attach_output()
        await deep.send_input(SendInputRequest(request_id="permission-task", inputs={"query": "Run approved work"}))
        interrupted = await asyncio.wait_for(_collect_output(output), timeout=10)
        interaction = next(item for item in interrupted if hasattr(item.payload, "id"))
        assert len(requests) == 1
        assert "permission-private-proposal" in str(requests[0])
        reply = InteractiveInput()
        reply.update(interaction.payload.id, {"approved": True})
        resumed_output = await deep.attach_output()
        await deep.send_input(SendInputRequest(request_id="permission-answer", inputs={"query": reply}))
        resumed = await asyncio.wait_for(_collect_output(resumed_output), timeout=10)
        assert any("approved work complete" in str(item.payload) for item in resumed)
        assert len(requests) == 2
        assert "permission-private-proposal" in str(requests[1])
        assert reference.invoke.await_count == 2

        fresh_output = await deep.attach_output()
        await deep.send_input(SendInputRequest(request_id="fresh-task", inputs={"query": "New user request"}))
        await asyncio.wait_for(_collect_output(fresh_output), timeout=10)
        assert "fresh-private-proposal" in str(requests[-1])
        assert "permission-private-proposal" not in str(requests[-1])
        context = deep.react_agent.context_engine.get_context(session_id=session.get_session_id())
        assert "private-proposal" not in str(context.get_messages())
    finally:
        await deep.stop()
        await Runner.stop()


@pytest.mark.asyncio
@pytest.mark.parametrize("abort_active_round", [False, True])
async def test_passive_output_detach_preserves_only_a_round_allowed_to_continue(abort_active_round):
    await Runner.start()
    primary_model = model()
    started, release = asyncio.Event(), asyncio.Event()
    stores = []

    async def stream(messages, **kwargs):
        del kwargs
        assert "detach-private-proposal" in str(messages)
        stores.append(active_moa.get().proposals)
        started.set()
        await release.wait()
        yield AssistantMessageChunk(content="completed after detach")

    primary_model.stream = stream
    reference = model()
    reference.invoke = AsyncMock(return_value=AssistantMessage(content="detach-private-proposal"))
    deep = agent(primary_model, MoAConfig(
        mode="verifier_routed", reference_models=(reference,), scorer=lambda *_: 0.5,
    ), enable_task_loop=True)
    session = Session(session_id=f"moa-detach-{uuid4().hex}", card=deep.card)
    await session.pre_run(inputs={})
    try:
        await deep.start(session=session)
        output = await deep.attach_output()
        await deep.send_input(SendInputRequest(request_id="detach-task", inputs={"query": "Finish this task"}))
        await asyncio.wait_for(started.wait(), timeout=5)
        round_task = deep._interaction_round_task
        await output.close(abort_active_round=abort_active_round)
        assert stores[0].closed is abort_active_round
        if not abort_active_round:
            assert stores[0].active.sources[0].text == "detach-private-proposal"
            release.set()
        await asyncio.wait_for(asyncio.shield(round_task), timeout=5)
        assert stores[0].closed and stores[0].active is None
    finally:
        release.set()
        await deep.stop()
        await Runner.stop()


@pytest.mark.asyncio
async def test_direct_stream_permission_reply_keeps_proposal_and_terminal_response_retires_it():
    primary_model = model()
    requests, stores = [], []
    outputs = [
        AssistantMessageChunk(tool_calls=[ToolCall(
            id="stream-confirm", type="function", name="echo", arguments="{}",
        )]),
        AssistantMessageChunk(content="stream resumed successfully"),
    ]

    async def stream(messages, **kwargs):
        del kwargs
        requests.append(deepcopy(messages))
        stores.append(active_moa.get().proposals)
        yield outputs.pop(0)

    primary_model.stream = stream
    reference = model()
    reference.invoke = AsyncMock(side_effect=[
        AssistantMessage(content="stream-permission-private-proposal"),
        RuntimeError("resume reference unavailable"),
    ])
    deep = agent(primary_model, MoAConfig(
        mode="verifier_routed", reference_models=(reference,), scorer=lambda *_: 0.5,
    ), tools=[Echo()], rails=[ConfirmInterruptRail(tool_names=["echo"])])
    session = Session(session_id=f"moa-direct-permission-{uuid4().hex}", card=deep.card)
    interrupted = [item async for item in deep.stream("Do approved work", session=session)]
    interaction = next(item for item in interrupted if hasattr(getattr(item, "payload", None), "id"))
    assert len(stores) == 1 and not stores[0].closed
    reply = InteractiveInput()
    reply.update(interaction.payload.id, {"approved": True})
    resume_session = Session(session_id=session.get_session_id(), card=deep.card)
    resumed = [item async for item in deep.stream({"query": reply}, session=resume_session)]
    assert any("stream resumed successfully" in str(item) for item in resumed)
    assert len(requests) == 2
    assert all("stream-permission-private-proposal" in str(request) for request in requests)
    assert stores[0] is stores[1] and stores[0].closed
    assert stores[0].active is None
    context = deep.react_agent.context_engine.get_context(session_id=session.get_session_id())
    assert "stream-permission-private-proposal" not in str(context.get_messages())
