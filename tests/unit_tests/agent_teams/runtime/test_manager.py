# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unit tests for TeamRuntimeManager.finalize_member."""

import asyncio
from types import SimpleNamespace
from unittest.mock import (
    AsyncMock,
    MagicMock,
)

import pytest

from openjiuwen.agent_teams.agent.member import TeamMember
from openjiuwen.agent_teams.interaction import ExternalTeamEvent
from openjiuwen.agent_teams.messager.inprocess import InProcessMessager, cleanup_inprocess_bus
from openjiuwen.agent_teams.runtime.manager import TeamRuntimeManager
from openjiuwen.agent_teams.runtime.pool import ActiveTeam, RuntimeState
from openjiuwen.agent_teams.schema.events import EventMessage, TeamTopic
from openjiuwen.agent_teams.schema.status import MemberStatus


class FakeTeamAgent:
    """Minimal mock for finalize_member's agent argument."""

    def __init__(
        self,
        member_name: str = "test_member",
        team_member: TeamMember | MagicMock | None = None,
    ) -> None:
        self.member_name = member_name
        self.team_member = team_member
        self.stop_coordination_calls = 0
        self.pause_coordination_calls = 0

    async def stop_coordination(self) -> None:
        self.stop_coordination_calls += 1

    async def pause_coordination(self) -> None:
        self.pause_coordination_calls += 1


class FakeTeamMember:
    """Mock TeamMember that tracks status updates."""

    def __init__(self, initial_status: MemberStatus) -> None:
        self._status = initial_status
        self.update_status_calls: list[MemberStatus] = []

    async def status(self) -> MemberStatus:
        return self._status

    async def update_status(self, new_status: MemberStatus) -> bool:
        self.update_status_calls.append(new_status)
        self._status = new_status
        return True


