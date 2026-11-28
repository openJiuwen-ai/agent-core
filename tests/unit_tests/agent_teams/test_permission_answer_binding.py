# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""An approval must remain bound to its original call and member."""

import asyncio
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import pytest

from openjiuwen.agent_teams.agent.team_agent import TeamAgent
from openjiuwen.agent_teams.harness import HarnessState, NativeHarness, TeamHarness
from openjiuwen.agent_teams.harness import team_harness
from openjiuwen.agent_teams.interaction.payload import DeliverResult
from openjiuwen.agent_teams.runtime.manager import TeamRuntimeManager, _resume_interactive_input
from openjiuwen.agent_teams.runtime.pool import ActiveTeam
from openjiuwen.agent_teams.schema.team import TeamRole
from openjiuwen.core.foundation.llm.model import Model
from openjiuwen.core.foundation.llm.schema.config import ModelClientConfig, ModelRequestConfig
from openjiuwen.core.session import InteractiveInput
from openjiuwen.core.single_agent.interrupt.state import INTERRUPTION_KEY, PendingInterrupt, pending_interrupts
from openjiuwen.core.single_agent.schema.agent_card import AgentCard
from openjiuwen.harness.factory import DeepAgentParts
from openjiuwen.harness.schema.config import DeepAgentConfig
from tests.unit_tests.agent_teams.harness.fixtures import wait_for_state
from tests.unit_tests.agent_teams.test_interrupt_admission import deliver, manager_for
from tests.unit_tests.agent_teams.test_pending_approval_native import runtime


@pytest.mark.asyncio
@pytest.mark.parametrize("address", ["call", "interrupt"])
@pytest.mark.parametrize("approved", [False, True])
async def test_completed_answer_cannot_resume_remaining_permission(tmp_path, monkeypatch, address, approved):
    async with runtime(tmp_path, monkeypatch, parallel=True) as run:
        manager, entry = await manager_for(run)
        original = pending_interrupts(run.native.loop_session.get_state(INTERRUPTION_KEY))
        first = next(item for item in original if item.tool_call_id == "call-a")
        second = next(item for item in original if item.tool_call_id == "call-b")

        def answer_for(item, decision):
            answer = InteractiveInput()
            value = {"approved": decision, "feedback": "", "auto_confirm": False}
            if address == "interrupt":
                answer.answer(item.interrupt_id, value)
            else:
                answer.update(item.tool_call_id, value)
            return answer

        assert (await deliver(manager, entry, answer_for(first, True))).ok
        assert await wait_for_state(run.harness, HarnessState.IDLE)
        before = pending_interrupts(run.native.loop_session.get_state(INTERRUPTION_KEY))
        assert before == [second]
        replay = answer_for(first, approved)
        keys = dict(replay.interrupt_answers)
        result = await deliver(manager, entry, replay)
        assert not result.ok and result.reason == "unsupported_interactive_input"
        assert replay.interrupt_answers == keys
        assert pending_interrupts(run.native.loop_session.get_state(INTERRUPTION_KEY)) == before
        assert run.edit.calls == 1 and not run.native._st.pending_queue
        assert (await deliver(manager, entry, answer_for(second, False))).ok
        assert await wait_for_state(run.harness, HarnessState.IDLE)
        assert not run.agent.has_pending_interrupt() and run.edit.calls == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("mixed", [False, True])
async def test_unknown_interrupt_id_cannot_claim_a_current_permission(tmp_path, monkeypatch, mixed):
    async with runtime(tmp_path, monkeypatch) as run:
        manager, entry = await manager_for(run)
        before = pending_interrupts(run.native.loop_session.get_state(INTERRUPTION_KEY))
        answer = InteractiveInput()
        answer.answer("expired-id", {"approved": True, "auto_confirm": False})
        if mixed:
            answer.answer(before[0].interrupt_id, {"approved": True})
        keys = dict(answer.interrupt_answers)
        result = await deliver(manager, entry, answer)
        assert not result.ok and result.reason == "unsupported_interactive_input"
        assert answer.interrupt_answers == keys
        assert pending_interrupts(run.native.loop_session.get_state(INTERRUPTION_KEY)) == before
        assert run.edit.calls == 0 and run.llm.call_count == 1


@pytest.mark.asyncio
async def test_unreadable_pending_remains_unavailable(tmp_path, monkeypatch):
    async with runtime(tmp_path, monkeypatch) as run:
        session = run.native.loop_session
        get_state = session.get_state

        def unreadable(key):
            if key == INTERRUPTION_KEY:
                raise RuntimeError("pending state is unavailable")
            return get_state(key)

        with monkeypatch.context() as patch:
            patch.setattr(session, "get_state", unreadable)
            answer = InteractiveInput()
            answer.answer("unknown", {"approved": True})
            result = await run.agent.resume_interrupt(answer)
            assert not result.ok and result.reason == "interrupt_resume_unavailable"
        assert run.agent.has_pending_interrupt() and run.edit.calls == 0


