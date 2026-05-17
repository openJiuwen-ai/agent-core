# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unit tests for TeamRuntimeManager.finalize_member."""

from unittest.mock import (
    AsyncMock,
    MagicMock,
)

import pytest

from openjiuwen.agent_teams.agent.member import TeamMember
from openjiuwen.agent_teams.runtime.manager import TeamRuntimeManager
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