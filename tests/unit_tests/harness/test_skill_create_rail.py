# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
# pylint: disable=protected-access

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from openjiuwen.agent_evolving.prompts.sections import (
    build_evolution_protocol_section,
    build_skill_creation_guidance_section,
)
from openjiuwen.agent_evolving.signal.skill_creation import (
    SKILL_CREATION_SIGNAL_PROMPT_ELIGIBLE,
    SKILL_CREATION_SIGNAL_SKILL_TOOL_COVER,
    SkillCreationSignalDetector,
)
from openjiuwen.agent_evolving.trajectory import LLMCallDetail, ToolCallDetail, TrajectoryBuilder, TrajectoryStep
from openjiuwen.core.single_agent.skills.skill_manager import Skill
from openjiuwen.harness.prompts.builder import SystemPromptBuilder
from openjiuwen.harness.prompts.sections import SectionName
from openjiuwen.harness.rails.evolution.evolution_rail import EvolutionTriggerPoint
from openjiuwen.harness.rails.skills.skill_create_rail import SkillCreateRail
from openjiuwen.harness.rails.skills.skill_use_rail import SkillUseRail


def _make_rail(tmp_path, *, auto_trigger=True) -> SkillCreateRail:
    return SkillCreateRail(
        skills_dir=str(tmp_path / "skills"),
        auto_trigger=auto_trigger,
    )


def _make_builder(session_id: str = "test") -> TrajectoryBuilder:
    return TrajectoryBuilder(session_id=session_id, source="test")


def _make_signal_detector() -> SkillCreationSignalDetector:
    return SkillCreationSignalDetector()


def _set_builder_tool_calling_iterations(
    rail,
    count: int,
    *,
    tools_per_iteration: int = 1,
    tool_name: str = "bash",
    session_id: str = "test",
) -> None:
    builder = _make_builder(session_id=session_id)
    _append_builder_tool_calling_iterations(
        builder,
        count,
        tools_per_iteration=tools_per_iteration,
        tool_name=tool_name,
    )
    rail._builder = builder


def _append_builder_tool_calling_iterations(
    builder,
    count: int,
    *,
    tools_per_iteration: int = 1,
    tool_name: str = "bash",
    tool_call_id_prefix: str = "tc",
) -> None:
    for idx in range(count):
        tool_calls = [
            {"id": f"{tool_call_id_prefix}_{idx}_{tool_idx}", "name": tool_name, "arguments": "{}"}
            for tool_idx in range(tools_per_iteration)
        ]
        builder.record_step(
            TrajectoryStep(
                kind="llm",
                detail=LLMCallDetail(
                    model="mock",
                    messages=[],
                    response={"tool_calls": tool_calls},
                ),
                meta={"operator_id": "llm_main"},
            )
        )
        for tool_idx in range(tools_per_iteration):
            _append_builder_tool_call(
                builder,
                tool_name,
                tool_call_id=f"{tool_call_id_prefix}_{idx}_{tool_idx}",
            )


def _append_builder_tool_call(
    builder,
    tool_name: str,
    *,
    call_result: object = "ok",
    tool_call_id: str | None = None,
) -> None:
    builder.record_step(
        TrajectoryStep(
            kind="tool",
            detail=ToolCallDetail(
                tool_name=tool_name,
                call_args="{}",
                call_result=call_result,
                tool_call_id=tool_call_id,
            ),
            meta={"operator_id": tool_name},
        )
    )


def _make_agent_with_skill_creation_capability(tmp_path):
    skill_dir = tmp_path / "skills" / "skill-creator"
    skill_dir.mkdir(parents=True, exist_ok=True)
    skill_rail = SkillUseRail(skills_dir=str(tmp_path / "skills"))
    skill_rail.skills = [
        Skill(
            name="skill-creator",
            description="Create skills",
            directory=Path(skill_dir),
        )
    ]

    agent = MagicMock()
    agent.system_prompt_builder = SystemPromptBuilder(language="cn")
    agent._registered_rails = [skill_rail]
    agent._pending_rails = []
    agent.ability_manager.list_tool_info = AsyncMock(return_value=[SimpleNamespace(name="skill_tool")])
    return agent


def _make_task_ctx(*, extra=None, is_follow_up=False, has_controller=True):
    ctx = MagicMock()
    ctx.extra = extra or {}
    ctx.inputs = SimpleNamespace(is_follow_up=is_follow_up)
    ctx.agent = MagicMock()
    if has_controller:
        ctx.agent._loop_controller = MagicMock()
        ctx.agent._loop_controller.enqueue_follow_up = MagicMock()
    else:
        ctx.agent._loop_controller = None
    return ctx