class TestFinalizeMember:
    """Test TeamRuntimeManager.finalize_member lifecycle decisions."""

    @pytest.mark.asyncio
    @pytest.mark.level0
    async def test_shutdown_requested_transitions_to_shutdown(self):
        """SHUTDOWN_REQUESTED must be consumed and become SHUTDOWN.

        This is the core bug fix: previously SHUTDOWN_REQUESTED was in
        _MEMBER_FINALIZED_STATUSES and skipped, leaving members unable to
        complete shutdown.
        """
        fake_member = FakeTeamMember(MemberStatus.SHUTDOWN_REQUESTED)
        agent = FakeTeamAgent(team_member=fake_member)

        await TeamRuntimeManager.finalize_member(agent)

        assert agent.stop_coordination_calls == 1
        assert agent.pause_coordination_calls == 0
        assert fake_member.update_status_calls == [MemberStatus.SHUTDOWN]

    @pytest.mark.asyncio
    @pytest.mark.level0
    async def test_ready_pauses_and_marks_ready(self):
        """READY status should pause the kernel and stay READY for next assignment."""
        fake_member = FakeTeamMember(MemberStatus.READY)
        agent = FakeTeamAgent(team_member=fake_member)

        await TeamRuntimeManager.finalize_member(agent)

        assert agent.stop_coordination_calls == 0
        assert agent.pause_coordination_calls == 1
        assert fake_member.update_status_calls == [MemberStatus.READY]

    @pytest.mark.asyncio
    @pytest.mark.level0
    async def test_busy_pauses_and_marks_ready(self):
        """BUSY status should pause and transition to READY."""
        fake_member = FakeTeamMember(MemberStatus.BUSY)
        agent = FakeTeamAgent(team_member=fake_member)

        await TeamRuntimeManager.finalize_member(agent)

        assert agent.stop_coordination_calls == 0
        assert agent.pause_coordination_calls == 1
        assert fake_member.update_status_calls == [MemberStatus.READY]

    @pytest.mark.asyncio
    @pytest.mark.level0
    async def test_already_shutdown_skips_status_write(self):
        """SHUTDOWN is a finalized status; only stop_coordination runs."""
        fake_member = FakeTeamMember(MemberStatus.SHUTDOWN)
        agent = FakeTeamAgent(team_member=fake_member)

        await TeamRuntimeManager.finalize_member(agent)

        assert agent.stop_coordination_calls == 1
        assert agent.pause_coordination_calls == 0
        assert fake_member.update_status_calls == []

    @pytest.mark.asyncio
    @pytest.mark.level0
    async def test_already_stopped_skips_status_write(self):
        """STOPPED is a finalized status; only stop_coordination runs."""
        fake_member = FakeTeamMember(MemberStatus.STOPPED)
        agent = FakeTeamAgent(team_member=fake_member)

        await TeamRuntimeManager.finalize_member(agent)

        assert agent.stop_coordination_calls == 1
        assert agent.pause_coordination_calls == 0
        assert fake_member.update_status_calls == []

    @pytest.mark.asyncio
    @pytest.mark.level0
    async def test_already_paused_skips_status_write(self):
        """PAUSED is a finalized status; only stop_coordination runs."""
        fake_member = FakeTeamMember(MemberStatus.PAUSED)
        agent = FakeTeamAgent(team_member=fake_member)

        await TeamRuntimeManager.finalize_member(agent)

        assert agent.stop_coordination_calls == 1
        assert agent.pause_coordination_calls == 0
        assert fake_member.update_status_calls == []

    @pytest.mark.asyncio
    @pytest.mark.level1
    async def test_no_team_member_pauses_default_path(self):
        """When team_member is None, status cannot be read, so default is pause."""
        agent = FakeTeamAgent(team_member=None)

        await TeamRuntimeManager.finalize_member(agent)

        # Without a team_member, status is None, so neither finalized nor
        # SHUTDOWN_REQUESTED — the default path is pause.
        assert agent.stop_coordination_calls == 0
        assert agent.pause_coordination_calls == 1

    @pytest.mark.asyncio
    @pytest.mark.level1
    async def test_no_team_member_pauses_on_other_status(self):
        """When team_member is None, default path pauses the kernel."""
        agent = FakeTeamAgent(team_member=None)

        await TeamRuntimeManager.finalize_member(agent)

        assert agent.stop_coordination_calls == 0
        assert agent.pause_coordination_calls == 1

    @pytest.mark.asyncio
    @pytest.mark.level1
    async def test_status_read_failure_falls_back_to_pause(self):
        """If member.status() throws, fallback is pause + READY."""
        failing_member = MagicMock()
        failing_member.status = AsyncMock(side_effect=RuntimeError("db error"))
        failing_member.update_status = AsyncMock(return_value=True)
        agent = FakeTeamAgent(team_member=failing_member)

        await TeamRuntimeManager.finalize_member(agent)

        assert agent.stop_coordination_calls == 0
        assert agent.pause_coordination_calls == 1
        failing_member.update_status.assert_awaited_once_with(MemberStatus.READY)

    @pytest.mark.asyncio
    @pytest.mark.level1
    async def test_unstarted_status_pauses_and_marks_ready(self):
        """UNSTARTED should be treated as non-finalized and pause."""
        fake_member = FakeTeamMember(MemberStatus.UNSTARTED)
        agent = FakeTeamAgent(team_member=fake_member)

        await TeamRuntimeManager.finalize_member(agent)

        assert agent.stop_coordination_calls == 0
        assert agent.pause_coordination_calls == 1
        assert fake_member.update_status_calls == [MemberStatus.READY]

    @pytest.mark.asyncio
    @pytest.mark.level1
    async def test_error_status_pauses_and_marks_ready(self):
        """ERROR status should pause and reset to READY for recovery."""
        fake_member = FakeTeamMember(MemberStatus.ERROR)
        agent = FakeTeamAgent(team_member=fake_member)

        await TeamRuntimeManager.finalize_member(agent)

        assert agent.stop_coordination_calls == 0
        assert agent.pause_coordination_calls == 1
        assert fake_member.update_status_calls == [MemberStatus.READY]

    @pytest.mark.asyncio
    @pytest.mark.level1
    async def test_restarting_status_pauses_and_marks_ready(self):
        """RESTARTING status should pause and become READY."""
        fake_member = FakeTeamMember(MemberStatus.RESTARTING)
        agent = FakeTeamAgent(team_member=fake_member)

        await TeamRuntimeManager.finalize_member(agent)

        assert agent.stop_coordination_calls == 0
        assert agent.pause_coordination_calls == 1
        assert fake_member.update_status_calls == [MemberStatus.READY]


class TestExternalEventIngress:
    @pytest.mark.asyncio
    @pytest.mark.level0
    async def test_routes_external_event_through_interact_to_local_bus(self):
        cleanup_inprocess_bus()
        manager = TeamRuntimeManager()
        agent = MagicMock()
        publisher = InProcessMessager()
        agent.team_backend = SimpleNamespace(messager=publisher)
        await manager.pool.add(
            ActiveTeam(
                team_name="alpha",
                agent=agent,
                current_session_id="session-1",
                state=RuntimeState.RUNNING,
            )
        )
        entry = await manager.pool.get("alpha")
        assert entry is not None
        await entry.interact_gate.close_and_drain()
        received: list[EventMessage] = []

        async def handler(message: EventMessage) -> None:
            received.append(message)

        topic = TeamTopic.MESSAGE.build("session-1", "alpha")
        subscriber = InProcessMessager()
        await subscriber.subscribe(topic, handler)
        message = EventMessage(
            event_type="message",
            payload={
                "team_name": "alpha",
                "message_id": "message-1",
                "from_member_name": "external-cli",
                "to_member_name": "leader",
            },
            sender_id="external-cli",
        )
        external_event = ExternalTeamEvent(topic=TeamTopic.MESSAGE, event=message)

        delivered = await manager.interact(
            external_event.to_wire(),
            team_name="alpha",
            session_id="session-1",
        )

        assert delivered.ok is True
        assert received == [message]
        cleanup_inprocess_bus()

    @pytest.mark.asyncio
    @pytest.mark.level0
    async def test_rejects_external_event_for_a_different_team(self):
        manager = TeamRuntimeManager()
        agent = MagicMock()
        agent.team_backend = SimpleNamespace(messager=InProcessMessager())
        await manager.pool.add(
            ActiveTeam(
                team_name="alpha",
                agent=agent,
                current_session_id="session-1",
                state=RuntimeState.RUNNING,
            )
        )
        result = await manager.interact(
            ExternalTeamEvent(
                topic=TeamTopic.MESSAGE,
                event=EventMessage(
                    event_type="message",
                    payload={
                        "team_name": "other",
                        "message_id": "message-1",
                        "from_member_name": "external-cli",
                        "to_member_name": "leader",
                    },
                ),
            ).to_wire(),
            team_name="alpha",
            session_id="session-1",
        )

        assert result.ok is False
        assert result.reason == "external_event_team_mismatch"