@pytest.mark.asyncio
async def test_member_lookup_skips_another_members_single_permission(monkeypatch):
    # Isolate owner selection while retaining the real TeamHarness matcher.
    monkeypatch.setattr(team_harness, "pending_interrupts", lambda state: state)

    def member(interrupt_id, call_id):
        harness = object.__new__(TeamHarness)
        pending = [PendingInterrupt(interrupt_id, call_id, "permission")]
        harness._native = NS(loop_session=NS(get_state=lambda _key: pending))
        return NS(
            owns_pending_interrupt_answer=harness.is_pending_interrupt_resume_valid,
            resume_interrupt=AsyncMock(return_value=DeliverResult.success()),
        )

    other, owner = member("intr-a", "call-a"), member("intr-b", "call-b")
    leader = NS(
        resume_interrupt=AsyncMock(return_value=DeliverResult.failure("unsupported_interactive_input")),
        spawn_manager=NS(spawned_handles={"other": NS(agent_ref=other), "owner": NS(agent_ref=owner)}),
    )
    answer = InteractiveInput()
    answer.answer("intr-b", {"approved": True, "auto_confirm": False})
    assert (await _resume_interactive_input(leader, answer, None)).ok
    other.resume_interrupt.assert_not_awaited()
    owner.resume_interrupt.assert_awaited_once_with(answer, run_context=None)
    assert set(answer.interrupt_answers) == {"intr-b"}


@pytest.mark.asyncio
async def test_manager_keeps_a_pausing_members_cleared_answer_retryable(tmp_path, monkeypatch):
    from openjiuwen.agent_teams.runtime import manager as runtime_manager

    monkeypatch.setattr(runtime_manager, "_INTERRUPT_RESUME_UNOWNED_WAIT_SECONDS", 0)
    async with runtime(tmp_path, monkeypatch, parallel=True) as run:
        card = AgentCard(name="empty-leader")
        model = Model(
            model_client_config=ModelClientConfig(
                client_provider="OpenAI", api_key="fake", api_base="http://unused.invalid",
            ),
            model_config=ModelRequestConfig(model="mock-model"),
        )
        config = DeepAgentConfig(card=card, model=model, cwd=str(tmp_path), enable_read_image_multimodal=False)
        spec = NS(resolve_parts=lambda context=None: DeepAgentParts(
            config=config, rails=[], tool_cards=[], tool_instances=[],
        ))
        native = NativeHarness(spec)
        harness = TeamHarness(spec, None, native, role=TeamRole.LEADER, member_name="leader")
        leader = TeamAgent(card)
        leader._configurator.harness = harness
        leader._spawn_manager = NS(spawned_handles={"worker": NS(agent_ref=run.agent)})
        manager = TeamRuntimeManager()
        entered, release = asyncio.Event(), asyncio.Event()
        original = run.native.react_agent._init_context
        pausing = None

        async def blocked(*args, **kwargs):
            entered.set()
            await release.wait()
            return await original(*args, **kwargs)

        monkeypatch.setattr(run.native.react_agent, "_init_context", blocked)
        pending = pending_interrupts(run.native.loop_session.get_state(INTERRUPTION_KEY))
        second = next(item for item in pending if item.tool_call_id == "call-b")
        try:
            await harness.start(team_session=None)
            entry = ActiveTeam(team_name="pause-window", agent=leader, current_session_id=native.session_id)
            await manager.pool.add(entry)
            answer_a = InteractiveInput()
            answer_a.update("call-a", {"approved": True, "auto_confirm": False})
            assert (await run.agent.resume_interrupt(answer_a)).ok
            await asyncio.wait_for(entered.wait(), 2)
            pausing = asyncio.create_task(run.harness.pause())
            assert await wait_for_state(run.harness, HarnessState.PAUSING)
            assert not leader.has_pending_interrupt() and not run.agent.has_pending_interrupt()
            answer_b = InteractiveInput()
            answer_b.answer(second.interrupt_id, {"approved": False, "auto_confirm": False})
            result = await deliver(manager, entry, answer_b)
            assert not result.ok and result.reason == "interrupt_resume_unavailable"
            assert entry.interact_gate.inflight == 0 and run.edit.calls == 0
            release.set()
            await asyncio.wait_for(pausing, 2)
            monkeypatch.setattr(run.native.react_agent, "_init_context", original)
            assert run.native.state is HarnessState.PAUSED
            restored = pending_interrupts(run.native.loop_session.get_state(INTERRUPTION_KEY))
            assert second in restored
            assert (await deliver(manager, entry, answer_b)).ok
            assert await wait_for_state(run.harness, HarnessState.IDLE)
            assert second not in pending_interrupts(run.native.loop_session.get_state(INTERRUPTION_KEY))
        finally:
            release.set()
            if pausing is not None:
                await asyncio.wait_for(pausing, 2)
            await harness.stop()
