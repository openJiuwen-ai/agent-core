# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
# pylint: disable=protected-access

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from openjiuwen.agent_evolving.prompts.sections import (
    build_team_evolution_protocol_section,
    build_team_skill_creation_guidance_section,
    build_team_skill_creation_nudge_section,
)
from openjiuwen.agent_evolving.trajectory import ToolCallDetail, TrajectoryBuilder, TrajectoryStep
from openjiuwen.core.single_agent.rail.base import InvokeInputs
from openjiuwen.core.single_agent.skills.skill_manager import Skill
from openjiuwen.harness.prompts.builder import SystemPromptBuilder
from openjiuwen.harness.prompts.sections import SectionName
from openjiuwen.harness.rails.evolution.evolution_rail import EvolutionTriggerPoint
from openjiuwen.harness.rails.skills.skill_use_rail import SkillUseRail
from openjiuwen.harness.rails.skills.team_skill_create_rail import TeamSkillCreateRail


def _make_rail(tmp_path, *, auto_trigger=True) -> TeamSkillCreateRail:
    return TeamSkillCreateRail(
        skills_dir=str(tmp_path / "skills"),
        auto_trigger=auto_trigger,
    )


def _set_builder_tool_calls(rail, tool_names: list[str]) -> None:
    builder = TrajectoryBuilder(session_id="test", source="test")
    _append_builder_tool_calls(builder, tool_names)
    rail._builder = builder


def _append_builder_tool_calls(builder: TrajectoryBuilder, tool_names: list[str]) -> None:
    for name in tool_names:
        builder.record_step(
            TrajectoryStep(
                kind="tool",
                detail=ToolCallDetail(tool_name=name, call_args="{}", call_result="ok"),
                meta={"operator_id": name},
            )
        )


def _make_invoke_ctx(agent: MagicMock, conversation_id: str = "test") -> MagicMock:
    ctx = MagicMock()
    ctx.agent = agent
    ctx.inputs = InvokeInputs(query="run team", conversation_id=conversation_id)
    return ctx


def _make_agent_with_team_skill_creation_capability(tmp_path):
    skill_dir = tmp_path / "skills" / "swarmskill-creator"
    skill_dir.mkdir(parents=True, exist_ok=True)
    skill_rail = SkillUseRail(skills_dir=str(tmp_path / "skills"))
    skill_rail.skills = [
        Skill(
            name="swarmskill-creator",
            description="Create swarm skills",
            directory=Path(skill_dir),
        )
    ]

    agent = MagicMock()
    agent.system_prompt_builder = SystemPromptBuilder(language="cn")
    agent._registered_rails = [skill_rail]
    agent._pending_rails = []
    agent.ability_manager.list_tool_info = AsyncMock(return_value=[SimpleNamespace(name="skill_tool")])
    return agent


def _agent_with_controller(tmp_path):
    controller = MagicMock()
    controller.enqueue_follow_up = MagicMock()
    agent = _make_agent_with_team_skill_creation_capability(tmp_path)
    agent._loop_controller = controller
    return agent, controller


def _assert_team_follow_up_contract(prompt: str) -> None:
    assert prompt.startswith("<auto_team_skill_creation_followup>\n")
    assert prompt.endswith("\n</auto_team_skill_creation_followup>")
    assert "不是用户的新需求" in prompt
    assert "常驻提示词中的“团队技能沉淀自检”规则" in prompt
    assert "可复用团队流程" in prompt
    assert "协作流程" in prompt
    assert "角色分工" in prompt
    assert "交接方式" in prompt
    assert "并行推进方式" in prompt
    assert "汇总整合方式" in prompt
    assert "验收方式" in prompt
    assert "最多追加两句" in prompt
    assert "是否创建 Team/Swarm Skill" in prompt
    assert "不要提及自检、沉淀、无需创建、已检查、内部判断或本提醒" in prompt
    assert "自然承接刚完成的团队任务" in prompt
    assert "ask_user" not in prompt
    assert "swarmskill-creator" not in prompt
    assert "自动创建" not in prompt
    assert "interrupt" not in prompt
    assert "审批" not in prompt


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

    def test_trajectory_store_is_not_accepted(self, tmp_path):
        with pytest.raises(TypeError, match="trajectory_store"):
            TeamSkillCreateRail(
                skills_dir=str(tmp_path / "skills"),
                trajectory_store=MagicMock(),
            )


