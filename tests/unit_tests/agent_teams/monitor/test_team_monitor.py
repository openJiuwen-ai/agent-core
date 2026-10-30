# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from unittest.mock import MagicMock

import pytest

from openjiuwen.agent_teams.monitor.team_monitor import TeamMonitor


def _build_monitor(team_agent: MagicMock) -> TeamMonitor:
    return TeamMonitor(
        team_name="team",
        session_id="session-1",
        db=MagicMock(),
        team_agent=team_agent,
    )


def test_team_agent_exposes_bound_leader() -> None:
    leader = MagicMock()

    monitor = _build_monitor(leader)

    assert monitor.team_agent is leader


@pytest.mark.asyncio
async def test_team_agent_distinguishes_rebuilt_leader() -> None:
    old_leader = MagicMock()
    new_leader = MagicMock()
    old_monitor = _build_monitor(old_leader)
    new_monitor = _build_monitor(new_leader)

    await old_monitor.start()
    old_leader.add_event_listener.assert_called_once()

    assert old_monitor.team_agent is not new_monitor.team_agent
    await old_monitor.stop()
    old_leader.remove_event_listener.assert_called_once()
    new_leader.remove_event_listener.assert_not_called()