@pytest.mark.asyncio
async def test_stop_timeout_retains_runtime_and_coalesces_until_exit(monkeypatch):
    import openjiuwen.agent_teams.runtime.manager as module

    monkeypatch.setattr(module, "_TEAM_STOP_TIMEOUT_SECONDS", 0.01)
    manager = TeamRuntimeManager()
    entered, release = asyncio.Event(), asyncio.Event()

    async def stop():
        entered.set()
        await release.wait()

    agent = SimpleNamespace(stop_coordination=AsyncMock(side_effect=stop))
    entry = ActiveTeam(team_name="stop-test", agent=agent, current_session_id="s")
    await manager.pool.add(entry)
    try:
        results = await asyncio.gather(
            manager.stop_team(team_name="stop-test", session_id="s"),
            manager.stop_team(team_name="stop-test", session_id="s"),
            return_exceptions=True,
        )
        assert all(isinstance(result, TimeoutError) for result in results)
        assert entered.is_set()
        assert entry.state is RuntimeState.STOPPING
        assert await manager.pool.get("stop-test") is entry
        assert not entry.stop_task.done()
        assert (await manager.interact("continue", team_name="stop-test", session_id="s")).reason == "stopping"
        await manager.finalize(team_name="stop-test", session_id="s")
        assert not await manager.pause(team_name="stop-test", session_id="s")
        with pytest.raises(Exception, match="still stopping"):
            await manager.activate(SimpleNamespace(team_name="stop-test"), "s", "continue")
    finally:
        release.set()
        await entry.stop_task
    agent.stop_coordination.assert_awaited_once()
    assert await manager.pool.get("stop-test") is None


@pytest.mark.asyncio
async def test_stop_failure_retains_runtime_and_can_retry():
    manager = TeamRuntimeManager()
    agent = SimpleNamespace(stop_coordination=AsyncMock(side_effect=[RuntimeError("stop failed"), None]))
    entry = ActiveTeam(team_name="stop-test", agent=agent, current_session_id="s")
    await manager.pool.add(entry)
    with pytest.raises(RuntimeError, match="stop failed"):
        await manager.stop_team(team_name="stop-test", session_id="s")
    assert await manager.pool.get("stop-test") is entry
    assert entry.state is RuntimeState.STOPPING
    assert await manager.stop_team(team_name="stop-test", session_id="s")
    assert await manager.pool.get("stop-test") is None


@pytest.mark.asyncio
@pytest.mark.parametrize("boundary", ["inspect", "pre_run", "gate_reset"])
async def test_resume_rechecks_stop_ownership_after_slow_await(monkeypatch, boundary):
    manager = TeamRuntimeManager()
    agent = SimpleNamespace(stop_coordination=AsyncMock())
    entry = ActiveTeam(team_name="t", agent=agent, current_session_id="s", state=RuntimeState.PAUSED)
    await manager.pool.add(entry)
    session = SimpleNamespace(get_session_id=lambda: "s")
    monkeypatch.setattr(TeamRuntimeManager, "_build_session", staticmethod(lambda value: session))

    async def inspect(*args):
        if boundary == "inspect":
            entry.state = RuntimeState.STOPPING
        return True, True, None

    async def pre_run(*args):
        if boundary == "pre_run":
            entry.state = RuntimeState.STOPPING

    async def reset():
        if boundary == "gate_reset":
            entry.state = RuntimeState.STOPPING

    monkeypatch.setattr(manager, "_inspect_session", inspect)
    monkeypatch.setattr(manager, "_pre_run_with_inputs", pre_run)
    monkeypatch.setattr(entry.interact_gate, "reset", reset)
    with pytest.raises(Exception, match="still stopping"):
        await manager.activate(SimpleNamespace(team_name="t"), "s", "continue")
    assert entry.state is RuntimeState.STOPPING
