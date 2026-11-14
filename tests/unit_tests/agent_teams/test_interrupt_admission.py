# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Addressed approval admission through the real Native supervisor and Manager."""

import asyncio
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import pytest

from openjiuwen.agent_teams.agent.team_agent import TeamAgent
from openjiuwen.agent_teams.harness import HarnessState
from openjiuwen.agent_teams.interaction.payload import DeliverResult
from openjiuwen.agent_teams.runtime.manager import TeamRuntimeManager
from openjiuwen.agent_teams.runtime.pool import ActiveTeam
from openjiuwen.agent_teams.schema.team import TeamRole
from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import BaseError, build_error
from openjiuwen.core.single_agent.interrupt.state import INTERRUPTION_KEY, pending_interrupts
from openjiuwen.core.single_agent.schema.agent_card import AgentCard
from tests.unit_tests.agent_teams.harness.fixtures import wait_for_state
from tests.unit_tests.agent_teams.test_pending_approval_native import (
    approval_answer,
    record_user_inputs,
    runtime,
)


async def manager_for(run):
    run.handler._blueprint.role = TeamRole.LEADER
    run.handler._blueprint.ctx.role = TeamRole.LEADER
    manager = TeamRuntimeManager()
    entry = ActiveTeam(team_name="admission", agent=run.agent, current_session_id=run.native.session_id)
    await manager.pool.add(entry)
    return manager, entry


async def deliver(manager, entry, answer):
    return await manager.interact(answer, team_name=entry.team_name, session_id=entry.current_session_id)


@pytest.mark.asyncio
@pytest.mark.parametrize("mixed", [False, True])
async def test_stale_or_partly_stale_answer_is_not_admitted(tmp_path, monkeypatch, mixed):
    async with runtime(tmp_path, monkeypatch) as run:
        manager, entry = await manager_for(run)
        before = pending_interrupts(run.native.loop_session.get_state(INTERRUPTION_KEY))
        answer = approval_answer("expired", True)
        if mixed:
            answer.update("call-a", {"approved": True})
        result = await deliver(manager, entry, answer)
        assert not result.ok and result.reason == "unsupported_interactive_input"
        assert pending_interrupts(run.native.loop_session.get_state(INTERRUPTION_KEY)) == before
        assert not run.native._st.pending_queue and run.native.state is HarnessState.IDLE
        assert run.edit.calls == 0 and run.llm.call_count == 1
        assert entry.interact_gate.inflight == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("hook", ["invoke", "_init_context"])
@pytest.mark.parametrize("duplicate", [False, True])
async def test_busy_answer_never_enters_fifo_or_query(tmp_path, monkeypatch, hook, duplicate):
    async with runtime(tmp_path, monkeypatch, parallel=not duplicate) as run:
        entered, release = asyncio.Event(), asyncio.Event()
        react = run.native.react_agent
        original = getattr(react, hook)
        seen = await record_user_inputs(run)

        async def blocked(*args, **kwargs):
            entered.set()
            await release.wait()
            return await original(*args, **kwargs)

        monkeypatch.setattr(react, hook, blocked)
        try:
            first = await run.agent.resume_interrupt(approval_answer("call-a", True))
            assert first.ok
            await asyncio.wait_for(entered.wait(), 2)
            second = await run.agent.resume_interrupt(approval_answer("call-a" if duplicate else "call-b", False))
            assert not second.ok and second.reason == "interrupt_resume_busy"
            assert not run.native._st.pending_queue
        finally:
            release.set()
        assert await wait_for_state(run.harness, HarnessState.IDLE)
        monkeypatch.setattr(react, hook, original)
        if duplicate:
            stale = await run.agent.resume_interrupt(approval_answer("call-a", True))
            assert not stale.ok and stale.reason == "unsupported_interactive_input"
        else:
            assert [item.tool_call_id for item in pending_interrupts(
                run.native.loop_session.get_state(INTERRUPTION_KEY)
            )] == ["call-b"]
            assert (await run.agent.resume_interrupt(approval_answer("call-b", False))).ok
            assert await wait_for_state(run.harness, HarnessState.IDLE)
        assert run.edit.calls == 1 and run.llm.call_count == 2
        assert not run.agent.has_pending_interrupt() and not run.native._st.pending_queue
        assert all(source != "query" for source, _, _ in seen)


