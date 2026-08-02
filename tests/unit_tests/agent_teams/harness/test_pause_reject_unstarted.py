# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Pause must reject not-yet-started tools and block new spawns."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from openjiuwen.agent_teams.harness.snapshot_rail import PhaseSnapshotRail
from openjiuwen.agent_teams.harness.state import ActiveRound, HarnessState, RoundPhase
from openjiuwen.agent_teams.agent.spawn_manager import SpawnManager
from openjiuwen.core.single_agent.rail.base import ToolCallInputs


def _active_round(*, pause_requested: bool = False) -> ActiveRound:
    return ActiveRound(
        round_id=1,
        task_id="t1",
        original_query="q",
        deep_agent=MagicMock(),
        task=MagicMock(done=MagicMock(return_value=True)),
        steering_queue=MagicMock(),
        pause_requested=pause_requested,
    )


@pytest.mark.asyncio
async def test_before_tool_call_rejects_unstarted_when_pause_armed() -> None:
    active = _active_round(pause_requested=True)
    harness = SimpleNamespace(active_round=active)
    rail = PhaseSnapshotRail(harness)
    tool_call = SimpleNamespace(id="call-1", name="create_task")
    ctx = SimpleNamespace(
        extra={},
        inputs=ToolCallInputs(tool_call=tool_call, tool_name="create_task"),
    )

    await rail.before_tool_call(ctx)

    assert ctx.extra.get("_skip_tool") is True
    assert active.tool_started is False
    assert active.iter_phase is RoundPhase.TOOL
    assert ctx.inputs.tool_result["paused"] is True
    assert "paused" in str(ctx.inputs.tool_msg.content)


@pytest.mark.asyncio
async def test_before_tool_call_marks_started_when_not_paused() -> None:
    active = _active_round(pause_requested=False)
    harness = SimpleNamespace(active_round=active)
    rail = PhaseSnapshotRail(harness)
    ctx = SimpleNamespace(
        extra={},
        inputs=ToolCallInputs(
            tool_call=SimpleNamespace(id="c2", name="view_task"),
            tool_name="view_task",
        ),
    )

    await rail.before_tool_call(ctx)

    assert ctx.extra.get("_skip_tool") is None
    assert active.tool_started is True
    assert active.iter_phase is RoundPhase.TOOL


@pytest.mark.asyncio
async def test_spawn_teammate_refuses_while_pausing() -> None:
    harness = SimpleNamespace(state=HarnessState.PAUSING, active_round=None)
    team_agent = SimpleNamespace(resources=SimpleNamespace(harness=harness))
    mgr = SpawnManager(
        state=MagicMock(),
        configurator=SimpleNamespace(member_name="leader"),
        team_agent_getter=lambda: team_agent,
    )
    ctx = SimpleNamespace(member_name="analyst")

    result = await mgr.spawn_teammate(ctx)

    assert result is None
    assert mgr._spawning == set()


@pytest.mark.asyncio
async def test_spawn_teammate_refuses_when_pause_requested() -> None:
    active = _active_round(pause_requested=True)
    harness = SimpleNamespace(state=HarnessState.RUNNING, active_round=active)
    team_agent = SimpleNamespace(resources=SimpleNamespace(harness=harness))
    mgr = SpawnManager(
        state=MagicMock(),
        configurator=SimpleNamespace(member_name="leader"),
        team_agent_getter=lambda: team_agent,
    )

    result = await mgr.spawn_teammate(SimpleNamespace(member_name="writer"))

    assert result is None
