# coding: utf-8
"""Teammate cold resume replays shared pending_resume when harness is IDLE."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from openjiuwen.agent_teams.agent.coordination.kernel import CoordinationKernel
from openjiuwen.agent_teams.harness.state import HarnessState
from openjiuwen.agent_teams.schema.team import TeamRole


@pytest.mark.asyncio
async def test_teammate_idle_cold_resumes_from_pending_resume() -> None:
    """IDLE teammate after restart must not skip shared pending_resume."""
    host = MagicMock()
    host.member_name = "market-researcher"
    host.role = TeamRole.TEAMMATE
    host.team_name = "t1"
    host.resources.harness = MagicMock()
    host.resources.harness.state = HarnessState.IDLE
    host.stream_controller.resume_agent = AsyncMock()

    kernel = CoordinationKernel.__new__(CoordinationKernel)
    kernel._host = host
    kernel._read_pending_resume = MagicMock(return_value={"query": "continue-task-board"})
    kernel._clear_pending_resume = MagicMock()

    await kernel.resume_paused_round()

    host.stream_controller.resume_agent.assert_awaited_once_with(query="continue-task-board")
    kernel._clear_pending_resume.assert_called_once()
