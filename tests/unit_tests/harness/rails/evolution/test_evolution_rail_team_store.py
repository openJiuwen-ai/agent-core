# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Unit tests for EvolutionRail team_trajectory_store dual-write."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from openjiuwen.agent_evolving.trajectory import InMemoryTrajectoryStore
from openjiuwen.agent_evolving.trajectory.types import (
    ToolCallDetail,
    TrajectoryStep,
)
from openjiuwen.core.single_agent.rail.base import InvokeInputs
from openjiuwen.harness.rails.evolution.evolution_rail import EvolutionRail


@dataclass
class _MockCard:
    id: str = "test-agent"


@dataclass
class _MockAgent:
    card: _MockCard = field(default_factory=_MockCard)


@dataclass
class _MockCtx:
    agent: Any = field(default_factory=_MockAgent)
    inputs: Any = field(
        default_factory=lambda: InvokeInputs(query="test query", conversation_id="test-session"),
    )


class TestEvolutionRailTeamTrajectory:
    """Tests for team_trajectory_store dual-write behavior."""

    @pytest.fixture
    def rail_with_team_store(self):
        personal = InMemoryTrajectoryStore()
        team = InMemoryTrajectoryStore()
        rail = EvolutionRail(
            trajectory_store=personal,
            team_trajectory_store=team,
            async_evolution=False,
        )
        return rail, personal, team

    @pytest.fixture
    def rail_without_team_store(self):
        personal = InMemoryTrajectoryStore()
        rail = EvolutionRail(
            trajectory_store=personal,
            team_trajectory_store=None,
            async_evolution=False,
        )
        return rail, personal

    @pytest.mark.asyncio
    async def test_save_called_twice_with_team_store(self, rail_with_team_store):
        """When team_trajectory_store is set, save is called on both stores."""
        rail, personal, team = rail_with_team_store
        ctx = _MockCtx()
        await rail.before_invoke(ctx)
        assert rail._builder is not None
        rail._builder.record_step(
            TrajectoryStep(
                kind="tool",
                detail=ToolCallDetail(tool_name="read_file"),
            )
        )
        await rail.after_invoke(ctx)

        assert len(personal.query()) == 1
        assert len(team.query()) == 1

    @pytest.mark.asyncio
    async def test_save_called_once_without_team_store(self, rail_without_team_store):
        """When team_trajectory_store is None, save is only called once."""
        rail, personal = rail_without_team_store
        ctx = _MockCtx()
        await rail.before_invoke(ctx)
        await rail.after_invoke(ctx)

        assert len(personal.query()) == 1

    def test_team_store_none_by_default(self):
        """team_trajectory_store defaults to None."""
        rail = EvolutionRail()
        assert rail._team_trajectory_store is None

    @pytest.mark.asyncio
    async def test_trajectory_has_member_id(self, rail_with_team_store):
        """Trajectory saved to team store contains member_id in meta."""
        rail, personal, team = rail_with_team_store
        ctx = _MockCtx()
        await rail.before_invoke(ctx)
        assert rail._builder.member_id == "test-agent"

        rail._builder.record_step(
            TrajectoryStep(
                kind="tool",
                detail=ToolCallDetail(tool_name="read_file"),
            )
        )
        await rail.after_invoke(ctx)

        traj = team.query()[0]
        assert traj.meta.get("member_id") == "test-agent"