class TestSkillCreateRailConstructor:
    def test_default_values(self, tmp_path):
        rail = _make_rail(tmp_path)
        assert rail._auto_trigger is True
        assert rail._evolution_trigger == EvolutionTriggerPoint.NONE

    def test_auto_trigger_false(self, tmp_path):
        rail = _make_rail(tmp_path, auto_trigger=False)
        assert rail._auto_trigger is False


class TestSkillCreateRailPrompts:
    @pytest.mark.asyncio
    async def test_before_model_call_injects_structured_guidance(self, tmp_path):
        rail = _make_rail(tmp_path)
        agent = _make_agent_with_skill_creation_capability(tmp_path)
        ctx = MagicMock()
        ctx.agent = agent

        await rail.before_model_call(ctx)

        section = agent.system_prompt_builder.get_section(SectionName.SKILL_CREATION_GUIDANCE)
        assert section is not None
        prompt = section.render("cn")
        assert "## 技能沉淀自检" in prompt
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
        assert "Skill creation 只沉淀未来同类任务可复用的类别级经验" in prompt
        assert "不需要创建时保持静默并正常回复" in prompt
        assert "以后遇到 xxx 按这个流程处理。" in prompt
        assert "下次做 xxx 时也这样检查 / 处理。" in prompt
        assert "以后输出 xxx 时保持这种格式 / 判断标准。" in prompt
        assert "刚才 xxx 出错后用 yyy 修好了" in prompt
        assert "最多追加两句" in prompt
        assert "skill-creator" in prompt
        assert "兼容的技能创建能力" in prompt
        assert "ask_user" in prompt
        assert "prepare_skill_evolution" in prompt
        assert "evolve_review_task" in prompt
        assert "evolve_skill_experiences" in prompt

    def test_creation_guidance_overrides_evolution_confirmation_when_combined(self):
        builder = SystemPromptBuilder(language="cn")
        builder.add_section(build_evolution_protocol_section("cn"))
        builder.add_section(build_skill_creation_guidance_section("cn"))

        prompt = builder.build()

        assert prompt.index("## 演进协议") < prompt.index("## 技能沉淀自检")
        assert "用户确认创建后，使用 `skill-creator`" in prompt
        assert "用户确认创建新 Skill 不是确认 Skill 演进" in prompt
        assert "不要调用 `prepare_skill_evolution`、`evolve_review_task` 或 `evolve_skill_experiences`" in prompt

    def test_english_creation_guidance_overrides_evolution_confirmation_when_combined(self):
        builder = SystemPromptBuilder(language="en")
        builder.add_section(build_evolution_protocol_section("en"))
        builder.add_section(build_skill_creation_guidance_section("en"))

        prompt = builder.build()

        assert prompt.index("## Evolution Protocol") < prompt.index("## Skill Capture Self-Check")
        assert "use `skill-creator` or a compatible skill creation capability" in prompt
        assert "User confirmation to create a new Skill is not consent for Skill evolution" in prompt
        assert "do not call" in prompt
        assert "`prepare_skill_evolution`, `evolve_review_task`, or `evolve_skill_experiences`" in prompt

    @pytest.mark.asyncio
    async def test_before_model_call_injects_stable_guidance_when_auto_trigger_false(self, tmp_path):
        rail = _make_rail(tmp_path, auto_trigger=False)
        agent = _make_agent_with_skill_creation_capability(tmp_path)
        ctx = MagicMock()
        ctx.agent = agent

        await rail.before_model_call(ctx)

        assert agent.system_prompt_builder.has_section(SectionName.SKILL_CREATION_GUIDANCE)

    @pytest.mark.asyncio
    async def test_follow_up_prompt_contract(self, tmp_path):
        rail = _make_rail(tmp_path)
        _set_builder_tool_calling_iterations(rail, 6)
        ctx = _make_task_ctx()

        await rail._on_after_task_iteration(ctx)

        ctx.agent._loop_controller.enqueue_follow_up.assert_called_once()
        prompt = ctx.agent._loop_controller.enqueue_follow_up.call_args.args[0]
        assert prompt.startswith("<auto_skill_creation_followup>\n")
        assert prompt.endswith("\n</auto_skill_creation_followup>")
        assert "不是用户的新需求" in prompt
        assert "不需要重新判断是否达到自检触发门槛" in prompt
        assert "最多追加两句" in prompt
        assert "第二句询问用户是否创建 Skill" in prompt
        assert "不要提及自检、沉淀、无需创建、已检查、内部判断或本提醒" in prompt
        assert "自然承接刚完成的任务" in prompt
        assert "不要重新总结任务结果" in prompt
        assert "ask_user" not in prompt
        assert "skill-creator" not in prompt
        assert "自动创建" not in prompt
        assert "interrupt" not in prompt
        assert "审批" not in prompt


