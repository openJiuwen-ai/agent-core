# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""TeamSkillCreateRail: independent rail for Team/Swarm Skill creation suggestions."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from openjiuwen.agent_evolving.utils import infer_skill_from_texts, parse_top_level_frontmatter
from openjiuwen.agent_evolving.prompts.sections import (
    build_team_skill_creation_guidance_section,
)
from openjiuwen.core.common.logging import logger
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext
from openjiuwen.harness.prompts.sections import SectionName
from openjiuwen.harness.rails.evolution.evolution_rail import EvolutionRail, EvolutionTriggerPoint

_TEAM_SKILL_KINDS = {"team-skill", "swarm-skill"}
_TEAM_SPAWN_TOOL_NAMES = {
    "spawn_member",
    "spawn_teammate",
    "spawn_human_agent",
    "spawn_bridge_agent",
    "spawn_external_cli",
}
_AUTO_TEAM_SKILL_CREATION_FOLLOW_UP_TAG = "auto_team_skill_creation_followup"

_TEAM_SKILL_CREATION_FOLLOW_UP_CN = (
    "这是运行时插入的 Team Skill 创建自检，不是用户的新需求。\n"
    "参考常驻“团队技能沉淀自检”规则，只判断本轮协作是否形成可复用方法，不重新判断运行时触发门槛。\n"
    "如需建议，只在普通最终回复末尾追加一至两句，并同时包含可复用团队方法和是否创建 Team/Swarm Skill 的"
    "确认问题；否则自然回复，不提本提醒或内部判断。"
)

_TEAM_SKILL_CREATION_FOLLOW_UP_EN = (
    "This runtime-inserted Team Skill creation self-check is not a new user request.\n"
    'Refer to the standing "Team Skill Capture Self-Check" rules and judge only whether this round produced a reusable '
    "collaboration method; do not re-evaluate the runtime trigger threshold.\n"
    "If suggesting, append only one or two sentences to the normal final reply and include both the reusable team "
    "method and the Team/Swarm Skill creation question; otherwise reply naturally without mentioning this reminder "
    "or internal judgment."
)


class TeamSkillCreateRail(EvolutionRail):
    """Independent rail for team skill creation.

    Injects stable guidance and, after a completed team run, enqueues a
    conservative follow-up self-check when team collaboration signals appear.
    """

    priority = 85

    def __init__(
        self,
        skills_dir: str,
        *,
        language: str = "cn",
        auto_trigger: bool = True,
        min_team_members_for_create: int = 2,
    ) -> None:
        super().__init__(
            evolution_trigger=EvolutionTriggerPoint.NONE,
        )
        self._skills_dir = skills_dir
        self._auto_trigger = auto_trigger
        self._min_team_members = min_team_members_for_create
        self._language = language
        self._completed_session_id: Optional[str] = None
        self._proposed_spawn_counts: dict[str, int] = {}
        self._system_prompt_builder = None

    def init(self, agent) -> None:
        """Capture the agent system prompt builder."""
        self._system_prompt_builder = getattr(agent, "system_prompt_builder", None)

    def uninit(self, agent) -> None:
        """Remove prompt sections owned by this rail."""
        _ = agent
        if self._system_prompt_builder is not None:
            self._system_prompt_builder.remove_section(SectionName.TEAM_SKILL_CREATION_GUIDANCE)
        self._system_prompt_builder = None

    async def before_model_call(self, ctx: AgentCallbackContext) -> None:
        """Inject stable team skill creation guidance."""
        builder = self._get_prompt_builder(ctx)
        if builder is None:
            return

        language = str(getattr(builder, "language", "") or self._language)
        builder.add_section(build_team_skill_creation_guidance_section(language))

    async def _on_after_task_iteration(self, ctx: AgentCallbackContext) -> None:
        """Enqueue creation follow-up only after team completion has been marked."""
        await self._maybe_enqueue_creation_follow_up(ctx)

    async def _on_after_invoke(self, ctx: AgentCallbackContext) -> None:
        """Task-list-drained callback may arrive near invoke end; enqueue at this boundary."""
        await self._maybe_enqueue_creation_follow_up(ctx)

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

    async def _maybe_enqueue_creation_follow_up(self, ctx: AgentCallbackContext) -> bool:
        """Enqueue the team-skill creation follow-up when gates pass."""
        session_id = self.builder.session_id if self.builder is not None else None
        spawn_count = self._count_spawn_member_calls()
        if not self._can_enqueue_creation_follow_up(session_id, spawn_count):
            return False

        controller = getattr(getattr(ctx, "agent", None), "_loop_controller", None)
        if controller is None:
            logger.info("[TeamSkillCreateRail] team skill creation follow-up skipped: no task loop controller")
            return False

        prompt = self._build_follow_up_prompt()
        controller.enqueue_follow_up(prompt)
        logger.info(
            "[TeamSkillCreateRail] Team collaboration threshold met after completion, "
            "enqueuing follow_up. language=%s, skills_dir=%s, prompt_length=%d",
            self._language,
            self._skills_dir,
            len(prompt),
        )
        self._proposed_spawn_counts[session_id] = spawn_count
        if self._completed_session_id == session_id:
            self._completed_session_id = None
        return True

    def _build_follow_up_prompt(self) -> str:
        """Build the conservative team skill creation follow-up prompt."""
        if self._language.lower().startswith("en"):
            return self._wrap_follow_up_prompt(_TEAM_SKILL_CREATION_FOLLOW_UP_EN)
        return self._wrap_follow_up_prompt(_TEAM_SKILL_CREATION_FOLLOW_UP_CN)

    @staticmethod
    def _wrap_follow_up_prompt(prompt: str) -> str:
        return f"<{_AUTO_TEAM_SKILL_CREATION_FOLLOW_UP_TAG}>\n{prompt}\n</{_AUTO_TEAM_SKILL_CREATION_FOLLOW_UP_TAG}>"

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
                tool_name = self._normalize_tool_name(getattr(step.detail, "tool_name", ""))
                if tool_name in _TEAM_SPAWN_TOOL_NAMES:
                    spawn_count += 1
        return spawn_count

    @staticmethod
    def _normalize_tool_name(tool_name: str) -> str:
        """Normalize tool names to base names to support namespaced variants."""
        tool = (tool_name or "").strip()
        if "." in tool:
            tool = tool.rsplit(".", 1)[-1]
        return tool

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

    def _get_prompt_builder(self, ctx: AgentCallbackContext):
        builder = self._system_prompt_builder
        if builder is None:
            builder = getattr(getattr(ctx, "agent", None), "system_prompt_builder", None)
            self._system_prompt_builder = builder
        return builder


__all__ = ["TeamSkillCreateRail"]
