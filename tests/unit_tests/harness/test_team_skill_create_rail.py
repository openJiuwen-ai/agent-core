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
from openjiuwen.agent_evolving.trajectory.model import Trajectory
from openjiuwen.agent_evolving.trajectory.processor import TrajectorySpanProcessor
from openjiuwen.agent_evolving.trajectory.schema import SESSION_ID, TRAJECTORY_ID
from openjiuwen.agent_evolving.trajectory.spans import attributes_from_map, iter_spans, merge_trajectories
from openjiuwen.extensions.observability import semconv
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
        trajectory_span_processor=TrajectorySpanProcessor(),
        auto_trigger=auto_trigger,
    )


def _make_trajectory(session_id: str = "test", spans: list[dict] | None = None) -> Trajectory:
    return Trajectory.from_otlp(
        {
            "resourceSpans": [
                {
                    "resource": {
                        "attributes": attributes_from_map(
                            {
                                SESSION_ID: session_id,
                                TRAJECTORY_ID: f"trajectory-{session_id}",
                            }
                        ),
                    },
                    "scopeSpans": [{"scope": {}, "spans": spans or []}],
                }
            ]
        }
    )


def _tool_span(span_index: int, tool_name: str, *, tool_input: object = "{}") -> dict:
    return {
        "traceId": "trace-test",
        "spanId": f"tool-{span_index}",
        "name": f"tool.{tool_name}",
        "attributes": attributes_from_map(
            {
                semconv.GEN_AI_TOOL_NAME: tool_name,
                semconv.GEN_AI_TOOL_INPUT: tool_input,
                semconv.GEN_AI_TOOL_OUTPUT: "ok",
            }
        ),
    }


def _make_trajectory_tool_calls(tool_names: list[str], *, session_id: str = "test") -> Trajectory:
    spans = [_tool_span(index, name) for index, name in enumerate(tool_names)]
    return _make_trajectory(session_id=session_id, spans=spans)


def _set_trajectory_tool_calls(rail, tool_names: list[str], *, session_id: str = "test") -> Trajectory:
    trajectory = _make_trajectory_tool_calls(tool_names, session_id=session_id)
    rail._trajectory = trajectory
    return trajectory


def _append_trajectory_tool_calls(trajectory: Trajectory, tool_names: list[str]) -> Trajectory:
    start_index = len(list(iter_spans(trajectory)))
    additions = [_tool_span(start_index + index, name) for index, name in enumerate(tool_names)]
    return merge_trajectories(trajectory, _make_trajectory(trajectory.session_id or "test", additions))


async def _after_task_iteration(rail: TeamSkillCreateRail, ctx) -> None:
    await rail._on_after_task_iteration(ctx, rail._trajectory)