class TestSkillCreateRailTriggering:
    @pytest.mark.parametrize(
        ("iterations", "tools_per_iteration"),
        [
            (6, 1),
            (1, 10),
        ],
    )
    @pytest.mark.asyncio
    async def test_first_prompt_threshold_is_effective_iterations_or_calls(
        self,
        tmp_path,
        iterations,
        tools_per_iteration,
    ):
        rail = _make_rail(tmp_path)
        _set_builder_tool_calling_iterations(
            rail,
            iterations,
            tools_per_iteration=tools_per_iteration,
        )
        ctx = _make_task_ctx()

        await rail._on_after_task_iteration(ctx)

        ctx.agent._loop_controller.enqueue_follow_up.assert_called_once()
        assert rail._last_prompted_tool_totals["test"] == (iterations, iterations * tools_per_iteration)

    @pytest.mark.parametrize(
        ("iterations", "tools_per_iteration"),
        [
            (5, 1),
            (1, 9),
        ],
    )
    @pytest.mark.asyncio
    async def test_does_not_prompt_below_first_threshold(self, tmp_path, iterations, tools_per_iteration):
        rail = _make_rail(tmp_path)
        _set_builder_tool_calling_iterations(
            rail,
            iterations,
            tools_per_iteration=tools_per_iteration,
        )
        ctx = _make_task_ctx()

        await rail._on_after_task_iteration(ctx)

        ctx.agent._loop_controller.enqueue_follow_up.assert_not_called()

    @pytest.mark.asyncio
    async def test_normal_reply_creation_question_records_prompt_without_follow_up(self, tmp_path):
        rail = _make_rail(tmp_path)
        _set_builder_tool_calling_iterations(rail, 6)
        ctx = _make_task_ctx()
        ctx.inputs.result = {
            "output": "这个流程可以沉淀成可复用技能。要不要我创建一个 Skill？",
            "result_type": "answer",
        }

        await rail._on_after_task_iteration(ctx)

        ctx.agent._loop_controller.enqueue_follow_up.assert_not_called()
        assert rail._follow_up_sent is True
        assert rail._last_prompted_tool_totals["test"] == (6, 6)
        assert rail._last_followed_tool_call_counts["test"] == 6

    @pytest.mark.asyncio
    async def test_english_normal_reply_creation_question_records_prompt_without_follow_up(self, tmp_path):
        rail = _make_rail(tmp_path)
        _set_builder_tool_calling_iterations(rail, 6)
        ctx = _make_task_ctx()
        ctx.inputs.result = {
            "message": {"content": "This workflow is reusable. Should I create a Skill for it?"},
            "result_type": "answer",
        }

        await rail._on_after_task_iteration(ctx)

        ctx.agent._loop_controller.enqueue_follow_up.assert_not_called()
        assert rail._last_prompted_tool_totals["test"] == (6, 6)
        assert rail._last_followed_tool_call_counts["test"] == 6

    @pytest.mark.asyncio
    async def test_normal_reply_creation_terms_below_threshold_do_not_consume_window(self, tmp_path):
        rail = _make_rail(tmp_path)
        _set_builder_tool_calling_iterations(rail, 5)
        ctx = _make_task_ctx()
        ctx.inputs.result = {
            "output": "这个流程可以沉淀成可复用技能。要不要我创建一个 Skill？",
            "result_type": "answer",
        }

        await rail._on_after_task_iteration(ctx)

        ctx.agent._loop_controller.enqueue_follow_up.assert_not_called()
        assert rail._last_prompted_tool_totals == {}
        assert rail._last_followed_tool_call_counts == {}

    @pytest.mark.asyncio
    async def test_normal_reply_creation_question_prevents_small_reprompt_window(self, tmp_path):
        rail = _make_rail(tmp_path)
        _set_builder_tool_calling_iterations(rail, 6)
        ctx = _make_task_ctx()
        ctx.inputs.result = {
            "output": "这个流程可以沉淀成可复用技能。要不要我创建一个 Skill？",
            "result_type": "answer",
        }

        await rail._on_after_task_iteration(ctx)
        await rail._on_before_invoke(ctx)
        ctx.inputs.result = {"output": "继续完成了一点小修改。", "result_type": "answer"}
        _append_builder_tool_calling_iterations(
            rail._builder,
            1,
            tools_per_iteration=3,
            tool_call_id_prefix="small_after_normal_prompt",
        )
        await rail._on_after_task_iteration(ctx)

        ctx.agent._loop_controller.enqueue_follow_up.assert_not_called()
        assert rail._last_prompted_tool_totals["test"] == (6, 6)
        assert rail._last_followed_tool_call_counts["test"] == 6

    @pytest.mark.asyncio
    async def test_normal_reply_creation_question_consumes_window_without_controller(self, tmp_path):
        rail = _make_rail(tmp_path)
        _set_builder_tool_calling_iterations(rail, 6)
        ctx = _make_task_ctx(has_controller=False)
        ctx.inputs.result = {
            "output": "这个流程可以沉淀成可复用技能。要不要我创建一个 Skill？",
            "result_type": "answer",
        }

        await rail._on_after_task_iteration(ctx)

        assert rail._follow_up_sent is True
        assert rail._last_prompted_tool_totals["test"] == (6, 6)
        assert rail._last_followed_tool_call_counts["test"] == 6

    @pytest.mark.parametrize(
        ("extra_iterations", "tools_per_iteration"),
        [
            (2, 1),
            (1, 4),
        ],
    )
    @pytest.mark.asyncio
    async def test_reprompt_threshold_uses_new_effective_window(
        self,
        tmp_path,
        extra_iterations,
        tools_per_iteration,
    ):
        rail = _make_rail(tmp_path)
        _set_builder_tool_calling_iterations(rail, 6, session_id="session-a")
        ctx = _make_task_ctx()

        await rail._on_after_task_iteration(ctx)
        await rail._on_before_invoke(ctx)
        _append_builder_tool_calling_iterations(
            rail._builder,
            extra_iterations,
            tools_per_iteration=tools_per_iteration,
            tool_call_id_prefix="extra",
        )
        await rail._on_after_task_iteration(ctx)

        assert ctx.agent._loop_controller.enqueue_follow_up.call_count == 2

    @pytest.mark.asyncio
    async def test_does_not_reprompt_when_only_total_still_exceeds_first_threshold(self, tmp_path):
        rail = _make_rail(tmp_path)
        _set_builder_tool_calling_iterations(rail, 6, session_id="session-a")
        ctx = _make_task_ctx()

        await rail._on_after_task_iteration(ctx)
        await rail._on_before_invoke(ctx)
        _append_builder_tool_calling_iterations(
            rail._builder,
            1,
            tools_per_iteration=3,
            tool_call_id_prefix="small",
        )
        await rail._on_after_task_iteration(ctx)

        ctx.agent._loop_controller.enqueue_follow_up.assert_called_once()
        assert rail._last_prompted_tool_totals["session-a"] == (6, 6)

    def test_metrics_filter_blacklisted_tools_and_count_unknown_namespaced_tools(self):
        builder = _make_builder()
        _append_builder_tool_calling_iterations(builder, 3, tool_name="ask_user")
        _append_builder_tool_calling_iterations(builder, 2, tool_name="team.send_message", tool_call_id_prefix="send")
        _append_builder_tool_calling_iterations(builder, 2, tool_name="team.custom_tool", tool_call_id_prefix="custom")

        metrics = _make_signal_detector().collect_metrics(builder)

        assert metrics.total_effective_tool_calling_iterations == 2
        assert metrics.total_effective_tool_calls == 2
        assert metrics.window_effective_tool_calling_iterations == 2
        assert metrics.window_effective_tool_calls == 2
        assert metrics.total_raw_tool_calls == 7

    def test_signal_detector_returns_prompt_and_skill_tool_cover_signals(self):
        detector = _make_signal_detector()
        builder = _make_builder()
        _append_builder_tool_calling_iterations(builder, 6)
        prompt_signals = detector.detect(builder)

        assert len(prompt_signals) == 1
        prompt_signal = prompt_signals[0]
        assert prompt_signal.signal_type == SKILL_CREATION_SIGNAL_PROMPT_ELIGIBLE
        assert prompt_signal.reason == "first_prompt_threshold"

        _append_builder_tool_call(builder, "team.skill_tool")
        cover_signals = detector.detect(builder)

        assert len(cover_signals) == 1
        cover_signal = cover_signals[0]
        assert cover_signal.signal_type == SKILL_CREATION_SIGNAL_SKILL_TOOL_COVER
        assert cover_signal.reason == "skill_tool_used"

    def test_signal_detector_can_reuse_collected_metrics(self):
        detector = _make_signal_detector()
        builder = _make_builder()
        _append_builder_tool_calling_iterations(builder, 6)
        metrics = detector.collect_metrics(builder)

        signals = detector.detect(builder, metrics=metrics)

        assert len(signals) == 1
        assert signals[0].metrics is metrics

    @pytest.mark.parametrize("tool_name", ["skill_tool", "team.skill_tool"])
    @pytest.mark.asyncio
    async def test_skill_tool_cover_signal_refreshes_watermark_and_blocks_current_invoke(
        self,
        tmp_path,
        tool_name,
    ):
        rail = _make_rail(tmp_path)
        _set_builder_tool_calling_iterations(rail, 6)
        _append_builder_tool_call(rail._builder, tool_name)
        ctx = _make_task_ctx()

        await rail._on_after_task_iteration(ctx)
        _append_builder_tool_calling_iterations(
            rail._builder,
            6,
            tool_call_id_prefix="after_skill_tool",
        )
        await rail._on_after_task_iteration(ctx)

        ctx.agent._loop_controller.enqueue_follow_up.assert_not_called()
        assert rail._last_followed_tool_call_counts["test"] == 13

    @pytest.mark.asyncio
    async def test_next_invoke_can_trigger_after_skill_tool_watermark(self, tmp_path):
        rail = _make_rail(tmp_path)
        _set_builder_tool_calling_iterations(rail, 1, tool_name="skill_tool")
        ctx = _make_task_ctx()

        await rail._on_after_task_iteration(ctx)
        await rail._on_before_invoke(ctx)
        _append_builder_tool_calling_iterations(
            rail._builder,
            6,
            tool_call_id_prefix="next",
        )
        await rail._on_after_task_iteration(ctx)

        ctx.agent._loop_controller.enqueue_follow_up.assert_called_once()

    @pytest.mark.parametrize(
        "ctx_kwargs",
        [
            {"extra": {"run_kind": "background"}},
            {"extra": {"run_kind": "heartbeat"}},
            {"extra": {"run_kind": "cron"}},
            {"is_follow_up": True},
        ],
    )
    @pytest.mark.asyncio
    async def test_suppressed_run_kinds_refresh_watermark(self, tmp_path, ctx_kwargs):
        rail = _make_rail(tmp_path)
        _set_builder_tool_calling_iterations(rail, 6)
        ctx = _make_task_ctx(**ctx_kwargs)

        await rail._on_after_task_iteration(ctx)

        ctx.agent._loop_controller.enqueue_follow_up.assert_not_called()
        assert rail._last_followed_tool_call_counts["test"] == 6

    @pytest.mark.asyncio
    async def test_controller_unavailable_does_not_consume_window(self, tmp_path):
        rail = _make_rail(tmp_path)
        _set_builder_tool_calling_iterations(rail, 6)
        ctx = _make_task_ctx(has_controller=False)

        await rail._on_after_task_iteration(ctx)
        ctx.agent._loop_controller = MagicMock()
        ctx.agent._loop_controller.enqueue_follow_up = MagicMock()
        await rail._on_after_task_iteration(ctx)

        ctx.agent._loop_controller.enqueue_follow_up.assert_called_once()
        assert rail._last_followed_tool_call_counts == {}

    @pytest.mark.asyncio
    async def test_auto_trigger_false_suppresses_follow_up(self, tmp_path):
        rail = _make_rail(tmp_path, auto_trigger=False)
        _set_builder_tool_calling_iterations(rail, 6)
        ctx = _make_task_ctx()

        await rail._on_after_task_iteration(ctx)

        ctx.agent._loop_controller.enqueue_follow_up.assert_not_called()

    @pytest.mark.asyncio
    async def test_follow_up_once_per_invoke(self, tmp_path):
        rail = _make_rail(tmp_path)
        _set_builder_tool_calling_iterations(rail, 6)
        ctx = _make_task_ctx()

        await rail._on_after_task_iteration(ctx)
        await rail._on_after_task_iteration(ctx)

        ctx.agent._loop_controller.enqueue_follow_up.assert_called_once()