class TestTeamSkillCreateRailThresholdCheck:
    @pytest.mark.parametrize(
        "tool_name",
        [
            "spawn_member",
            "spawn_teammate",
            "spawn_human_agent",
            "spawn_bridge_agent",
            "spawn_external_cli",
            "team.spawn_teammate",
        ],
    )
    def test_should_propose_when_supported_spawn_tool_meets_threshold(self, tmp_path, tool_name):
        rail = _make_rail(tmp_path)
        _set_builder_tool_calls(rail, [tool_name] * 3)
        assert rail._should_propose_new_team_skill() is True

    def test_should_not_propose_when_below_threshold(self, tmp_path):
        rail = _make_rail(tmp_path)
        _set_builder_tool_calls(rail, ["spawn_member"])
        assert rail._should_propose_new_team_skill() is False

    @pytest.mark.parametrize("tool_name", ["send_message", "team.send_message", "view_task"])
    def test_non_spawn_tools_do_not_count(self, tmp_path, tool_name):
        rail = _make_rail(tmp_path)
        _set_builder_tool_calls(rail, [tool_name] * 3)
        assert rail._should_propose_new_team_skill() is False

    @pytest.mark.parametrize("tool_name", ["not_spawn_member", "team.spawn_member_extra"])
    def test_spawn_tool_names_require_exact_match(self, tmp_path, tool_name):
        rail = _make_rail(tmp_path)
        _set_builder_tool_calls(rail, [tool_name] * 3)
        assert rail._should_propose_new_team_skill() is False

    def test_should_count_mixed_spawn_calls(self, tmp_path):
        rail = _make_rail(tmp_path)
        _set_builder_tool_calls(
            rail,
            [
                "spawn_member",
                "spawn_teammate",
                "team.spawn_bridge_agent",
                "view_task",
                "team.spawn_external_cli",
            ],
        )
        assert rail._should_propose_new_team_skill() is True


