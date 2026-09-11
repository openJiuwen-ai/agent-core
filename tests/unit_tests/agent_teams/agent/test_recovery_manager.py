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
    """Cold recovery leaves requested and completed shutdown states untouched."""
    member_dao = SimpleNamespace(update_member_status=AsyncMock(return_value=True))
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
    member_dao.update_member_status.assert_not_awaited()
    spawn_manager.cleanup_teammate.assert_not_awaited()
    spawn_manager.restart_teammate.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.level0
async def test_recover_team_restarts_error_but_not_departed_member() -> None:
    """ERROR remains recoverable while an explicitly departed member stays down."""
    member_dao = SimpleNamespace(update_member_status=AsyncMock(return_value=True))
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
    member_dao.update_member_status.assert_awaited_once_with(
        "failed",
        "team",
        MemberStatus.RESTARTING.value,
    )
    spawn_manager.restart_teammate.assert_awaited_once_with("failed")
