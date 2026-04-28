# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""TeamSkillCreateRail: independent rail for team skill creation.

Triggers via threshold detection in after_task_iteration:
- Detects spawn_member calls meeting threshold after each task-loop round
- Enqueues follow_up via TaskLoopController so the next round picks it up

After user confirms creation via ask_user, the model invokes the
team-skill-creator skill to execute the creation.
"""

from __future__ import annotations

from typing import Optional

from openjiuwen.agent_evolving.trajectory import TrajectoryStore
from openjiuwen.core.common.logging import logger
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext
from openjiuwen.harness.rails.evolution.evolution_rail import EvolutionRail, EvolutionTriggerPoint

_FOLLOW_UP_PROMPT_CN = (
    "**重要：你必须先调用 ask_user 工具向用户确认，不可跳过此步骤。**\n"
    "系统检测到对话中 spawn 了多个团队成员，可能值得创建团队技能。请按以下步骤执行：\n"
    "1. 调用 ask_user 工具向用户确认：\n"
    "   - 问题：\"我检测到多 Agent 协作模式可能值得创建为团队技能。是否创建？\"\n"
    "   - 选项：[\"创建\"，\"跳过\"，\"自定义指令：（请描述需求）\"]\n"
    "2. 如果用户选择\"创建\"或提供了自定义指令，请调用 **team-skill-creator** 技能，"
    "根据用户的要求和当前对话上下文执行团队技能创建。\n"
    "   新技能应保存到技能目录：{skills_dir}"
)
_FOLLOW_UP_PROMPT_EN = (
    "**Important: You MUST call the ask_user tool to confirm with the user first. Do not skip this step.**\n"
    "The system detected multiple team member spawns that may be worth creating as a Team Skill. "
    "Please follow these steps:\n"
    "1. Use ask_user tool to confirm with the user:\n"
    "   - Question: \"I detected a multi-agent collaboration pattern that may be worth creating "
    "as a Team Skill. Create it?\"\n"
    "   - Options: [\"Create\", \"Skip\", \"Custom instruction: (describe your needs)\"]\n"
    "2. If user chooses \"Create\" or provides a custom instruction, invoke the **team-skill-creator** skill "
    "to execute the team skill creation.\n"
    "   Save the new skill to: {skills_dir}"
)


class TeamSkillCreateRail(EvolutionRail):
    """Independent rail for team skill creation.

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
        min_team_members_for_create: int = 2,
        trajectory_store: Optional[TrajectoryStore] = None,
    ) -> None:
        super().__init__(
            trajectory_store=trajectory_store,
            evolution_trigger=EvolutionTriggerPoint.NONE,
        )
        self._skills_dir = skills_dir
        self._auto_trigger = auto_trigger
        self._min_team_members = min_team_members_for_create
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

        if not self._should_propose_new_team_skill():
            return

        agent = ctx.agent
        controller = getattr(agent, "_loop_controller", None)
        if controller is None:
            logger.warning(
                "[TeamSkillCreateRail] team skill creation proposal dropped: "
                "no TaskLoopController available. "
                "This rail only works in task-loop mode."
            )
            return

        if self._language == "cn":
            prompt = _FOLLOW_UP_PROMPT_CN.format(skills_dir=self._skills_dir)
        else:
            prompt = _FOLLOW_UP_PROMPT_EN.format(skills_dir=self._skills_dir)

        logger.info(
            "[TeamSkillCreateRail] Multi-agent collaboration pattern detected, enqueuing follow_up. "
            "language=%s, skills_dir=%s, prompt_length=%d",
            self._language,
            self._skills_dir,
            len(prompt),
        )
        logger.debug("[TeamSkillCreateRail] follow_up prompt: %s", prompt)
        controller.enqueue_follow_up(prompt)
        self._proposal_sent = True
        logger.info("[TeamSkillCreateRail] follow_up enqueued successfully")

    # ---- Threshold detection ----

    def _should_propose_new_team_skill(self) -> bool:
        """Check if spawn_member calls meet team creation threshold.

        Uses the trajectory builder collected by EvolutionRail,
        avoiding redundant message parsing.
        """
        if self._builder is None:
            logger.debug("[TeamSkillCreateRail] trajectory builder is None, skipping")
            return False

        spawn_count = 0
        for step in self._builder.steps:
            if step.kind == "tool" and step.detail:
                tool_name = getattr(step.detail, "tool_name", "")
                if "spawn_member" in tool_name:
                    spawn_count += 1

        if spawn_count < self._min_team_members:
            logger.debug(
                "[TeamSkillCreateRail] spawn_member count %d below threshold %d, skipping",
                spawn_count,
                self._min_team_members,
            )
            return False

        logger.info(
            "[TeamSkillCreateRail] team skill creation threshold met: %d spawn_member calls "
            "(threshold: %d)",
            spawn_count,
            self._min_team_members,
        )
        return True


__all__ = ["TeamSkillCreateRail"]