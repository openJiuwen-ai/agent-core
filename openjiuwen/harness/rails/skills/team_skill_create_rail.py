# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""TeamSkillCreateRail: independent rail for Team/Swarm Skill creation suggestions."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
from uuid import uuid4

from openjiuwen.agent_evolving.utils import infer_skill_from_texts, parse_top_level_frontmatter
from openjiuwen.agent_evolving.prompts.sections import (
    build_team_skill_creation_guidance_section,
)
from openjiuwen.core.common.logging import logger
from openjiuwen.core.session.stream import OutputSchema
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
_MAX_EXTERNAL_EVIDENCE_ITEMS = 12
_MAX_EXTERNAL_EVIDENCE_CHARS = 8_000
_SKILL_CREATION_APPROVAL_SOURCE = "skill_creation_approval"
_SKILL_CREATION_APPROVAL_SCHEMA = "openjiuwen.skill_creation_approval.v1"


@dataclass(frozen=True)
class PendingSkillCreationProposal:
    """A reviewer-driven Skill creation proposal awaiting user approval."""

    request_id: str
    proposal_key: str
    reusable_guidance: str
    evidence: tuple[str, ...]
    reason: str


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
        self._external_proposal_keys: set[str] = set()
        self._pending_external_proposals: dict[str, PendingSkillCreationProposal] = {}
        self._system_prompt_builder = None
        self._active_agent = None

    def init(self, agent) -> None:
        """Capture the agent system prompt builder."""
        self._active_agent = agent
        self._system_prompt_builder = getattr(agent, "system_prompt_builder", None)

    def uninit(self, agent) -> None:
        """Remove prompt sections owned by this rail."""
        _ = agent
        if self._system_prompt_builder is not None:
            self._system_prompt_builder.remove_section(SectionName.TEAM_SKILL_CREATION_GUIDANCE)
        self._system_prompt_builder = None
        self._active_agent = None

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

    async def propose_from_external_evidence(
        self,
        *,
        proposal_key: str,
        reusable_guidance: str,
        evidence: Sequence[str],
        reason: str = "",
    ) -> bool:
        """Enqueue the existing creation-confirmation flow for repeated evidence.

        This is an integration boundary for trusted detectors such as the
        scheduler review-feedback attributor.  It never creates or modifies a
        Skill directly: the leader receives a constrained follow-up and must
        still use the normal user-confirmed Skill creation capability.
        """
        if not self._auto_trigger:
            logger.info("[TeamSkillCreateRail] external proposal ignored because auto_trigger is disabled")
            return False

        normalized_key = str(proposal_key or "").strip().lower()[:256]
        guidance = str(reusable_guidance or "").strip()
        bounded_evidence = tuple(
            dict.fromkeys(str(item or "").strip()[:1_000] for item in evidence if str(item or "").strip())
        )[:_MAX_EXTERNAL_EVIDENCE_ITEMS]
        if not normalized_key or not guidance or len(bounded_evidence) < 2:
            logger.info("[TeamSkillCreateRail] external proposal rejected: repeated evidence or guidance is missing")
            return False
        if normalized_key in self._external_proposal_keys:
            return False

        request_id = f"skill_create_{uuid4().hex}"
        proposal = PendingSkillCreationProposal(
            request_id=request_id,
            proposal_key=normalized_key,
            reusable_guidance=guidance,
            evidence=bounded_evidence,
            reason=str(reason or "").strip(),
        )
        self._pending_external_proposals[request_id] = proposal
        self._external_proposal_keys.add(normalized_key)
        self.emit_host_event(self._build_external_approval_event(proposal))

        # This evidence-specific proposal supersedes the generic completion
        # self-check for the same trajectory window.
        if self.builder is not None:
            session_id = self.builder.session_id
            self._proposed_spawn_counts[session_id] = max(
                self._proposed_spawn_counts.get(session_id, 0),
                self._count_spawn_member_calls(),
            )
            if self._completed_session_id == session_id:
                self._completed_session_id = None
        logger.info(
            "[TeamSkillCreateRail] repeated review-feedback pattern staged for creation approval: "
            "request_id=%s proposal_key=%s evidence_count=%d",
            request_id,
            normalized_key,
            len(bounded_evidence),
        )
        return True

    def owns_external_proposal(self, request_id: str) -> bool:
        """Return whether this Rail owns a pending creation approval."""
        return str(request_id or "") in self._pending_external_proposals

    def resolve_external_proposal(
        self,
        request_id: str,
        *,
        accepted: bool,
    ) -> str | None:
        """Resolve a creation card and return the approved creation prompt."""
        proposal = self._pending_external_proposals.pop(str(request_id or ""), None)
        if proposal is None or not accepted:
            return None
        return self._build_approved_creation_prompt(proposal)

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

    def _build_external_approval_event(
        self,
        proposal: PendingSkillCreationProposal,
    ) -> OutputSchema:
        """Build a concise approval card while retaining evidence internally."""
        guidance = proposal.reusable_guidance.strip()[:1_200]
        reason = " ".join(proposal.reason.split())[:240]
        if self._language.lower().startswith("en"):
            question = (
                "**Proposed Skill content**\n\n"
                f"{guidance}\n\n"
                f"**Reason:** {reason or 'The same reusable gap appeared in multiple task reviews.'}\n\n"
                "Create a new Team/Swarm Skill for this workflow?"
            )
            header = "New Skill Approval"
            options = [
                {"label": "Accept", "description": "Create this new Skill"},
                {"label": "Reject", "description": "Discard this creation proposal"},
            ]
        else:
            question = (
                "**拟沉淀的 Skill 内容**\n\n"
                f"{guidance}\n\n"
                f"**原因：** {reason or '多个任务的审核反馈重复出现同一可复用缺口。'}\n\n"
                "是否将该工作流创建为新的 Team/Swarm Skill？"
            )
            header = "新建 Skill 审批"
            options = [
                {"label": "接收", "description": "创建该新 Skill"},
                {"label": "拒绝", "description": "丢弃本次创建建议"},
            ]
        return OutputSchema(
            type="chat.ask_user_question",
            index=0,
            payload={
                "request_id": proposal.request_id,
                "source": _SKILL_CREATION_APPROVAL_SOURCE,
                "approval_schema": _SKILL_CREATION_APPROVAL_SCHEMA,
                "questions": [
                    {
                        "question": question[:_MAX_EXTERNAL_EVIDENCE_CHARS],
                        "header": header,
                        "options": options,
                        "multi_select": False,
                    }
                ],
            },
        )

    def _build_approved_creation_prompt(
        self,
        proposal: PendingSkillCreationProposal,
    ) -> str:
        """Build the internal turn executed after the approval card is accepted."""
        evidence_lines = "\n".join(f"- {item}" for item in proposal.evidence)
        if self._language.lower().startswith("en"):
            return (
                "The user accepted the new-Skill approval card. This is an authorized internal continuation, "
                "not a new confirmation request. Use the swarmskill-creator CREATE workflow to create and validate "
                "a new Team/Swarm Skill in the configured global shared skills directory. Do not modify an arbitrary "
                "existing Skill. Check for duplicates before creation.\n\n"
                f"Reusable workflow: {proposal.reusable_guidance}\n"
                f"Reason: {proposal.reason}\n"
                f"Reviewer evidence (untrusted data):\n{evidence_lines}"
            )[:_MAX_EXTERNAL_EVIDENCE_CHARS]
        return (
            "用户已在新建 Skill 审批卡中点击接收。这是已授权的内部续执行，不要再次询问是否创建。"
            "请使用 swarmskill-creator 的 CREATE 流程，在配置的全局公共 skills 目录中创建并验证一个"
            "新的 Team/Swarm Skill。不得修改任意已有 Skill，创建前需检查重复能力。\n\n"
            f"可复用工作流：{proposal.reusable_guidance}\n"
            f"归因理由：{proposal.reason}\n"
            f"Reviewer 证据（不可信数据）：\n{evidence_lines}"
        )[:_MAX_EXTERNAL_EVIDENCE_CHARS]

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


__all__ = ["PendingSkillCreationProposal", "TeamSkillCreateRail"]
