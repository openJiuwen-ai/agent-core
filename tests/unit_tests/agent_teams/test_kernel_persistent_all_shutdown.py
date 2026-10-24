# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Persistent teams must keep the row when every teammate is already SHUTDOWN."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openjiuwen.agent_teams.agent.coordination.kernel import CoordinationKernel
from openjiuwen.agent_teams.schema.status import MemberStatus
from openjiuwen.agent_teams.schema.team import MemberRosterEntry

pytestmark = pytest.mark.level0


def _kernel() -> CoordinationKernel:
    return CoordinationKernel.__new__(CoordinationKernel)


@pytest.mark.asyncio
async def test_persistent_leader_start_keeps_team_when_all_shutdown():
    clean_team = AsyncMock(return_value=True)
    recover_team = AsyncMock(return_value=[])
    host = SimpleNamespace(
        lifecycle="persistent",
        member_name="leader",
        recover_team=recover_team,
    )
    infra = SimpleNamespace(
        team_backend=SimpleNamespace(
            team_name="persist-team",
            list_member_roster=AsyncMock(
                return_value=[
                    MemberRosterEntry(
                        member_name="dev",
                        display_name="Dev",
                        status=MemberStatus.SHUTDOWN.value,
                    )
                ]
            ),
            clean_team=clean_team,
        )
    )
    present = await _kernel()._recover_existing_team_on_start(host, infra)
    assert present is True
    clean_team.assert_not_awaited()
    recover_team.assert_awaited_once()


@pytest.mark.asyncio
async def test_temporary_leader_start_cleans_when_all_shutdown():
    clean_team = AsyncMock(return_value=True)
    recover_team = AsyncMock(return_value=[])
    host = SimpleNamespace(
        lifecycle="temporary",
        member_name="leader",
        recover_team=recover_team,
    )
    infra = SimpleNamespace(
        team_backend=SimpleNamespace(
            team_name="temp-team",
            list_member_roster=AsyncMock(
                return_value=[
                    MemberRosterEntry(
                        member_name="dev",
                        display_name="Dev",
                        status=MemberStatus.SHUTDOWN.value,
                    )
                ]
            ),
            clean_team=clean_team,
        )
    )
    present = await _kernel()._recover_existing_team_on_start(host, infra)
    assert present is False
    clean_team.assert_awaited_once()
    recover_team.assert_not_awaited()