@pytest.mark.asyncio
async def test_cancelled_unclaimed_answer_does_not_start_a_round(tmp_path, monkeypatch):
    async with runtime(tmp_path, monkeypatch) as run:
        entered, release = asyncio.Event(), asyncio.Event()
        original = run.native._on_send

        async def blocked(cmd):
            entered.set()
            await release.wait()
            return await original(cmd)

        monkeypatch.setattr(run.native, "_on_send", blocked)
        task = asyncio.create_task(run.agent.resume_interrupt(approval_answer("call-a", True)))
        try:
            await asyncio.wait_for(entered.wait(), 2)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        finally:
            release.set()
        for _ in range(10):
            await asyncio.sleep(0)
        assert run.agent.has_pending_interrupt()
        assert run.native.state is HarnessState.IDLE and run.native._st.round_id_counter == 1
        assert run.edit.calls == 0 and run.llm.call_count == 1
        monkeypatch.setattr(run.native, "_on_send", original)
        assert (await run.agent.resume_interrupt(approval_answer("call-a", False))).ok
        assert await wait_for_state(run.harness, HarnessState.IDLE)


@pytest.mark.asyncio
async def test_closed_gate_rejects_typed_answer_and_reset_restores_admission(tmp_path, monkeypatch):
    async with runtime(tmp_path, monkeypatch) as run:
        manager, entry = await manager_for(run)
        await entry.interact_gate.close_and_drain()
        for answer in ("keep waiting", approval_answer("call-a", True)):
            result = await deliver(manager, entry, answer)
            assert not result.ok and result.reason == "gate_closed"
        assert run.agent.has_pending_interrupt() and run.edit.calls == 0
        assert run.llm.call_count == 1 and entry.interact_gate.inflight == 0
        await entry.interact_gate.reset()
        assert (await deliver(manager, entry, approval_answer("call-a", True))).ok
        assert await wait_for_state(run.harness, HarnessState.IDLE)
        assert run.edit.calls == 1 and not run.agent.has_pending_interrupt()


@pytest.mark.asyncio
async def test_gate_drain_covers_admission_not_model_completion(tmp_path, monkeypatch):
    async with runtime(tmp_path, monkeypatch) as run:
        manager, entry = await manager_for(run)
        admitted, release_admission = asyncio.Event(), asyncio.Event()
        model_entered, release_model = asyncio.Event(), asyncio.Event()
        original_resume, original_invoke = run.agent.resume_interrupt, run.native.react_agent.invoke

        async def blocked_resume(*args, **kwargs):
            admitted.set()
            await release_admission.wait()
            return await original_resume(*args, **kwargs)

        async def blocked_model(*args, **kwargs):
            model_entered.set()
            await release_model.wait()
            return await original_invoke(*args, **kwargs)

        monkeypatch.setattr(run.agent, "resume_interrupt", blocked_resume)
        monkeypatch.setattr(run.native.react_agent, "invoke", blocked_model)
        answer = asyncio.create_task(deliver(manager, entry, approval_answer("call-a", True)))
        closing = None
        try:
            await asyncio.wait_for(admitted.wait(), 2)
            assert entry.interact_gate.inflight == 1
            closing = asyncio.create_task(entry.interact_gate.close_and_drain())
            await asyncio.sleep(0)
            late = await deliver(manager, entry, approval_answer("call-a", True))
            assert not late.ok and late.reason == "gate_closed"
            assert not closing.done()
            release_admission.set()
            assert (await asyncio.wait_for(answer, 2)).ok
            await asyncio.wait_for(closing, 2)
            await asyncio.wait_for(model_entered.wait(), 2)
            assert not release_model.is_set() and run.edit.calls == 0
            assert entry.interact_gate.inflight == 0
        finally:
            release_admission.set()
            release_model.set()
            await answer
            if closing is not None:
                await closing
        assert await wait_for_state(run.harness, HarnessState.IDLE)
        assert run.edit.calls == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [RuntimeError("unknown failure"), asyncio.CancelledError()])
async def test_unknown_error_and_cancellation_release_gate_ticket(tmp_path, monkeypatch, error):
    async with runtime(tmp_path, monkeypatch) as run:
        manager, entry = await manager_for(run)
        monkeypatch.setattr(run.agent, "resume_interrupt", AsyncMock(side_effect=error))
        with pytest.raises(type(error)):
            await deliver(manager, entry, approval_answer("call-a", True))
        assert entry.interact_gate.inflight == 0
        assert run.edit.calls == 0 and run.agent.has_pending_interrupt()


@pytest.mark.asyncio
async def test_only_marked_admission_errors_become_failure_receipts(tmp_path, monkeypatch):
    async with runtime(tmp_path, monkeypatch) as run:
        error = build_error(StatusCode.DEEPAGENT_RUNTIME_ERROR, error_msg="unexpected runtime error")
        monkeypatch.setattr(run.harness, "_resume_interrupt", AsyncMock(side_effect=error))
        with pytest.raises(BaseError):
            await run.agent.resume_interrupt(approval_answer("call-a", True))


