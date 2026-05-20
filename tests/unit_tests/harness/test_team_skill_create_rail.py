# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
# pylint: disable=protected-access

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from openjiuwen.agent_evolving.trajectory import ToolCallDetail, TrajectoryBuilder, TrajectoryStep
from openjiuwen.core.single_agent.rail.base import InvokeInputs
from openjiuwen.harness.rails.evolution.evolution_rail import EvolutionTriggerPoint
from openjiuwen.harness.rails.skills.team_skill_create_rail import TeamSkillCreateRail


def _make_rail(tmp_path, *, auto_trigger=True) -> TeamSkillCreateRail:
    return TeamSkillCreateRail(
        skills_dir=str(tmp_path / "skills"),
        auto_trigger=auto_trigger,
    )


def _set_builder_tool_calls(rail, tool_names: list[str]) -> None:
    """Set up a mock TrajectoryBuilder with given tool calls."""
    builder = TrajectoryBuilder(session_id="test", source="test")
    _append_builder_tool_calls(builder, tool_names)
    rail._builder = builder


def _append_builder_tool_calls(builder: TrajectoryBuilder, tool_names: list[str]) -> None:
    """Append mock tool calls to an existing TrajectoryBuilder."""
    for name in tool_names:
        step = TrajectoryStep(
            kind="tool",
            detail=ToolCallDetail(tool_name=name, call_args="{}", call_result="ok"),
            meta={"operator_id": name},
        )
        builder.record_step(step)


def _make_invoke_ctx(agent: MagicMock, conversation_id: str = "test") -> MagicMock:
    ctx = MagicMock()
    ctx.agent = agent
    ctx.inputs = InvokeInputs(query="run team", conversation_id=conversation_id)
    return ctx


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
    async def test_after_task_iteration_follow_up_when_threshold_met_after_completion(self, tmp_path):
        rail = _make_rail(tmp_path)
        _set_builder_tool_calls(rail, ["spawn_member"] * 3)

        controller = MagicMock()
        controller.enqueue_follow_up = MagicMock()
        agent = MagicMock()
        agent._loop_controller = controller
        ctx = _make_invoke_ctx(agent)

        assert await rail.notify_team_completed() is True
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
        ctx = _make_invoke_ctx(agent)

        assert await rail.notify_team_completed() is True
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
        ctx = _make_invoke_ctx(agent)

        assert await rail.notify_team_completed() is False
        await rail._on_after_task_iteration(ctx)

        controller.enqueue_follow_up.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_follow_up_until_team_completed(self, tmp_path):
        rail = _make_rail(tmp_path)
        _set_builder_tool_calls(rail, ["spawn_member"] * 3)

        controller = MagicMock()
        controller.enqueue_follow_up = MagicMock()
        agent = MagicMock()
        agent._loop_controller = controller
        ctx = _make_invoke_ctx(agent)

        await rail._on_after_task_iteration(ctx)
        await rail._on_after_invoke(ctx)

        controller.enqueue_follow_up.assert_not_called()

    @pytest.mark.asyncio
    async def test_follow_up_after_team_completed_mark(self, tmp_path):
        rail = _make_rail(tmp_path)
        _set_builder_tool_calls(rail, ["spawn_member"] * 3)

        controller = MagicMock()
        controller.enqueue_follow_up = MagicMock()
        agent = MagicMock()
        agent._loop_controller = controller
        ctx = _make_invoke_ctx(agent)

        result = await rail.notify_team_completed()
        await rail._on_after_invoke(ctx)

        assert result is True
        controller.enqueue_follow_up.assert_called_once()
        prompt = controller.enqueue_follow_up.call_args[0][0]
        assert "team-skill-creator" in prompt

    @pytest.mark.asyncio
    async def test_completion_mark_does_not_apply_to_new_session(self, tmp_path):
        rail = _make_rail(tmp_path)
        _set_builder_tool_calls(rail, ["spawn_member"] * 3)

        controller = MagicMock()
        controller.enqueue_follow_up = MagicMock()
        agent = MagicMock()
        agent._loop_controller = controller

        assert await rail.notify_team_completed() is True

        rail._builder = TrajectoryBuilder(session_id="other-session", source="test")
        ctx = _make_invoke_ctx(agent, conversation_id="other-session")
        await rail._on_after_invoke(ctx)

        controller.enqueue_follow_up.assert_not_called()

    @pytest.mark.asyncio
    async def test_follow_up_only_once_per_completed_session(self, tmp_path):
        rail = _make_rail(tmp_path)
        _set_builder_tool_calls(rail, ["spawn_member"] * 3)

        controller = MagicMock()
        controller.enqueue_follow_up = MagicMock()
        agent = MagicMock()
        agent._loop_controller = controller
        ctx = _make_invoke_ctx(agent)

        assert await rail.notify_team_completed() is True
        await rail._on_after_invoke(ctx)
        assert await rail.notify_team_completed() is True
        await rail._on_after_invoke(ctx)

        controller.enqueue_follow_up.assert_called_once()

    @pytest.mark.asyncio
    async def test_follow_up_can_repeat_in_same_session_after_new_team_run(self, tmp_path):
        rail = _make_rail(tmp_path)
        _set_builder_tool_calls(rail, ["spawn_member"] * 2)

        controller = MagicMock()
        controller.enqueue_follow_up = MagicMock()
        agent = MagicMock()
        agent._loop_controller = controller
        ctx = _make_invoke_ctx(agent)

        assert await rail.notify_team_completed() is True
        await rail._on_after_invoke(ctx)

        _append_builder_tool_calls(rail._builder, ["spawn_member"] * 2)
        assert await rail.notify_team_completed() is True
        await rail._on_after_invoke(ctx)

        assert controller.enqueue_follow_up.call_count == 2

    @pytest.mark.parametrize("skill_kind", ["team-skill", "swarm-skill"])
    @pytest.mark.asyncio
    async def test_no_follow_up_when_existing_team_skill_was_used(self, tmp_path, skill_kind):
        skills_dir = tmp_path / "skills"
        team_skill_dir = skills_dir / "research-team"
        team_skill_dir.mkdir(parents=True)
        (team_skill_dir / "SKILL.md").write_text(
            f"---\nname: research-team\nkind: {skill_kind}\nroles:\n  - name: planner\n    kind: ai_agent\n---\n# Research Team\n",
            encoding="utf-8",
        )
        rail = TeamSkillCreateRail(skills_dir=str(skills_dir))
        _set_builder_tool_calls(rail, ["spawn_member"] * 2)
        rail._builder.record_step(
            TrajectoryStep(
                kind="tool",
                detail=ToolCallDetail(
                    tool_name="skill_tool",
                    call_args={"skill_name": "research-team", "relative_file_path": "SKILL.md"},
                    call_result="loaded",
                ),
                meta={"operator_id": "skill_tool"},
            )
        )

        controller = MagicMock()
        controller.enqueue_follow_up = MagicMock()
        agent = MagicMock()
        agent._loop_controller = controller
        ctx = _make_invoke_ctx(agent)

        assert await rail.notify_team_completed() is True
        await rail._on_after_invoke(ctx)

        controller.enqueue_follow_up.assert_not_called()