class TestTeamSkillCreateRailPrompts:
    def test_team_nudge_only_reminds_self_check(self, tmp_path):
        section = build_team_skill_creation_nudge_section(str(tmp_path / "skills"))
        prompt = section.render("cn")

        assert "团队技能沉淀自检" in prompt
        assert str(tmp_path / "skills") in prompt
        assert "ask_user" not in prompt
        assert "swarmskill-creator" not in prompt
        assert "“是”“创建”“需要”" not in prompt

    @pytest.mark.asyncio
    async def test_before_model_call_injects_structured_team_guidance(self, tmp_path):
        rail = _make_rail(tmp_path)
        agent = _make_agent_with_team_skill_creation_capability(tmp_path)
        ctx = _make_invoke_ctx(agent)

        await rail.before_model_call(ctx)

        section = agent.system_prompt_builder.get_section(SectionName.TEAM_SKILL_CREATION_GUIDANCE)
        assert section is not None
        prompt = section.render("cn")
        assert "## 团队技能沉淀自检" in prompt
        for heading in (
            "### 判断场景",
            "#### 应考虑创建",
            "#### 不应创建",
            "### 用户意图信号",
            "### 回复与确认规则",
            "#### 最终回复",
            "#### 用户确认",
            "#### 创建执行",
        ):
            assert heading in prompt
        assert "### 核心原则" not in prompt
        assert "Team/Swarm Skill creation 只沉淀未来同类团队任务可复用的协作方法" in prompt
        assert "不需要创建时保持静默并正常回复" in prompt
        assert "以后做 xxx 团队任务时按这次分工推进。" in prompt
        assert "下次 xxx 仍按这种角色 / 成员职责安排。" in prompt
        assert "以后 xxx 的交接、汇总和验收沿用这个流程。" in prompt
        assert "类似 xxx 的用户反馈以后也这样分派给成员处理。" in prompt
        assert "最多追加两句" in prompt
        assert "Team/Swarm Skill" in prompt
        assert "swarmskill-creator" in prompt
        assert "ask_user" in prompt
        assert "prepare_skill_evolution" in prompt
        assert "evolve_review_task" in prompt
        assert "evolve_skill_experiences" in prompt

    @pytest.mark.asyncio
    async def test_before_model_call_keeps_guidance_when_auto_trigger_false(self, tmp_path):
        rail = _make_rail(tmp_path, auto_trigger=False)
        agent = _make_agent_with_team_skill_creation_capability(tmp_path)
        ctx = _make_invoke_ctx(agent)

        await rail.before_model_call(ctx)

        assert agent.system_prompt_builder.has_section(SectionName.TEAM_SKILL_CREATION_GUIDANCE)

    def test_team_creation_guidance_overrides_evolution_confirmation_when_combined(self):
        builder = SystemPromptBuilder(language="cn")
        builder.add_section(build_team_evolution_protocol_section("cn"))
        builder.add_section(build_team_skill_creation_guidance_section("cn"))

        prompt = builder.build()

        assert prompt.index("## 团队 Skill 演进自检") < prompt.index("## 团队技能沉淀自检")
        assert "用户确认创建后，使用 `swarmskill-creator`" in prompt
        assert "用户确认创建新团队技能不是确认 Swarm Skill 演进" in prompt
        assert "不要调用 `prepare_skill_evolution`、`evolve_review_task`" in prompt
        assert "或 `evolve_skill_experiences`" in prompt

    def test_english_team_creation_guidance_overrides_evolution_confirmation_when_combined(self):
        builder = SystemPromptBuilder(language="en")
        builder.add_section(build_team_evolution_protocol_section("en"))
        builder.add_section(build_team_skill_creation_guidance_section("en"))

        prompt = builder.build()

        assert prompt.index("## Team Skill Evolution Self-Check") < prompt.index("## Team Skill Capture Self-Check")
        assert "use `swarmskill-creator` or a compatible team skill creation capability" in prompt
        assert "User confirmation to create a new team skill is not consent for" in prompt
        assert "Swarm Skill evolution" in prompt
        assert "do not call `prepare_skill_evolution`, `evolve_review_task`, or" in prompt
        assert "`evolve_skill_experiences`" in prompt


