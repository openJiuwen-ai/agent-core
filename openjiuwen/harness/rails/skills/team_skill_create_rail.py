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

from pathlib import Path
from typing import Optional

from openjiuwen.agent_evolving.trajectory import TrajectoryStore
from openjiuwen.agent_evolving.utils import infer_skill_from_texts, parse_top_level_frontmatter
from openjiuwen.core.common.logging import logger
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext
from openjiuwen.harness.rails.evolution.evolution_rail import EvolutionRail, EvolutionTriggerPoint

_FOLLOW_UP_PROMPT_CN = (
    "**重要：你必须先向用户确认，不可跳过此步骤。**\n"
    "系统检测到对话中 spawn 了多个团队成员，可能值得创建团队技能。请按以下步骤执行：\n"
    "1. 直接询问或调用 ask_user 工具向用户确认：\n"
    '   - 问题："我检测到多 Agent 协作模式可能值得创建为团队技能。是否创建？"\n'
    '   - 选项：["创建"，"跳过"，"自定义指令：（请描述需求）"]\n'
    '2. 如果用户选择"创建"或提供了自定义指令，请调用 **team-skill-creator** 技能，'
    "根据用户的要求和当前对话上下文执行团队技能创建。\n"
    "   新技能应保存到技能目录：{skills_dir}"
)
_FOLLOW_UP_PROMPT_EN = (
    "**Important: You MUST confirm with the user first. Do not skip this step.**\n"
    "The system detected multiple team member spawns that may be worth creating as a Team Skill. "
    "Please follow these steps:\n"
    "1. Directly inquire or invoke the `ask_user` tool to confirm with the user:\n"
    '   - Question: "I detected a multi-agent collaboration pattern that may be worth creating '
    'as a Team Skill. Create it?"\n'
    '   - Options: ["Create", "Skip", "Custom instruction: (describe your needs)"]\n'
    '2. If user chooses "Create" or provides a custom instruction, invoke the **team-skill-creator** skill '
    "to execute the team skill creation.\n"
    "   Save the new skill to: {skills_dir}"
)
_TEAM_SKILL_KINDS = {"team-skill", "swarm-skill"}


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
        self._completed_session_id: Optional[str] = None
        self._proposed_spawn_counts: dict[str, int] = {}

    async def _on_after_task_iteration(self, ctx: AgentCallbackContext) -> None:
        """Try to enqueue creation proposal only after team completion has been marked."""
        self._maybe_enqueue_creation_follow_up(ctx)

    async def _on_after_invoke(self, ctx: AgentCallbackContext) -> None:
        """Task-list-drained callback may arrive near invoke end; enqueue at this boundary."""
        self._maybe_enqueue_creation_follow_up(ctx)

    async def notify_team_completed(
        self,
        ctx: Optional[AgentCallbackContext] = None,
    ) -> bool:
        """Mark the current invoke for team-skill creation proposal at a lifecycle boundary."""
        if not self._auto_trigger:
            logger.info("[TeamSkillCreateRail] notify_team_completed ignored because auto_trigger is disabled")
            return False
        if self.builder is None:
            logger.warning(
                "[TeamSkillCreateRail] notify_team_completed: no trajectory available "
                "(before_invoke may not have fired)"
            )
            return False

        self._completed_session_id = self.builder.session_id
        logger.debug(
            "[TeamSkillCreateRail] notify_team_completed marked session_id=%s",
            self._completed_session_id,
        )
        return True

    def _current_builder_session_id(self) -> Optional[str]:
        """Return the current trajectory builder session id, if available."""
        if self.builder is None:
            return None
        return self.builder.session_id

    def _build_follow_up_prompt(self) -> str:
        """Build the user-confirmation follow-up prompt."""
        if self._language == "cn":
            return _FOLLOW_UP_PROMPT_CN.format(skills_dir=self._skills_dir)
        return _FOLLOW_UP_PROMPT_EN.format(skills_dir=self._skills_dir)

    def _maybe_enqueue_creation_follow_up(self, ctx: AgentCallbackContext) -> bool:
        """Enqueue the team-skill creation follow-up when completion and threshold gates pass."""
        session_id = self._current_builder_session_id()
        spawn_count = self._count_spawn_member_calls()
        if not self._can_enqueue_creation_follow_up(session_id, spawn_count):
            return False

        agent = ctx.agent
        controller = getattr(agent, "_loop_controller", None)
        if controller is None:
            logger.warning(
                "[TeamSkillCreateRail] team skill creation proposal dropped: "
                "no TaskLoopController available. "
                "This rail only works in task-loop mode."
            )
            return False

        prompt = self._build_follow_up_prompt()

        logger.info(
            "[TeamSkillCreateRail] Multi-agent collaboration pattern detected after team completion, "
            "enqueuing follow_up. language=%s, skills_dir=%s, prompt_length=%d",
            self._language,
            self._skills_dir,
            len(prompt),
        )
        logger.debug("[TeamSkillCreateRail] follow_up prompt: %s", prompt)
        controller.enqueue_follow_up(prompt)
        self._proposed_spawn_counts[session_id] = spawn_count
        if self._completed_session_id == session_id:
            self._completed_session_id = None
        logger.info("[TeamSkillCreateRail] follow_up enqueued successfully")
        return True

    def _can_enqueue_creation_follow_up(self, session_id: Optional[str], spawn_count: int) -> bool:
        """Check completion, threshold, dedupe, and existing-team-skill gates."""
        if not self._auto_trigger or session_id is None or self._completed_session_id != session_id:
            return False
        if spawn_count <= self._proposed_spawn_counts.get(session_id, 0):
            return False
        if spawn_count < self._min_team_members:
            logger.debug(
                "[TeamSkillCreateRail] spawn_member count %d below threshold %d, skipping",
                spawn_count,
                self._min_team_members,
            )
            return False
        if self._detect_used_team_skill() is not None:
            logger.info("[TeamSkillCreateRail] existing team skill detected, skipping creation proposal")
            return False
        return True

    # ---- Threshold detection ----

    def _should_propose_new_team_skill(self) -> bool:
        """Check if spawn_member calls meet team creation threshold.

        Uses the trajectory builder collected by EvolutionRail,
        avoiding redundant message parsing.
        """
        spawn_count = self._count_spawn_member_calls()
        if spawn_count == 0 and self._builder is None:
            logger.debug("[TeamSkillCreateRail] trajectory builder is None, skipping")
            return False

        if spawn_count < self._min_team_members:
            logger.debug(
                "[TeamSkillCreateRail] spawn_member count %d below threshold %d, skipping",
                spawn_count,
                self._min_team_members,
            )
            return False

        logger.info(
            "[TeamSkillCreateRail] team skill creation threshold met: %d spawn_member calls (threshold: %d)",
            spawn_count,
            self._min_team_members,
        )
        return True

    def _count_spawn_member_calls(self) -> int:
        """Count recorded spawn_member tool calls in the current trajectory builder."""
        if self._builder is None:
            return 0

        spawn_count = 0
        for step in self._builder.steps:
            if step.kind == "tool" and step.detail:
                tool_name = getattr(step.detail, "tool_name", "")
                if "spawn_member" in tool_name:
                    spawn_count += 1
        return spawn_count

    def _detect_used_team_skill(self) -> Optional[str]:
        """Return the team skill referenced by the trajectory, if any."""
        if self._builder is None:
            return None

        known_team_skills = self._known_team_skill_names()
        if not known_team_skills:
            return None

        skill_tool_payloads: list[object] = []
        texts: list[str] = []
        for step in self._builder.steps:
            if step.kind != "tool" or not step.detail:
                continue
            tool_name = getattr(step.detail, "tool_name", "")
            if tool_name == "skill_tool":
                skill_tool_payloads.append(getattr(step.detail, "call_args", None))
            texts.append(str(getattr(step.detail, "call_args", "")))
            texts.append(str(getattr(step.detail, "call_result", "")))

        used_skill = infer_skill_from_texts(
            known_team_skills,
            skill_tool_payloads=skill_tool_payloads,
            texts=texts,
        )
        if used_skill:
            logger.info("[TeamSkillCreateRail] detected existing team skill '%s' from trajectory", used_skill)
        return used_skill

    def _known_team_skill_names(self) -> set[str]:
        """List skill names in skills_dir whose SKILL.md declares a team/swarm skill kind."""
        root = Path(self._skills_dir)
        if not root.exists():
            return set()

        names: set[str] = set()
        for skill_md in root.glob("*/SKILL.md"):
            try:
                frontmatter = parse_top_level_frontmatter(skill_md.read_text(encoding="utf-8"))
            except OSError:
                continue
            if frontmatter.get("kind") in _TEAM_SKILL_KINDS:
                names.add(skill_md.parent.name)
        return names


__all__ = ["TeamSkillCreateRail"]
