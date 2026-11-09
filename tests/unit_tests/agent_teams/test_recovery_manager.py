# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""RecoveryManager.recover_team 的拉起面收窄。

按需拉起下 UNSTARTED 是「待委派才拉起」的正常名册形态，
不是崩溃待恢复。recover_team 必须跳过它：不拉起、不写 RESTARTING
（顺带消掉 UNSTARTED->RESTARTING 非法迁移噪）。
待委派成员的拉起由委派触发 + stale-pending 双兜底承担。
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openjiuwen.agent_teams.agent.recovery_manager import RecoveryManager
from openjiuwen.agent_teams.schema.status import MemberStatus


def _make_recovery_manager(roster):
    team_backend = SimpleNamespace(
        restore_external_cli_specs_from_db=AsyncMock(),
        list_member_roster=AsyncMock(return_value=roster),
        db=SimpleNamespace(
            member=SimpleNamespace(update_member_status=AsyncMock()),
        ),
    )
    configurator = SimpleNamespace(
        member_name="leader",
        team_backend=team_backend,
        team_name="t",
    )
    spawn_manager = SimpleNamespace(
        has_live_handle=lambda name: False,
        restart_teammate=AsyncMock(return_value=True),
    )
    return RecoveryManager(configurator, spawn_manager), team_backend, spawn_manager


@pytest.mark.asyncio
@pytest.mark.level0
async def test_recover_team_skips_unstarted_members():
    """UNSTARTED 成员（懒拉起常态）recover 不拉起、不写 RESTARTING。"""
    roster = [
        SimpleNamespace(member_name="leader", status=MemberStatus.PAUSED.value),
        SimpleNamespace(member_name="m-paused", status=MemberStatus.PAUSED.value),
        SimpleNamespace(member_name="m-unstarted", status=MemberStatus.UNSTARTED.value),
    ]
    mgr, team_backend, spawn_manager = _make_recovery_manager(roster)

    restarted = await mgr.recover_team()

    assert restarted == ["m-paused"]
    spawn_manager.restart_teammate.assert_awaited_once_with("m-paused")
    written = [
        call.args[0]
        for call in team_backend.db.member.update_member_status.await_args_list
    ]
    assert written == ["m-paused"], "UNSTARTED 成员不得写 RESTARTING"


@pytest.mark.asyncio
@pytest.mark.level0
async def test_recover_team_still_restarts_previously_running_members():
    """pause 前在跑的成员（PAUSED）恢复时照常拉起——收窄只针对 UNSTARTED。"""
    roster = [
        SimpleNamespace(member_name="leader", status=MemberStatus.PAUSED.value),
        SimpleNamespace(member_name="m-1", status=MemberStatus.PAUSED.value),
        SimpleNamespace(member_name="m-2", status=MemberStatus.ERROR.value),
    ]
    mgr, team_backend, spawn_manager = _make_recovery_manager(roster)

    restarted = await mgr.recover_team()

    assert restarted == ["m-1", "m-2"]
    assert spawn_manager.restart_teammate.await_count == 2