async def _after_invoke(rail: TeamSkillCreateRail, ctx) -> None:
    await rail._on_after_invoke(ctx, rail._trajectory)


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
    assert "参考常驻“团队技能沉淀自检”规则" in prompt
    assert "不重新判断运行时触发门槛" in prompt
    assert "普通最终回复末尾追加一至两句" in prompt
    assert "同时包含可复用团队方法" in prompt
    assert "是否创建 Team/Swarm Skill" in prompt
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
            trajectory_span_processor=TrajectorySpanProcessor(),
            min_team_members_for_create=4,
        )
        assert rail._min_team_members == 4

    def test_trajectory_store_is_not_accepted(self, tmp_path):
        with pytest.raises(TypeError, match="trajectory_store"):
            TeamSkillCreateRail(
                skills_dir=str(tmp_path / "skills"),
                trajectory_span_processor=TrajectorySpanProcessor(),
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
        _set_trajectory_tool_calls(rail, [tool_name] * 3)
        assert rail._should_propose_new_team_skill() is True

    def test_should_not_propose_when_below_threshold(self, tmp_path):
        rail = _make_rail(tmp_path)
        _set_trajectory_tool_calls(rail, ["spawn_member"])
        assert rail._should_propose_new_team_skill() is False

    @pytest.mark.parametrize("tool_name", ["send_message", "team.send_message", "view_task"])
    def test_non_spawn_tools_do_not_count(self, tmp_path, tool_name):
        rail = _make_rail(tmp_path)
        _set_trajectory_tool_calls(rail, [tool_name] * 3)
        assert rail._should_propose_new_team_skill() is False

    @pytest.mark.parametrize("tool_name", ["not_spawn_member", "team.spawn_member_extra"])
    def test_spawn_tool_names_require_exact_match(self, tmp_path, tool_name):
        rail = _make_rail(tmp_path)
        _set_trajectory_tool_calls(rail, [tool_name] * 3)
        assert rail._should_propose_new_team_skill() is False

    def test_should_count_mixed_spawn_calls(self, tmp_path):
        rail = _make_rail(tmp_path)
        _set_trajectory_tool_calls(
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
        assert "skills_dir" not in prompt
        assert "ask_user" not in prompt
        assert "swarmskill-creator" not in prompt
        assert "“是”“创建”“需要”" not in prompt

    @pytest.mark.asyncio
    async def test_before_model_call_injects_structured_team_guidance(self, tmp_path):
        rail = _make_rail(tmp_path)
        agent = _make_agent_with_team_skill_creation_capability(tmp_path)
        ctx = _make_invoke_ctx(agent)

        assert agent._registered_rails[0].skills[0].name == "swarmskill-creator"
        await rail.before_model_call(ctx)

        section = agent.system_prompt_builder.get_section(SectionName.TEAM_SKILL_CREATION_GUIDANCE)
        assert section is not None
        prompt = section.render("cn")
        assert "## 团队技能沉淀自检" in prompt
        for heading in ("目标与边界", "决策顺序", "用户可见输出", "用户确认", "能力交接"):
            assert prompt.count(f"### {heading}") == 1
        assert "只沉淀未来同类团队任务可复用的新协作方法" in prompt
        assert "高优先级用户意图" in prompt
        assert "任务拆解、成员路由" in prompt
        assert "自检不得打断团队任务" in prompt
        assert "Team/Swarm Skill" in prompt
        assert "确认后交给团队技能创建能力" in prompt
        assert "只在普通最终回复末尾追加一至两句" in prompt
        assert "必须同时包含" in prompt
        assert "创建确认不等于 Swarm Skill 演进确认" in prompt
        assert "prepare_skill_evolution" not in prompt
        assert "evolve_review_task" not in prompt
        assert "evolve_skill_experiences" not in prompt

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
        assert "确认后交给团队技能创建能力" in prompt
        assert "只在普通最终回复末尾追加一至两句" in prompt
        assert "创建确认不等于 Swarm Skill 演进确认" in prompt
        assert "prepare_skill_evolution" not in prompt

    def test_english_team_creation_guidance_overrides_evolution_confirmation_when_combined(self):
        builder = SystemPromptBuilder(language="en")
        builder.add_section(build_team_evolution_protocol_section("en"))
        builder.add_section(build_team_skill_creation_guidance_section("en"))

        prompt = builder.build()

        assert prompt.index("## Team Skill Evolution Self-Check") < prompt.index("## Team Skill Capture Self-Check")
        assert "After confirmation, hand the team context to the team Skill creation capability" in prompt
        assert "append only one or two sentences" in prompt
        assert "Creation confirmation is not Swarm Skill evolution confirmation" in prompt
        assert "prepare_skill_evolution" not in prompt


class TestTeamSkillCreateRailFollowUp:
    @pytest.mark.asyncio
    async def test_external_repeated_feedback_routes_to_creation_confirmation(self, tmp_path):
        rail = _make_rail(tmp_path)
        _set_trajectory_tool_calls(rail, ["spawn_member"] * 2)
        agent, controller = _agent_with_controller(tmp_path)
        rail.init(agent)

        routed = await rail.propose_from_external_evidence(
            proposal_key="release-recovery-checklist",
            reusable_guidance="Create a reusable release recovery checklist.",
            evidence=(
                "task=a: recovery policy was missing",
                "task=b: recovery policy was missing",
            ),
            reason="the same missing workflow failed two task reviews",
        )

        assert routed is True
        controller.enqueue_follow_up.assert_not_called()
        events = await rail.drain_pending_approval_events(wait=False)
        assert len(events) == 1
        event = events[0]
        assert event.type == "chat.ask_user_question"
        assert event.payload["source"] == "skill_creation_approval"
        assert event.payload["approval_schema"] == "openjiuwen.skill_creation_approval.v1"
        request_id = event.payload["request_id"]
        assert request_id.startswith("skill_create_")
        assert rail.owns_external_proposal(request_id) is True
        question = event.payload["questions"][0]
        assert question["header"] == "新建 Skill 审批"
        assert "拟沉淀的 Skill 内容" in question["question"]
        assert "Create a reusable release recovery checklist." in question["question"]
        assert "the same missing workflow failed two task reviews" in question["question"]
        assert "任务证据" not in question["question"]
        assert "task=a" not in question["question"]
        assert "task=b" not in question["question"]
        assert [option["label"] for option in question["options"]] == ["接收", "拒绝"]

        creation_prompt = rail.resolve_external_proposal(request_id, accepted=True)
        assert creation_prompt is not None
        assert "用户已在新建 Skill 审批卡中点击接收" in creation_prompt
        assert "swarmskill-creator" in creation_prompt
        assert "task=a" in creation_prompt
        assert "task=b" in creation_prompt
        assert rail.owns_external_proposal(request_id) is False

        # The proposal is idempotent and consumes the generic self-check for
        # the same trajectory window.
        assert (
            await rail.propose_from_external_evidence(
                proposal_key="release-recovery-checklist",
                reusable_guidance="Create a reusable release recovery checklist.",
                evidence=("task=a", "task=b"),
            )
            is False
        )
        await _after_invoke(rail, _make_invoke_ctx(agent))
        controller.enqueue_follow_up.assert_not_called()

    @pytest.mark.asyncio
    async def test_external_feedback_requires_two_evidence_items(self, tmp_path):
        rail = _make_rail(tmp_path)
        agent, controller = _agent_with_controller(tmp_path)
        rail.init(agent)

        assert (
            await rail.propose_from_external_evidence(
                proposal_key="one-off",
                reusable_guidance="Create a one-off Skill.",
                evidence=("task=a",),
            )
            is False
        )
        controller.enqueue_follow_up.assert_not_called()

    @pytest.mark.parametrize("tool_name", ["spawn_member", "team.spawn_teammate"])
    @pytest.mark.asyncio
    async def test_schedules_follow_up_when_threshold_met_after_completion(self, tmp_path, tool_name):
        rail = _make_rail(tmp_path)
        _set_trajectory_tool_calls(rail, [tool_name] * 3)
        agent, controller = _agent_with_controller(tmp_path)
        ctx = _make_invoke_ctx(agent)

        assert await rail.notify_team_completed() is True
        await _after_task_iteration(rail, ctx)

        controller.enqueue_follow_up.assert_called_once()
        _assert_team_follow_up_contract(controller.enqueue_follow_up.call_args.args[0])

    @pytest.mark.asyncio
    async def test_no_follow_up_when_auto_trigger_false(self, tmp_path):
        rail = _make_rail(tmp_path, auto_trigger=False)
        _set_trajectory_tool_calls(rail, ["spawn_member"] * 3)
        agent, controller = _agent_with_controller(tmp_path)
        ctx = _make_invoke_ctx(agent)

        assert await rail.notify_team_completed() is False
        await _after_task_iteration(rail, ctx)

        controller.enqueue_follow_up.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_follow_up_until_team_completed(self, tmp_path):
        rail = _make_rail(tmp_path)
        _set_trajectory_tool_calls(rail, ["spawn_member"] * 3)
        agent, controller = _agent_with_controller(tmp_path)
        ctx = _make_invoke_ctx(agent)

        await _after_task_iteration(rail, ctx)
        await _after_invoke(rail, ctx)

        controller.enqueue_follow_up.assert_not_called()

    @pytest.mark.asyncio
    async def test_completion_mark_does_not_apply_to_new_session(self, tmp_path):
        rail = _make_rail(tmp_path)
        _set_trajectory_tool_calls(rail, ["spawn_member"] * 3)
        agent, controller = _agent_with_controller(tmp_path)

        assert await rail.notify_team_completed() is True

        rail._trajectory = _make_trajectory(session_id="other-session")
        ctx = _make_invoke_ctx(agent, conversation_id="other-session")
        await _after_invoke(rail, ctx)

        controller.enqueue_follow_up.assert_not_called()

    @pytest.mark.asyncio
    async def test_system_nudge_only_once_per_completed_session(self, tmp_path):
        rail = _make_rail(tmp_path)
        _set_trajectory_tool_calls(rail, ["spawn_member"] * 3)
        agent, controller = _agent_with_controller(tmp_path)
        ctx = _make_invoke_ctx(agent)

        assert await rail.notify_team_completed() is True
        await _after_invoke(rail, ctx)
        assert await rail.notify_team_completed() is True
        await _after_invoke(rail, ctx)

        controller.enqueue_follow_up.assert_called_once()
        assert rail._proposed_spawn_counts["test"] == 3

    @pytest.mark.asyncio
    async def test_system_nudge_can_repeat_in_same_session_after_new_team_run(self, tmp_path):
        rail = _make_rail(tmp_path)
        _set_trajectory_tool_calls(rail, ["spawn_member"] * 2)
        agent, controller = _agent_with_controller(tmp_path)
        ctx = _make_invoke_ctx(agent)

        assert await rail.notify_team_completed() is True
        await _after_invoke(rail, ctx)

        rail._trajectory = _append_trajectory_tool_calls(rail._trajectory, ["spawn_member"] * 2)
        assert await rail.notify_team_completed() is True
        await _after_invoke(rail, ctx)

        assert controller.enqueue_follow_up.call_count == 2
        assert rail._proposed_spawn_counts["test"] == 4

    @pytest.mark.asyncio
    async def test_no_controller_does_not_consume_completed_team_window(self, tmp_path):
        rail = _make_rail(tmp_path)
        _set_trajectory_tool_calls(rail, ["spawn_member"] * 3)
        agent = _make_agent_with_team_skill_creation_capability(tmp_path)
        agent._loop_controller = None
        ctx = _make_invoke_ctx(agent)

        assert await rail.notify_team_completed() is True
        await _after_invoke(rail, ctx)

        assert rail._proposed_spawn_counts == {}
        assert rail._completed_session_id == "test"

        controller = MagicMock()
        controller.enqueue_follow_up = MagicMock()
        agent._loop_controller = controller
        await _after_invoke(rail, ctx)

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
        rail = TeamSkillCreateRail(
            skills_dir=str(skills_dir),
            trajectory_span_processor=TrajectorySpanProcessor(),
        )
        _set_trajectory_tool_calls(rail, ["spawn_member"] * 2)
        rail._trajectory = merge_trajectories(
            rail._trajectory,
            _make_trajectory(
                "test",
                [
                    _tool_span(
                        2,
                        "skill_tool",
                        tool_input={"skill_name": "research-team", "relative_file_path": "SKILL.md"},
                    )
                ],
            ),
        )
        agent, controller = _agent_with_controller(tmp_path)
        ctx = _make_invoke_ctx(agent)

        assert await rail.notify_team_completed() is True
        await _after_invoke(rail, ctx)

        controller.enqueue_follow_up.assert_not_called()
