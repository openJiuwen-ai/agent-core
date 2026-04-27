# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""SkillCreateRail: independent rail for 1D skill creation.

Triggers via threshold detection in after_task_iteration:
- Detects tool call patterns meeting thresholds after each task-loop round
- Enqueues follow_up via TaskLoopController so the next round picks it up

After user confirms creation via ask_user, the model invokes the
skill-creator skill to execute the creation.
"""

from __future__ import annotations

from typing import Optional

from openjiuwen.agent_evolving.trajectory import TrajectoryStore
from openjiuwen.core.common.logging import logger
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext
from openjiuwen.harness.rails.evolution_rail import EvolutionRail, EvolutionTriggerPoint

_FOLLOW_UP_PROMPT_CN = (
    "**重要：你必须先调用 ask_user 工具向用户确认，不可跳过此步骤。**\n"
    "系统检测到对话中存在可复用模式，可能值得创建新技能。请按以下步骤执行：\n"
    "1. 调用 ask_user 工具向用户确认：\n"
    "   - 问题：\"我检测到您可能值得创建一个新技能。是否创建？\"\n"
    "   - 选项：[\"创建\"，\"跳过\"，\"自定义指令：（请描述需求）\"]\n"
    "2. 如果用户选择\"创建\"或提供了自定义指令，请调用 **skill-creator** 技能，"
    "根据用户的要求和当前对话上下文执行技能创建。\n"
    "   新技能应保存到技能目录：{skills_dir}"
)
_FOLLOW_UP_PROMPT_EN = (
    "**Important: You MUST call the ask_user tool to confirm with the user first. Do not skip this step.**\n"
    "The system detected a reusable pattern that may be worth creating as a new skill. "
    "Please follow these steps:\n"
    "1. Use ask_user tool to confirm with the user:\n"
    "   - Question: \"I detected a pattern that may be worth creating as a new skill. Create it?\"\n"
    "   - Options: [\"Create\", \"Skip\", \"Custom instruction: (describe your needs)\"]\n"
    "2. If user chooses \"Create\" or provides a custom instruction, invoke the **skill-creator** skill "
    "to execute the skill creation.\n"
    "   Save the new skill to: {skills_dir}"
)


class SkillCreateRail(EvolutionRail):
    """Independent rail for 1D skill creation.

    Trigger mode: threshold detection in after_task_iteration →
    enqueue follow_up via TaskLoopController.
    """

    priority = 85

    def __init__(
        self,
        skills_dir: str,
        *,
        language: str = "cn",
        auto_trigger: bool = True,
        tool_call_threshold: int = 10,
        tool_diversity_threshold: int = 5,
        trajectory_store: Optional[TrajectoryStore] = None,
    ) -> None:
        super().__init__(
            trajectory_store=trajectory_store,
            evolution_trigger=EvolutionTriggerPoint.NONE,
        )
        self._skills_dir = skills_dir
        self._auto_trigger = auto_trigger
        self._tool_call_threshold = tool_call_threshold
        self._tool_diversity_threshold = tool_diversity_threshold
        self._language = language
        self._proposal_sent = False

    async def _on_before_invoke(self, ctx: AgentCallbackContext) -> None:
        """Reset proposal flag at the start of each invoke cycle."""
        self._proposal_sent = False

    async def _on_after_task_iteration(self, ctx: AgentCallbackContext) -> None:
        """Threshold detection → enqueue follow_up for the next round."""
        if not self._auto_trigger:
            return

        # Only propose once per invoke cycle; reset in before_invoke
        if self._proposal_sent:
            return

        if not self._should_propose_new_skill():
            return

        agent = ctx.agent
        controller = getattr(agent, "_loop_controller", None)
        if controller is None:
            logger.warning(
                "[SkillCreateRail] skill creation proposal dropped: "
                "no TaskLoopController available. "
                "This rail only works in task-loop mode."
            )
            return

        if self._language == "cn":
            prompt = _FOLLOW_UP_PROMPT_CN.format(skills_dir=self._skills_dir)
        else:
            prompt = _FOLLOW_UP_PROMPT_EN.format(skills_dir=self._skills_dir)

        logger.info(
            "[SkillCreateRail] Reusable pattern detected, enqueuing follow_up. "
            "language=%s, skills_dir=%s, prompt_length=%d",
            self._language,
            self._skills_dir,
            len(prompt),
        )
        logger.debug("[SkillCreateRail] follow_up prompt: %s", prompt)
        controller.enqueue_follow_up(prompt)
        self._proposal_sent = True
        logger.info("[SkillCreateRail] follow_up enqueued successfully")

    # ---- Threshold detection ----

    def _should_propose_new_skill(self) -> bool:
        """Check if tool call patterns meet creation thresholds.

        Uses the trajectory builder collected by EvolutionRail,
        avoiding redundant message parsing.
        """
        if self._builder is None:
            logger.debug("[SkillCreateRail] trajectory builder is None, skipping")
            return False

        tool_calls: list[str] = []
        for step in self._builder.steps:
            if step.kind == "tool" and step.detail:
                tool_name = getattr(step.detail, "tool_name", "")
                if tool_name:
                    tool_calls.append(tool_name)

        total_calls = len(tool_calls)
        unique_tools = len(set(tool_calls))

        if total_calls < self._tool_call_threshold:
            logger.debug(
                "[SkillCreateRail] tool call count %d below threshold %d, skipping",
                total_calls,
                self._tool_call_threshold,
            )
            return False

        if unique_tools < self._tool_diversity_threshold:
            logger.debug(
                "[SkillCreateRail] unique tool count %d below threshold %d, skipping",
                unique_tools,
                self._tool_diversity_threshold,
            )
            return False

        logger.info(
            "[SkillCreateRail] skill creation threshold met: %d tool calls, %d unique tools "
            "(thresholds: %d calls, %d unique)",
            total_calls,
            unique_tools,
            self._tool_call_threshold,
            self._tool_diversity_threshold,
        )
        return True


__all__ = ["SkillCreateRail"]