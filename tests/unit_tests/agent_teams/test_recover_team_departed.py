# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""recover_team must not resurrect soft-stopped members or strand passive humans."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openjiuwen.agent_teams.agent.recovery_manager import RecoveryManager
from openjiuwen.agent_teams.schema.status import MemberStatus
from openjiuwen.agent_teams.schema.team import TeamRole

pytestmark = pytest.mark.level0


def _manager(members, *, restart=None, transitions=None) -> RecoveryManager:
    transitions = transitions if transitions is not None else []

    async def _transition(name, team, frm, to):
        transitions.append((name, frm, to))
        return True

    team_backend = SimpleNamespace(
        restore_external_cli_specs_from_db=AsyncMock(),
        list_member_roster=AsyncMock(return_value=members),
        db=SimpleNamespace(
            member=SimpleNamespace(
                update_member_status=AsyncMock(return_value=True),
                try_transition_member_status=_transition,
            )
        ),
    )
    spawn_manager = SimpleNamespace(
        has_live_handle=lambda _name: False,
        restart_teammate=restart or AsyncMock(return_value=True),
    )
    configurator = SimpleNamespace(
        member_name="leader",
        team_name="team",
        team_backend=team_backend,
        role=TeamRole.LEADER,
    )
    return RecoveryManager(configurator, spawn_manager)


@pytest.mark.asyncio
async def test_recover_skips_shutdown_members():
    restart = AsyncMock(return_value=True)
    mgr = _manager(
        [
            SimpleNamespace(
                member_name="gone",
                status=MemberStatus.SHUTDOWN.value,
                role=TeamRole.TEAMMATE.value,
            ),
            SimpleNamespace(
                member_name="leaving",
                status=MemberStatus.SHUTDOWN_REQUESTED.value,
                role=TeamRole.TEAMMATE.value,
            ),
            SimpleNamespace(
                member_name="alive",
                status=MemberStatus.READY.value,
                role=TeamRole.TEAMMATE.value,
            ),
        ],
        restart=restart,
    )
    restarted = await mgr.recover_team()
    assert restarted == ["alive"]
    restart.assert_awaited_once_with("alive")


@pytest.mark.asyncio
async def test_recover_settles_passive_restarting_without_harness_restart():
    restart = AsyncMock(return_value=True)
    transitions: list = []
    mgr = _manager(
        [
            SimpleNamespace(
                member_name="pm1",
                status=MemberStatus.RESTARTING.value,
                role=TeamRole.PASSIVE_HUMAN.value,
            ),
            SimpleNamespace(
                member_name="pm2",
                status=MemberStatus.READY.value,
                role=TeamRole.PASSIVE_HUMAN.value,
            ),
            SimpleNamespace(
                member_name="pm3",
                status=MemberStatus.SHUTDOWN.value,
                role=TeamRole.PASSIVE_HUMAN.value,
            ),
        ],
        restart=restart,
        transitions=transitions,
    )
    restarted = await mgr.recover_team()
    assert restarted == []
    restart.assert_not_awaited()
    assert transitions == [
        ("pm1", MemberStatus.RESTARTING, MemberStatus.READY),
    ]