class TestTeamSkillCreateRailFollowUp:
    @pytest.mark.parametrize("tool_name", ["spawn_member", "team.spawn_teammate"])
    @pytest.mark.asyncio
    async def test_schedules_follow_up_when_threshold_met_after_completion(self, tmp_path, tool_name):
        rail = _make_rail(tmp_path)
        _set_builder_tool_calls(rail, [tool_name] * 3)
        agent, controller = _agent_with_controller(tmp_path)
        ctx = _make_invoke_ctx(agent)

        assert await rail.notify_team_completed() is True
        await rail._on_after_task_iteration(ctx)

        controller.enqueue_follow_up.assert_called_once()
        _assert_team_follow_up_contract(controller.enqueue_follow_up.call_args.args[0])

    @pytest.mark.asyncio
    async def test_no_follow_up_when_auto_trigger_false(self, tmp_path):
        rail = _make_rail(tmp_path, auto_trigger=False)
        _set_builder_tool_calls(rail, ["spawn_member"] * 3)
        agent, controller = _agent_with_controller(tmp_path)
        ctx = _make_invoke_ctx(agent)

        assert await rail.notify_team_completed() is False
        await rail._on_after_task_iteration(ctx)

        controller.enqueue_follow_up.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_follow_up_until_team_completed(self, tmp_path):
        rail = _make_rail(tmp_path)
        _set_builder_tool_calls(rail, ["spawn_member"] * 3)
        agent, controller = _agent_with_controller(tmp_path)
        ctx = _make_invoke_ctx(agent)

        await rail._on_after_task_iteration(ctx)
        await rail._on_after_invoke(ctx)

        controller.enqueue_follow_up.assert_not_called()

    @pytest.mark.asyncio
    async def test_completion_mark_does_not_apply_to_new_session(self, tmp_path):
        rail = _make_rail(tmp_path)
        _set_builder_tool_calls(rail, ["spawn_member"] * 3)
        agent, controller = _agent_with_controller(tmp_path)

        assert await rail.notify_team_completed() is True

        rail._builder = TrajectoryBuilder(session_id="other-session", source="test")
        ctx = _make_invoke_ctx(agent, conversation_id="other-session")
        await rail._on_after_invoke(ctx)

        controller.enqueue_follow_up.assert_not_called()

    @pytest.mark.asyncio
    async def test_system_nudge_only_once_per_completed_session(self, tmp_path):
        rail = _make_rail(tmp_path)
        _set_builder_tool_calls(rail, ["spawn_member"] * 3)
        agent, controller = _agent_with_controller(tmp_path)
        ctx = _make_invoke_ctx(agent)

        assert await rail.notify_team_completed() is True
        await rail._on_after_invoke(ctx)
        assert await rail.notify_team_completed() is True
        await rail._on_after_invoke(ctx)

        controller.enqueue_follow_up.assert_called_once()
        assert rail._proposed_spawn_counts["test"] == 3

    @pytest.mark.asyncio
    async def test_system_nudge_can_repeat_in_same_session_after_new_team_run(self, tmp_path):
        rail = _make_rail(tmp_path)
        _set_builder_tool_calls(rail, ["spawn_member"] * 2)
        agent, controller = _agent_with_controller(tmp_path)
        ctx = _make_invoke_ctx(agent)

        assert await rail.notify_team_completed() is True
        await rail._on_after_invoke(ctx)

        _append_builder_tool_calls(rail._builder, ["spawn_member"] * 2)
        assert await rail.notify_team_completed() is True
        await rail._on_after_invoke(ctx)

        assert controller.enqueue_follow_up.call_count == 2
        assert rail._proposed_spawn_counts["test"] == 4

    @pytest.mark.asyncio
    async def test_no_controller_does_not_consume_completed_team_window(self, tmp_path):
        rail = _make_rail(tmp_path)
        _set_builder_tool_calls(rail, ["spawn_member"] * 3)
        agent = _make_agent_with_team_skill_creation_capability(tmp_path)
        agent._loop_controller = None
        ctx = _make_invoke_ctx(agent)

        assert await rail.notify_team_completed() is True
        await rail._on_after_invoke(ctx)

        assert rail._proposed_spawn_counts == {}
        assert rail._completed_session_id == "test"

        controller = MagicMock()
        controller.enqueue_follow_up = MagicMock()
        agent._loop_controller = controller
        await rail._on_after_invoke(ctx)

        controller.enqueue_follow_up.assert_called_once()
        _assert_team_follow_up_contract(controller.enqueue_follow_up.call_args.args[0])
        assert rail._proposed_spawn_counts["test"] == 3

    @pytest.mark.parametrize("skill_kind", ["team-skill", "swarm-skill"])
    @pytest.mark.asyncio
    async def test_no_follow_up_when_existing_team_skill_was_used(self, tmp_path, skill_kind):
        skills_dir = tmp_path / "skills"
        team_skill_dir = skills_dir / "research-team"
        team_skill_dir.mkdir(parents=True)
        (team_skill_dir / "SKILL.md").write_text(
            "---\n"
            "name: research-team\n"
            f"kind: {skill_kind}\n"
            "roles:\n"
            "  - name: planner\n"
            "    kind: ai_agent\n"
            "---\n"
            "# Research Team\n",
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
        agent, controller = _agent_with_controller(tmp_path)
        ctx = _make_invoke_ctx(agent)

        assert await rail.notify_team_completed() is True
        await rail._on_after_invoke(ctx)

        controller.enqueue_follow_up.assert_not_called()
