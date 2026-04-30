# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
# pylint: disable=protected-access

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from openjiuwen.harness.rails.evolution.evolution_rail import EvolutionTriggerPoint
from openjiuwen.harness.rails.skills.team_skill_create_rail import TeamSkillCreateRail


def _make_rail(tmp_path, *, auto_trigger=True) -> TeamSkillCreateRail:
    return TeamSkillCreateRail(
        skills_dir=str(tmp_path / "skills"),
        auto_trigger=auto_trigger,
    )


def _set_builder_tool_calls(rail, tool_names: list[str]) -> None:
    """Set up a mock TrajectoryBuilder with given tool calls."""
    from openjiuwen.agent_evolving.trajectory import TrajectoryBuilder, TrajectoryStep, ToolCallDetail

    builder = TrajectoryBuilder(session_id="test", source="test")
    for name in tool_names:
        step = TrajectoryStep(
            kind="tool",
            detail=ToolCallDetail(tool_name=name, call_args="{}", call_result="ok"),
            meta={"operator_id": name},
        )
        builder.record_step(step)
    rail._builder = builder


class TestTeamSkillCreateRailConstructor:
    def test_default_values(self, tmp_path):
        rail = _make_rail(tmp_path)
        assert rail._auto_trigger is True
        assert rail._min_team_members == 2
        assert rail._evolution_trigger == EvolutionTriggerPoint.NONE

    def test_custom_min_members(self, tmp_path):
        rail = TeamSkillCreateRail(
            skills_dir=str(tmp_path / "skills"),
            min_team_members_for_create=4,
        )
        assert rail._min_team_members == 4


class TestTeamSkillCreateRailThresholdCheck:
    def test_should_propose_when_spawn_meets_threshold(self, tmp_path):
        rail = _make_rail(tmp_path, auto_trigger=True)
        _set_builder_tool_calls(rail, ["spawn_member"] * 3)
        assert rail._should_propose_new_team_skill() is True

    def test_should_not_propose_when_below_threshold(self, tmp_path):
        rail = _make_rail(tmp_path, auto_trigger=True)
        _set_builder_tool_calls(rail, ["spawn_member"])
        assert rail._should_propose_new_team_skill() is False

    def test_should_not_propose_when_no_builder(self, tmp_path):
        rail = _make_rail(tmp_path, auto_trigger=True)
        rail._builder = None
        assert rail._should_propose_new_team_skill() is False

    def test_empty_steps(self, tmp_path):
        rail = _make_rail(tmp_path, auto_trigger=True)
        _set_builder_tool_calls(rail, [])
        assert rail._should_propose_new_team_skill() is False


class TestTeamSkillCreateRailOnAfterTaskIteration:
    """_on_after_task_iteration detects thresholds and triggers follow_up."""

    @pytest.mark.asyncio
    async def test_follow_up_when_threshold_met(self, tmp_path):
        rail = _make_rail(tmp_path)
        _set_builder_tool_calls(rail, ["spawn_member"] * 3)

        controller = MagicMock()
        controller.enqueue_follow_up = MagicMock()
        agent = MagicMock()
        agent._loop_controller = controller
        ctx = MagicMock()
        ctx.agent = agent

        await rail._on_after_task_iteration(ctx)

        controller.enqueue_follow_up.assert_called_once()
        call_args = controller.enqueue_follow_up.call_args[0][0]
        assert "team-skill-creator" in call_args
        assert "ask_user" in call_args
        assert str(rail._skills_dir) in call_args
        assert "必须" in call_args  # strong constraint keyword

    @pytest.mark.asyncio
    async def test_no_follow_up_when_below_threshold(self, tmp_path):
        rail = _make_rail(tmp_path)
        _set_builder_tool_calls(rail, ["spawn_member"])

        controller = MagicMock()
        controller.enqueue_follow_up = MagicMock()
        agent = MagicMock()
        agent._loop_controller = controller
        ctx = MagicMock()
        ctx.agent = agent

        await rail._on_after_task_iteration(ctx)

        controller.enqueue_follow_up.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_follow_up_when_auto_trigger_false(self, tmp_path):
        rail = _make_rail(tmp_path, auto_trigger=False)
        _set_builder_tool_calls(rail, ["spawn_member"] * 3)

        controller = MagicMock()
        controller.enqueue_follow_up = MagicMock()
        agent = MagicMock()
        agent._loop_controller = controller
        ctx = MagicMock()
        ctx.agent = agent

        await rail._on_after_task_iteration(ctx)

        controller.enqueue_follow_up.assert_not_called()