@pytest.mark.asyncio
async def test_external_running_provider_retains_none_send_success():
    agent = TeamAgent(AgentCard(name="external-admission"))
    send = AsyncMock(return_value=None)
    agent._configurator.harness = NS(
        state=HarnessState.RUNNING,
        is_pending_interrupt_resume_valid=lambda _: True,
        send=send,
    )
    result = await agent.resume_interrupt(approval_answer("provider-call", True))
    assert isinstance(result, DeliverResult) and result.ok
    send.assert_awaited_once()


@pytest.mark.asyncio
async def test_internal_plan_response_retains_raw_delivery(tmp_path, monkeypatch):
    async with runtime(tmp_path, monkeypatch) as run:
        raw_send = AsyncMock()
        directed = AsyncMock()
        monkeypatch.setattr(run.harness, "send", raw_send)
        monkeypatch.setattr(run.harness, "_resume_interrupt", directed)
        await run.agent._resume_plan_interrupt(approval_answer("call-a", True))
        raw_send.assert_awaited_once()
        directed.assert_not_awaited()


@pytest.mark.asyncio
async def test_pausing_with_temporarily_empty_pending_keeps_durable_answer(tmp_path, monkeypatch):
    from tests.unit_tests.agent_teams.test_pending_approval_mailbox import mail

    async with runtime(tmp_path, monkeypatch, parallel=True) as run:
        entered, release = asyncio.Event(), asyncio.Event()
        original = run.native.react_agent._init_context

        async def blocked(*args, **kwargs):
            entered.set()
            await release.wait()
            return await original(*args, **kwargs)

        monkeypatch.setattr(run.native.react_agent, "_init_context", blocked)
        pausing = None
        try:
            assert (await run.agent.resume_interrupt(approval_answer("call-a", True))).ok
            await asyncio.wait_for(entered.wait(), 2)
            assert not run.agent.has_pending_interrupt()
            pausing = asyncio.create_task(run.harness.pause())
            assert await wait_for_state(run.harness, HarnessState.PAUSING)
            run.mailbox.messages = [mail("1", approval=True, tool_call_id="call-b", approved=False)]
            await run.handler._process_unread_messages("worker")
            assert run.mailbox.read == [] and not run.native._st.pending_queue
        finally:
            release.set()
            if pausing is not None:
                await asyncio.wait_for(pausing, 2)
        from openjiuwen.core.context_engine.context.cancellation import resolve_cancellation_cause

        assert run.native.state is HarnessState.PAUSED
        pending = pending_interrupts(run.native.loop_session.get_state(INTERRUPTION_KEY))
        assert pending
        paused_query = run.native._st.paused_query
        cause = resolve_cancellation_cause(run.native.session_id)
        stale = await run.agent.resume_interrupt(approval_answer("expired", True))
        assert not stale.ok and stale.reason == "unsupported_interactive_input"
        assert run.native.state is HarnessState.PAUSED and run.native._st.paused_query == paused_query
        assert resolve_cancellation_cause(run.native.session_id) == cause
        assert pending_interrupts(run.native.loop_session.get_state(INTERRUPTION_KEY)) == pending
        assert (await run.agent.resume_interrupt(approval_answer(pending[0].tool_call_id, False))).ok
        assert await wait_for_state(run.harness, HarnessState.IDLE)


@pytest.mark.asyncio
@pytest.mark.parametrize("address", ["call", "interrupt", "both"])
async def test_valid_batch_answers_reuse_existing_interrupt_addressing(tmp_path, monkeypatch, address):
    from openjiuwen.core.session import InteractiveInput

    async with runtime(tmp_path, monkeypatch, parallel=True) as run:
        current = pending_interrupts(run.native.loop_session.get_state(INTERRUPTION_KEY))
        answer = InteractiveInput()
        for item in current:
            approved = item.tool_call_id == "call-a"
            if address in ("call", "both"):
                answer.update(item.tool_call_id, {"approved": approved})
            if address in ("interrupt", "both"):
                answer.answer(item.interrupt_id, {"approved": approved})
        assert (await run.agent.resume_interrupt(answer)).ok
        assert await wait_for_state(run.harness, HarnessState.IDLE)
        assert run.edit.calls == 1 and not run.agent.has_pending_interrupt()
        assert not run.native._st.pending_queue


