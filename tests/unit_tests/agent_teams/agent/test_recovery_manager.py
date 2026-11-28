# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unit tests for team member lifecycle recovery policy."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from openjiuwen.agent_teams.agent.recovery_manager import RecoveryManager
from openjiuwen.agent_teams.schema.status import MemberStatus


def _member(name: str, status: MemberStatus) -> SimpleNamespace:
    return SimpleNamespace(member_name=name, status=status.value)


@pytest.mark.asyncio
@pytest.mark.level0
async def test_recover_team_skips_shutdown_states() -> None:
    """Team recovery leaves requested and completed shutdown states untouched."""
    member_dao = SimpleNamespace(claim_member_restart=AsyncMock(return_value=True))
    backend = SimpleNamespace(
        restore_external_cli_specs_from_db=AsyncMock(),
        list_member_roster=AsyncMock(
            return_value=[
                _member("leader", MemberStatus.READY),
                _member("departed", MemberStatus.SHUTDOWN),
                _member("orphan", MemberStatus.SHUTDOWN_REQUESTED),
            ]
        ),
        db=SimpleNamespace(member=member_dao),
    )
    spawn_manager = MagicMock()
    spawn_manager.has_live_handle.return_value = False
    spawn_manager.cleanup_teammate = AsyncMock()
    spawn_manager.restart_teammate = AsyncMock(return_value=True)
    configurator = SimpleNamespace(
        team_backend=backend,
        member_name="leader",
        team_name="team",
    )

    restarted = await RecoveryManager(configurator, spawn_manager).recover_team()

    assert restarted == []
    member_dao.claim_member_restart.assert_not_awaited()
    spawn_manager.cleanup_teammate.assert_not_awaited()
    spawn_manager.restart_teammate.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.level0
async def test_recover_team_restarts_error_but_not_departed_member() -> None:
    """ERROR remains recoverable while an explicitly departed member stays down."""
    member_dao = SimpleNamespace(claim_member_restart=AsyncMock(return_value=True))
    backend = SimpleNamespace(
        restore_external_cli_specs_from_db=AsyncMock(),
        list_member_roster=AsyncMock(
            return_value=[
                _member("leader", MemberStatus.READY),
                _member("failed", MemberStatus.ERROR),
                _member("departed", MemberStatus.SHUTDOWN),
            ]
        ),
        is_passive_human=AsyncMock(return_value=False),
        get_member=AsyncMock(return_value=SimpleNamespace(role="teammate")),
        db=SimpleNamespace(member=member_dao),
    )
    spawn_manager = MagicMock()
    spawn_manager.has_live_handle.return_value = False
    spawn_manager.cleanup_teammate = AsyncMock()
    spawn_manager.restart_teammate = AsyncMock(return_value=True)
    configurator = SimpleNamespace(
        team_backend=backend,
        member_name="leader",
        team_name="team",
    )

    restarted = await RecoveryManager(configurator, spawn_manager).recover_team()

    assert restarted == ["failed"]
    member_dao.claim_member_restart.assert_awaited_once_with(
        "failed",
        "team",
        MemberStatus.ERROR,
    )
    spawn_manager.restart_teammate.assert_awaited_once_with("failed")


@pytest.mark.asyncio
@pytest.mark.level0
async def test_recover_team_restarts_only_members_claimed_from_ready_or_busy() -> None:
    """Team recovery skips a teammate whose status changed before its claim."""
    member_dao = SimpleNamespace(claim_member_restart=AsyncMock(side_effect=[True, False]))
    backend = SimpleNamespace(
        restore_external_cli_specs_from_db=AsyncMock(),
        list_member_roster=AsyncMock(
            return_value=[
                _member("leader", MemberStatus.READY),
                _member("ready_member", MemberStatus.READY),
                _member("busy_member", MemberStatus.BUSY),
            ]
        ),
        is_passive_human=AsyncMock(return_value=False),
        get_member=AsyncMock(return_value=SimpleNamespace(role="teammate")),
        db=SimpleNamespace(member=member_dao),
    )
    spawn_manager = MagicMock()
    spawn_manager.has_live_handle.return_value = False
    spawn_manager.restart_teammate = AsyncMock(return_value=True)
    configurator = SimpleNamespace(
        team_backend=backend,
        member_name="leader",
        team_name="team",
    )

    restarted = await RecoveryManager(configurator, spawn_manager).recover_team()

    assert restarted == ["ready_member"]
    assert [call.args for call in member_dao.claim_member_restart.await_args_list] == [
        ("ready_member", "team", MemberStatus.READY),
        ("busy_member", "team", MemberStatus.BUSY),
    ]
    spawn_manager.restart_teammate.assert_awaited_once_with("ready_member")


@pytest.mark.asyncio
@pytest.mark.level0
async def test_recover_team_leaves_dormant_members_asleep() -> None:
    """A retired (DORMANT) member wakes only when addressed, never from a cold recovery sweep (F_118)."""
    member_dao = SimpleNamespace(claim_member_restart=AsyncMock(return_value=True))
    backend = SimpleNamespace(
        restore_external_cli_specs_from_db=AsyncMock(),
        list_member_roster=AsyncMock(
            return_value=[
                _member("leader", MemberStatus.READY),
                _member("failed", MemberStatus.ERROR),
                _member("sleepy", MemberStatus.DORMANT),
            ]
        ),
        is_passive_human=AsyncMock(return_value=False),
        get_member=AsyncMock(return_value=SimpleNamespace(role="teammate")),
        db=SimpleNamespace(member=member_dao),
    )
    spawn_manager = MagicMock()
    spawn_manager.has_live_handle.return_value = False
    spawn_manager.cleanup_teammate = AsyncMock()
    spawn_manager.restart_teammate = AsyncMock(return_value=True)
    configurator = SimpleNamespace(team_backend=backend, member_name="leader", team_name="team")

    restarted = await RecoveryManager(configurator, spawn_manager).recover_team()

    assert restarted == ["failed"]
    spawn_manager.restart_teammate.assert_awaited_once_with("failed")


@pytest.mark.asyncio
async def test_recovery_uses_persisted_role_with_lightweight_roster():
    from openjiuwen.agent_teams.schema.team import MemberRosterEntry

    roster = [MemberRosterEntry(member_name="colleague", display_name="Colleague", status="ready"),
              MemberRosterEntry(member_name="expert", display_name="Expert", status="ready")]
    member_dao = SimpleNamespace(claim_member_restart=AsyncMock(return_value=True))
    backend = SimpleNamespace(
        restore_external_cli_specs_from_db=AsyncMock(), list_member_roster=AsyncMock(return_value=roster),
        get_member=AsyncMock(side_effect=[SimpleNamespace(role="host_managed"), SimpleNamespace(role="teammate")]),
        is_passive_human=AsyncMock(return_value=False), db=SimpleNamespace(member=member_dao),
    )
    spawn = MagicMock()
    spawn.has_live_handle.return_value = False
    spawn.restart_teammate = AsyncMock(return_value=True)
    configurator = SimpleNamespace(team_backend=backend, member_name="leader", team_name="team")
    restarted = await RecoveryManager(configurator, spawn).recover_team()
    assert restarted == ["expert"]
    spawn.restart_teammate.assert_awaited_once_with("expert")
    member_dao.claim_member_restart.assert_awaited_once_with("expert", "team", MemberStatus.READY)