@pytest.mark.asyncio
@pytest.mark.parametrize("approved", [False, True])
@pytest.mark.parametrize("overlap", [False, True])
async def test_real_plan_event_preserves_raw_admission_and_overlap(tmp_path, monkeypatch, approved, overlap):
    from openjiuwen.agent_teams.agent.coordination.handlers.agent_lifecycle import AgentLifecycleHandler
    from openjiuwen.agent_teams.schema.events import EventMessage, TaskPlanResponseEvent

    async with runtime(tmp_path, monkeypatch, mixed=True) as run:
        lifecycle = AgentLifecycleHandler(run.agent, run.handler._blueprint, run.handler._infra, None)
        event = EventMessage.from_event(TaskPlanResponseEvent(
            team_name="admission", member_name="worker", task_id="task-plan",
            tool_call_id="call-a", approved=approved, status="in_progress" if approved else "planning",
            plan_id="plan-one", feedback="plan decision",
        ))
        entered, release = asyncio.Event(), asyncio.Event()
        original = run.native.react_agent.invoke

        async def blocked(*args, **kwargs):
            entered.set()
            await release.wait()
            return await original(*args, **kwargs)

        if overlap:
            monkeypatch.setattr(run.native.react_agent, "invoke", blocked)
        try:
            await lifecycle.on_task_plan_response(event)
            if overlap:
                await asyncio.wait_for(entered.wait(), 2)
                await lifecycle.on_task_plan_response(event)
                queued, = run.native._st.pending_queue
                assert queued.content.user_inputs["call-a"] == {
                    "approved": approved, "feedback": "plan decision", "plan_id": "plan-one",
                }
        finally:
            release.set()
        assert await wait_for_state(run.harness, HarnessState.IDLE)
        assert run.other.calls == int(approved) and run.edit.calls == 0
        # The legacy plan channel retains its old overlap/FIFO behavior.
        # It has no durable retry mail and is outside the new typed guarantee.
        if overlap:
            assert len(run.native._st.pending_queue) == 1
        assert run.agent.has_pending_interrupt()


@pytest.mark.asyncio
async def test_leader_mismatch_forwards_to_the_owning_inprocess_teammate():
    from openjiuwen.agent_teams.runtime.manager import _resume_interactive_input

    answer = approval_answer("mate-call", True)
    teammate_resume = AsyncMock(return_value=DeliverResult.success())
    other_resume = AsyncMock(return_value=DeliverResult.success())
    leader = NS(
        resume_interrupt=AsyncMock(return_value=DeliverResult.failure("unsupported_interactive_input")),
        spawn_manager=NS(spawned_handles={
            "other": NS(agent_ref=NS(
                owns_pending_interrupt_answer=lambda payload: False,
                resume_interrupt=other_resume,
            )),
            "mate": NS(agent_ref=NS(
                owns_pending_interrupt_answer=lambda payload: payload is answer,
                resume_interrupt=teammate_resume,
            )),
            "remote": NS(agent_ref=None),
        }),
    )
    result = await _resume_interactive_input(leader, answer, None)
    assert result.ok
    teammate_resume.assert_awaited_once()
    other_resume.assert_not_awaited()


@pytest.mark.asyncio
async def test_leader_mismatch_keeps_receipt_when_no_teammate_owns_it():
    from openjiuwen.agent_teams.runtime.manager import _resume_interactive_input

    answer = approval_answer("expired", False)
    leader = NS(
        resume_interrupt=AsyncMock(return_value=DeliverResult.failure("unsupported_interactive_input")),
        spawn_manager=NS(spawned_handles={
            "mate": NS(agent_ref=NS(
                owns_pending_interrupt_answer=lambda payload: False,
                resume_interrupt=AsyncMock(return_value=DeliverResult.success()),
            )),
        }),
    )
    result = await _resume_interactive_input(leader, answer, None)
    assert not result.ok and result.reason == "unsupported_interactive_input"


@pytest.mark.asyncio
async def test_stopped_owner_keeps_answer_unclaimed_instead_of_leader_mismatch():
    from openjiuwen.agent_teams.runtime.manager import _resume_interactive_input

    answer = approval_answer("mate-call", True)
    stopped = build_error(
        StatusCode.DEEPAGENT_RUNTIME_ERROR,
        error_msg="NativeHarness already stopped.",
    )
    leader = NS(
        resume_interrupt=AsyncMock(return_value=DeliverResult.failure("unsupported_interactive_input")),
        spawn_manager=NS(spawned_handles={
            "mate": NS(agent_ref=NS(
                owns_pending_interrupt_answer=lambda _payload: True,
                resume_interrupt=AsyncMock(side_effect=stopped),
            )),
        }),
    )
    result = await _resume_interactive_input(leader, answer, None)
    assert not result.ok and result.reason == "interrupt_resume_unavailable"


@pytest.mark.asyncio
async def test_missing_harness_reports_interrupt_unavailable():
    agent = TeamAgent(AgentCard(name="no-harness"))
    result = await agent.resume_interrupt(approval_answer("call-a", True))
    assert not result.ok and result.reason == "interrupt_resume_unavailable"
