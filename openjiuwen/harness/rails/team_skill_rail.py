# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""TeamSkillRail: online auto-evolution for multi-agent team skills.

Counterpart of SkillEvolutionRail for team skills:
generate evolution records → user approval (default) → append

Inherits EvolutionRail to gain automatic trajectory collection.
"""

from __future__ import annotations

import json
import re
import time
import uuid
from dataclasses import asdict, dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Optional, Union

from openjiuwen.agent_evolving.checkpointing import EvolutionStore
from openjiuwen.agent_evolving.checkpointing.types import (
    PendingChange,
)
from openjiuwen.agent_evolving.optimizer.skill_call.experience_scorer import (
    ExperienceScorer,
)
from openjiuwen.agent_evolving.optimizer.team_skill_optimizer import (
    TeamSkillOptimizer,
)
from openjiuwen.agent_evolving.trajectory import (
    TeamTrajectoryAggregator,
    Trajectory,
    TrajectoryStore,
)
from openjiuwen.agent_evolving.utils import infer_skill_from_texts
from openjiuwen.core.common.logging import logger
from openjiuwen.core.memory.lite.frontmatter import parse_frontmatter
from openjiuwen.core.session.stream import OutputSchema
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext, ToolCallInputs
from openjiuwen.harness.rails.evolution_rail import EvolutionRail, EvolutionTriggerPoint


class TeamSignalType(Enum):
    """Types of evolution signals the rail can detect."""

    USER_REQUEST = "user_request"
    TRAJECTORY_ISSUE = "trajectory_issue"


@dataclass
class UserIntent:
    """Parsed user improvement intention."""

    is_improvement: bool
    intent: str


@dataclass
class TrajectoryIssue:
    """Detected trajectory issue."""

    issue_type: str
    description: str
    affected_role: str = ""
    severity: str = "medium"  # low | medium | high


class TeamSkillRail(EvolutionRail):
    """Team skill evolution rail — counterpart of SkillEvolutionRail.

    SkillEvolutionRail handles 1D skill PATCH;
    TeamSkillRail handles team skill PATCH.
    New team skill creation is handled by TeamSkillCreateRail.
    Both can coexist on the same agent via agent_customizer.
    """

    priority = 80
    _SKILL_MD_RE = re.compile(r"[/\\]([^/\\]+)[/\\]SKILL\.md", re.IGNORECASE)

    _USER_REQUEST_PROMPT_CN = (
        "判断以下用户输入是否包含对当前团队任务或团队协作方式的改进意见。\n"
        "如果是，提取改进意图的摘要。\n\n"
        "团队技能描述：{team_skill_description}\n"
        "当前角色：{roles}\n"
        "用户输入：{user_messages}\n\n"
        '输出 JSON: {{"is_improvement": true/false, "intent": "str"}}\n'
    )

    _USER_REQUEST_PROMPT_EN = (
        "Determine if the following user input contains improvement suggestions "
        "for the current team task or collaboration approach.\n"
        "If yes, extract a summary of the improvement intent.\n\n"
        "Team skill description: {team_skill_description}\n"
        "Current roles: {roles}\n"
        "User input: {user_messages}\n\n"
        'Output JSON: {{"is_improvement": true/false, "intent": "str"}}\n'
    )

    _TRAJECTORY_ISSUE_PROMPT_CN = (
        "分析以下执行轨迹，判断团队技能是否存在不足需要演进。\n\n"
        "当前团队技能：\n{skill_content}\n\n"
        "执行轨迹摘要：\n{trajectory_summary}\n\n"
        "请从以下维度分析：\n"
        "- 角色配合是否恰当（是否有角色间协作断裂、数据未传递）\n"
        "- 约束是否被违反（超时、产出格式不合规）\n"
        "- 流程是否低效（重复调用、多余步骤）\n"
        "- 角色能力是否不足（某角色多次失败或产出质量不达标）\n\n"
        "如果存在不足，输出 JSON 数组：\n"
        '[{{"issue_type": str, "description": str, "affected_role": str, "severity": "low"|"medium"|"high"}}]\n'
        "如果没有问题，输出空数组 []。"
    )

    _TRAJECTORY_ISSUE_PROMPT_EN = (
        "Analyze the following execution trajectory and determine whether the team skill has deficiencies.\n\n"
        "Current team skill:\n{skill_content}\n\n"
        "Trajectory summary:\n{trajectory_summary}\n\n"
        "Analyze from these dimensions:\n"
        "- Role coordination (collaboration breaks, data not passed)\n"
        "- Constraint violations (timeout, output format issues)\n"
        "- Workflow inefficiency (redundant calls, extra steps)\n"
        "- Role capability gaps (repeated failures, poor output quality)\n\n"
        "If issues exist, output a JSON array:\n"
        '[{{"issue_type": str, "description": str, "affected_role": str, "severity": "low"|"medium"|"high"}}]\n'
        "If no issues, output empty array [].\n"
    )

    def __init__(
        self,
        skills_dir: Union[str, list[str]],
        *,
        llm: Any,
        model: str,
        language: str = "cn",
        trajectory_store: Optional[TrajectoryStore] = None,
        team_trajectory_store: Optional[TrajectoryStore] = None,
        auto_save: bool = False,
        async_evolution: bool = True,
        team_id: Optional[str] = None,
        trajectories_dir: Optional[Path] = None,
    ) -> None:
        super().__init__(
            trajectory_store=trajectory_store,
            team_trajectory_store=team_trajectory_store,
            accumulate_trajectory=True,
            evolution_trigger=EvolutionTriggerPoint.NONE,
            async_evolution=async_evolution,
        )
        self._store = EvolutionStore(skills_dir)
        debug_dir = str(self._store.base_dirs[0].parent / "_debug")
        self._optimizer = TeamSkillOptimizer(
            llm,
            model,
            language,
            debug_dir=debug_dir,
        )
        self._scorer = ExperienceScorer(llm, model, language)
        self._auto_save = auto_save

        self._pending_patch_snapshots: dict[str, PendingChange] = {}
        self._evolution_triggered: bool = False
        self._team_id = team_id
        self._trajectories_dir = trajectories_dir

        logger.info(
            "[TeamSkillRail] initialized: skills_dir=%s, model=%s, auto_save=%s, team_id=%s",
            skills_dir,
            model,
            auto_save,
            team_id,
        )

    @property
    def store(self) -> EvolutionStore:
        return self._store

    @property
    def scorer(self) -> ExperienceScorer:
        """Get the experience scorer."""
        return self._scorer

    # ===== TUI progress helper =====

    def _emit_progress(self, message: str) -> None:
        """Push a progress message to TUI (rendered as reasoning step) and log it.

        Trailing newline is required because the TUI concatenates consecutive
        reasoning chunks without separators (see appendThinkingChunk).
        """
        logger.info("[TeamSkillRail] %s", message)
        event = OutputSchema(
            type="llm_reasoning",
            index=0,
            payload={"content": f"[Team Skill Evolution] {message}\n"},
        )
        self._pending_approval_events.append(event)

    # ===== Lifecycle hooks =====

    async def _on_before_invoke(self, ctx: AgentCallbackContext) -> None:
        # Reset evolution trigger flag on first round of a new session
        if self._builder is None:
            self._evolution_triggered = False

    async def _snapshot_for_evolution(
        self,
        trajectory: Trajectory,
        ctx: Optional[AgentCallbackContext],
    ) -> Optional[dict]:
        """Phase 1: TeamSkillRail only needs trajectory (no session dependency)."""
        return {"trajectory": trajectory, "skill_name": "team-skill"}

    async def _on_after_tool_call(self, ctx: AgentCallbackContext) -> None:
        """Detect 'all tasks completed' via view_task result and trigger evolution."""
        if self._evolution_triggered:
            return

        inputs = ctx.inputs
        if not isinstance(inputs, ToolCallInputs):
            return

        if inputs.tool_name != "view_task":
            return

        result_preview = str(inputs.tool_result)[:300]
        logger.info("[TeamSkillRail] view_task intercepted, result preview: %s", result_preview)

        if not self._all_tasks_completed(inputs.tool_result):
            logger.info("[TeamSkillRail] view_task: tasks still in progress, skipping")
            return

        await self.notify_team_completed(ctx)

    # ===== Public API: external completion notification =====

    async def notify_team_completed(
        self,
        ctx: Optional[AgentCallbackContext] = None,
    ) -> bool:
        """Trigger skill evolution when all team tasks are completed.

        In async mode: snapshots data, spawns background task, returns immediately.
        In sync mode: awaits run_evolution directly (backward-compatible).
        """
        if self._evolution_triggered:
            return False

        self._evolution_triggered = True
        self._emit_progress("all tasks completed, starting evolution analysis...")

        if self.builder is None:
            logger.warning(
                "[TeamSkillRail] notify_team_completed: no trajectory available (before_invoke may not have fired)"
            )
            return False

        # Note: trajectory save is handled by EvolutionRail.after_invoke
        trajectory = self.builder.build()
        trajectory.steps = list(trajectory.steps)

        if self._async_evolution:
            snapshot = await self._snapshot_for_evolution(trajectory, ctx)
            if snapshot is not None:
                from openjiuwen.core.common.background_tasks import create_background_task

                bg_task = await create_background_task(
                    self._safe_run_evolution(snapshot),
                    name="evolution-team-skill",
                    group="evolution",
                )
                self._bg_tasks.add(bg_task)
                self._bg_tasks = {t for t in self._bg_tasks if not t.done()}
        else:
            await self.run_evolution(trajectory, ctx)

        self._dump_trajectory_debug(trajectory)
        return True

    @staticmethod
    def _all_tasks_completed(result: Any) -> bool:
        """Check view_task result: True if >=1 completed and 0 non-terminal tasks."""
        text = str(result).lower()
        if "completed" not in text:
            return False
        non_terminal = ("pending", "claimed", "in_progress", "blocked")
        return not any(s in text for s in non_terminal)

    # ===== EvolutionRail hook =====

    async def run_evolution(
        self,
        trajectory: Trajectory,
        ctx: Optional[AgentCallbackContext] = None,
        *,
        snapshot: Optional[dict] = None,
    ) -> None:
        """Triggered when view_task shows all member tasks completed.

        Dual-path signal detection:
        1. Active: LLM determines if user input contains improvement intent
        2. Passive: LLM analyzes trajectory for team skill deficiencies
        """
        t0 = time.time()
        try:
            # Aggregate team trajectories from team_trajectory_store
            if self._team_trajectory_store is not None:
                aggregator = TeamTrajectoryAggregator(
                    store=self._team_trajectory_store,
                    team_id=self._team_id or "unknown",
                )
                team_traj = aggregator.aggregate(trajectory.session_id, filter_collaborative=True)
                if team_traj.members:
                    self._emit_progress(
                        f"aggregated {team_traj.combined.meta.get('member_count', len(team_traj.members))} members, "
                        f"{len(team_traj.combined.steps)} collaborative steps"
                    )
                    trajectory = team_traj.combined

            used_skill = self._detect_used_team_skill(trajectory)
            if not used_skill:
                logger.info("[TeamSkillRail] no existing skill detected, skipping")
                self._emit_progress("no existing skill found, skipping")
                return

            logger.info("[TeamSkillRail] detected existing skill '%s'", used_skill)

            # Load skill content for signal detection
            current_content = await self._store.read_skill_content(used_skill)

            # Active signal: LLM determines user improvement intent
            ctx_messages = await self._collect_messages(ctx) if ctx else []
            user_intent = await self._detect_user_request(ctx_messages, current_content)

            # Passive signal: LLM analyzes trajectory issues (only if no active signal)
            trajectory_issues: list[TrajectoryIssue] = []
            if not user_intent:
                trajectory_issues = await self._detect_trajectory_issues(
                    trajectory,
                    current_content,
                )

            if not user_intent and not trajectory_issues:
                logger.info("[TeamSkillRail] no signals detected for '%s'", used_skill)
                self._emit_progress("no evolution signals detected")
                return

            # Generate patch based on signal type
            if user_intent:
                self._emit_progress(f"user improvement intent detected: {user_intent.intent[:100]}")
                record = await self._optimizer.generate_user_patch(
                    trajectory,
                    used_skill,
                    user_intent.intent,
                )
            else:
                self._emit_progress(f"trajectory issues detected: {len(trajectory_issues)} issues")
                record = await self._optimizer.generate_trajectory_patch(
                    trajectory,
                    used_skill,
                    [asdict(i) for i in trajectory_issues],
                )

            if record:
                await self._handle_patch_record(record, used_skill)
            else:
                self._emit_progress("no patch generated")

            elapsed = time.time() - t0
            logger.info("[TeamSkillRail] run_evolution completed in %.1fs", elapsed)
        except Exception as exc:
            logger.warning("[TeamSkillRail] run_evolution failed: %s", exc, exc_info=True)
            self._emit_progress(f"evolution analysis failed: {exc}")

    # ===== PATCH path =====

    async def _handle_patch(
        self,
        trajectory: Trajectory,
        ctx: Optional[AgentCallbackContext],
        skill_name: str,
    ) -> None:
        logger.info("[TeamSkillRail] PATCH: reading current content of '%s'", skill_name)
        current_content = await self._store.read_skill_content(skill_name)
        content_len = len(current_content) if current_content else 0
        logger.info("[TeamSkillRail] PATCH: current content length=%d, calling LLM...", content_len)

        self._emit_progress(f"comparing trajectory against skill '{skill_name}'...")

        t0 = time.time()
        patch = await self._optimizer.generate_patch(
            trajectory=trajectory,
            skill_name=skill_name,
            current_skill_content=current_content,
        )
        elapsed = time.time() - t0

        if not patch:
            logger.info("[TeamSkillRail] PATCH: LLM found no patch needed for '%s' (%.1fs)", skill_name, elapsed)
            self._emit_progress(f"no new learnings found for '{skill_name}'")
            return

        logger.info(
            "[TeamSkillRail] PATCH generated: section='%s', content_len=%d (%.1fs)",
            patch.change.section,
            len(patch.change.content),
            elapsed,
        )

        if self._auto_save:
            await self._store.append_record(skill_name, patch)
            logger.info("[TeamSkillRail] PATCH auto-saved for '%s'", skill_name)
            self._emit_progress(f"patch auto-saved to '{skill_name}'")
        else:
            pending = PendingChange.make(skill_name, [patch])
            pending.change_id = f"team_skill_evolve_{uuid.uuid4().hex[:8]}"
            self._pending_patch_snapshots[pending.change_id] = pending
            self._emit_patch_approval_event(skill_name, pending)
            logger.info(
                "[TeamSkillRail] PATCH buffered for approval: change_id=%s, skill='%s'",
                pending.change_id,
                skill_name,
            )
            self._emit_progress(f"patch for '{skill_name}' ready, awaiting your approval")

    async def on_approve_patch(self, request_id: str) -> None:
        """Handle approval of PATCH records."""
        pending = self._pending_patch_snapshots.pop(request_id, None)
        if not pending:
            logger.warning("[TeamSkillRail] on_approve_patch: unknown request_id=%s", request_id)
            return

        for record in pending.payload:
            await self._store.append_record(pending.skill_name, record)

        logger.info(
            "[TeamSkillRail] user approved %d patch(es) for '%s'",
            len(pending.payload),
            pending.skill_name,
        )

    async def on_reject_patch(self, request_id: str) -> None:
        pending = self._pending_patch_snapshots.pop(request_id, None)
        if pending:
            logger.info(
                "[TeamSkillRail] user rejected %d patch(es) for '%s'",
                len(pending.payload),
                pending.skill_name,
            )

    async def request_simplify(
        self,
        skill_name: str,
        user_intent: Optional[str] = None,
    ) -> Optional[dict[str, int]]:
        """Execute simplify for team skill.

        Unlike SkillEvolutionRail's request_simplify (which stages for approval),
        this directly executes simplify actions without approval.

        Returns simplify action counts, or None if no records/action found.
        """
        if not self._store.skill_exists(skill_name):
            return None

        evo_log = await self._store.load_full_evolution_log(skill_name)
        records = evo_log.entries
        if not records:
            return None

        content = await self._store.read_skill_content(skill_name)
        summary = self._store.extract_description_from_skill_md(content)

        actions = await self._scorer.simplify(
            skill_name=skill_name,
            skill_summary=summary,
            records=records,
            user_intent=user_intent,
        )
        if not actions:
            return None

        result = await self._scorer.execute_simplify_actions(self._store, skill_name, actions)
        logger.info("[TeamSkillRail] simplify executed for '%s': %s", skill_name, result)
        self._emit_progress(f"simplify completed for '{skill_name}': {result}")
        return result

    _REBUILD_PROMPT_TEMPLATE_CN = (
        "你收到了一个团队技能的重建请求。旧版本已归档，请执行以下步骤：\n\n"
        "## 已筛选的历史演进经验（score >= {min_score}）\n\n"
        "{evolution_records}\n\n"
        "## 用户意图\n\n"
        "{user_intent}\n\n"
        "## 执行要求\n\n"
        "请调用 teamskill-creator 技能：\n"
        "1. 基于以上历史经验和用户意图，生成新的 SKILL.md\n"
        "2. 重置 evolutions.json 为空列表\n\n"
        "旧版本已归档至 archive/ 目录，可直接创建新版本。"
    )

    _REBUILD_PROMPT_TEMPLATE_EN = (
        "You received a team skill rebuild request. Old version has been archived. Please follow these steps:\n\n"
        "## Filtered Historical Evolution Records (score >= {min_score})\n\n"
        "{evolution_records}\n\n"
        "## User Intent\n\n"
        "{user_intent}\n\n"
        "## Execution Requirements\n\n"
        "Please invoke the teamskill-creator skill:\n"
        "1. Generate new SKILL.md based on the historical records and user intent above\n"
        "2. Reset evolutions.json to empty list\n\n"
        "Old version has been archived to archive/ directory, you can directly create the new version."
    )

    @staticmethod
    def _format_evolution_records(records: list, language: str = "cn") -> str:
        """Format evolution records as readable markdown text."""
        if language == "cn":
            header = "经验"
            content_label = "内容"
            empty = "（无演进经验）"
        else:
            header = "Experience"
            content_label = "Content"
            empty = "(no evolution records)"

        lines: list[str] = []
        for idx, record in enumerate(records, 1):
            change = getattr(record, "change", None)
            section = getattr(change, "section", "?") if change else "?"
            content = getattr(change, "content", "") if change else ""
            source = getattr(record, "source", "unknown")
            timestamp = getattr(record, "timestamp", "")
            line_parts = [
                f"### {header} #{idx} [{timestamp}] - source: {source}",
                f"- Section: {section}",
                f"- {content_label}: {content}",
            ]
            lines.append("\n".join(line_parts))
        return "\n\n".join(lines) if lines else empty

    async def _build_rebuild_prompt(
        self,
        skill_name: str,
        user_intent: Optional[str],
        min_score: float = 0.5,
    ) -> str:
        """Build text-format followup prompt for rebuild.

        Args:
            skill_name: Name of the skill to rebuild.
            user_intent: Optional user-specified optimization direction.
            min_score: Minimum score threshold for evolution records to include.
                Default 0.5 filters out low-quality experiences.

        Note: This method is called AFTER archive operations have been performed
        by request_rebuild(). The prompt only contains filtered evolution records
        and instructions for generating new content.
        """
        records_log = await self._store.load_full_evolution_log(skill_name)

        # Filter records by score and skip_reason (same as SkillRewriter)
        filtered_records = []
        for record in records_log.entries:
            score = getattr(record, "score", 0.0)
            if score < min_score:
                continue
            change = getattr(record, "change", None)
            skip_reason = getattr(change, "skip_reason", None) if change else None
            if skip_reason:
                continue
            filtered_records.append(record)

        language = self._optimizer.language
        evolution_text = self._format_evolution_records(filtered_records, language)
        if language == "cn":
            intent = user_intent or "根据以上演进经验，对团队技能进行全面优化和重建。"
        else:
            intent = user_intent or (
                "Based on the evolution records above, perform a comprehensive rebuild "
                "of the team skill."
            )

        template = (
            self._REBUILD_PROMPT_TEMPLATE_CN if language == "cn" else self._REBUILD_PROMPT_TEMPLATE_EN
        )

        logger.info(
            "[TeamSkillRail] rebuild prompt built: skill=%s, total_records=%d, filtered_records=%d, min_score=%.2f",
            skill_name,
            len(records_log.entries),
            len(filtered_records),
            min_score,
        )

        return template.format(
            evolution_records=evolution_text,
            user_intent=intent,
            min_score=min_score,
        )

    async def request_rebuild(
        self,
        skill_name: str,
        user_intent: Optional[str] = None,
        min_score: float = 0.5,
    ) -> Optional[str]:
        """Build a rebuild prompt for team skill.

        Packages historical skill content + filtered evolution records + user intent
        into a text prompt for the caller to inject into agent loop.
        The caller (slash command handler or host) is responsible for
        delivering this prompt to the agent.

        Args:
            skill_name: Name of the skill to rebuild.
            user_intent: Optional user-specified optimization direction.
            min_score: Minimum score threshold for evolution records to include.
                Default 0.5 filters out low-quality experiences.

        Returns the prompt text on success, None if skill not found.
        """
        if not self._store.skill_exists(skill_name):
            return None

        # Step 1: Archive current SKILL.md and evolutions.json BEFORE rebuild
        try:
            body_archive = await self._store.archive_skill_body(skill_name)
            if body_archive:
                logger.info(
                    "[TeamSkillRail] archived SKILL.md -> %s for '%s'",
                    body_archive,
                    skill_name,
                )

            evo_archive = await self._store.archive_evolutions(skill_name)
            if evo_archive:
                logger.info(
                    "[TeamSkillRail] archived evolutions.json -> %s for '%s'",
                    evo_archive,
                    skill_name,
                )

            self._emit_progress(f"archived old version for '{skill_name}'")
        except Exception as exc:
            logger.warning(
                "[TeamSkillRail] archive failed for '%s': %s",
                skill_name,
                exc,
            )
            self._emit_progress(f"archive failed for '{skill_name}': {exc}")
            # Continue anyway - archive failure shouldn't block rebuild

        # Step 2: Build prompt with filtered evolution records
        followup_text = await self._build_rebuild_prompt(skill_name, user_intent, min_score)
        logger.info(
            "[TeamSkillRail] rebuild prompt generated for '%s'",
            skill_name,
        )
        self._emit_progress(f"rebuild prompt generated for '{skill_name}'")
        return followup_text

    async def request_user_evolution(
        self,
        skill_name: str,
        user_intent: str,
        *,
        auto_approve: bool = False,
    ) -> Optional[str]:
        """User-triggered evolution entry point.

        Allows user to explicitly provide improvement suggestions for a team skill,
        bypassing the passive _detect_user_request() flow.

        Args:
            skill_name: Target team skill name.
            user_intent: User's explicit improvement suggestion.
            auto_approve: If True, patch is stored directly without approval.
                          If False (default), patch is staged for user approval.

        Returns:
            request_id if patch generated and staged, None if skill not found or no patch.

        Example:
            request_id = await rail.request_user_evolution(
                "research-team",
                "增加 reviewer 角色，限制 research 时间不超过 10 分钟"
            )
        """
        if not self._store.skill_exists(skill_name):
            logger.warning(
                "[TeamSkillRail] request_user_evolution: skill '%s' not found",
                skill_name,
            )
            return None

        # Get current trajectory (from builder or use minimal placeholder)
        trajectory = self._builder.build() if self._builder else None
        if trajectory is None:
            trajectory = Trajectory(
                execution_id="user_triggered",
                session_id="user_triggered",
                source="user_triggered",
                steps=[],
            )

        # Generate patch via TeamSkillOptimizer
        record = await self._optimizer.generate_user_patch(
            trajectory,
            skill_name,
            user_intent,
        )

        if record is None:
            logger.info(
                "[TeamSkillRail] request_user_evolution: no patch generated for '%s'",
                skill_name,
            )
            return None

        # Handle storage/approval
        if auto_approve:
            await self._store.append_record(skill_name, record)
            logger.info(
                "[TeamSkillRail] request_user_evolution: '%s' patch auto-approved and stored",
                skill_name,
            )
            self._emit_progress(f"evolution patch auto-approved for '{skill_name}'")
            return record.id
        else:
            pending = PendingChange.make(skill_name, [record])
            pending.change_id = f"team_skill_evolve_{uuid.uuid4().hex[:8]}"
            self._pending_patch_snapshots[pending.change_id] = pending
            self._emit_patch_approval_event(skill_name, pending)
            logger.info(
                "[TeamSkillRail] request_user_evolution: patch staged for approval, change_id=%s",
                pending.change_id,
            )
            self._emit_progress(f"patch for '{skill_name}' ready, awaiting approval")
            return pending.change_id

    # ===== Shared: drain approval events =====

    def _collect_pending_approval_events(self) -> list[OutputSchema]:
        """Return and clear the team skill event buffer."""
        events = list(self._pending_approval_events)
        self._pending_approval_events.clear()
        return events

    # ===== Private helpers =====

    def _detect_used_team_skill(self, trajectory: Trajectory) -> Optional[str]:
        """Scan trajectory for SKILL.md read traces to identify which team skill was used.

        Only considers skills whose SKILL.md frontmatter contains
        ``kind: team-skill`` so that regular skills in the shared
        directory are not mistakenly matched.
        """
        all_skill_names = set(self._store.list_skill_names())
        if not all_skill_names:
            logger.info("[TeamSkillRail] no existing team skills on disk")
            return None

        # Filter to only team-skill kind
        known_skills = {name for name in all_skill_names if self._is_team_skill(name)}
        if not known_skills:
            logger.info("[TeamSkillRail] no team-skill kind skills found among %d total skills", len(all_skill_names))
            return None

        skill_tool_payloads: list[Any] = []
        texts: list[str] = []
        for step in trajectory.steps:
            if step.kind != "tool" or not step.detail:
                continue
            tool_name = getattr(step.detail, "tool_name", "")
            if tool_name == "skill_tool":
                skill_tool_payloads.append(getattr(step.detail, "call_args", None))
            texts.append(str(getattr(step.detail, "call_args", "")))
            texts.append(str(getattr(step.detail, "call_result", "")))

        best = infer_skill_from_texts(
            known_skills,
            skill_tool_payloads=skill_tool_payloads,
            texts=texts,
        )
        if best:
            logger.info("[TeamSkillRail] detected team skill '%s' from trajectory", best)
            return best

        logger.info("[TeamSkillRail] no skill SKILL.md reads found in trajectory")
        return None

    async def _detect_user_request(
        self,
        messages: list[dict],
        team_skill_content: str,
    ) -> Optional[UserIntent]:
        """Detect whether user messages contain team improvement intent."""
        if not messages:
            return None

        user_msgs = [m.get("content", "") for m in messages if m.get("role") == "user"][-10:]
        if not user_msgs:
            return None

        user_text = "\n".join(user_msgs)
        skill_summary = team_skill_content[:1000] if team_skill_content else ""

        prompt_template = (
            self._USER_REQUEST_PROMPT_CN if self._optimizer.language == "cn" else self._USER_REQUEST_PROMPT_EN
        )
        prompt = prompt_template.format(
            team_skill_description=skill_summary,
            roles="",
            user_messages=user_text[:2000],
        )

        try:
            response = await self._optimizer.llm.invoke(
                model=self._optimizer.model,
                messages=[{"role": "user", "content": prompt}],
                timeout=30,
            )
            raw = response.content if hasattr(response, "content") else str(response)
        except Exception as exc:
            logger.warning("[TeamSkillRail] _detect_user_request LLM call failed: %s", exc)
            return None

        parsed = TeamSkillOptimizer.parse_json(raw)
        if parsed and parsed.get("is_improvement"):
            return UserIntent(
                is_improvement=True,
                intent=parsed.get("intent", ""),
            )
        return None

    async def _detect_trajectory_issues(
        self,
        trajectory: Trajectory,
        team_skill_content: str,
    ) -> list[TrajectoryIssue]:
        """Detect trajectory issues via LLM analysis."""
        trajectory_summary = TeamSkillOptimizer.build_trajectory_summary(trajectory)

        prompt_template = (
            self._TRAJECTORY_ISSUE_PROMPT_CN
            if self._optimizer.language == "cn"
            else self._TRAJECTORY_ISSUE_PROMPT_EN
        )
        prompt = prompt_template.format(
            skill_content=team_skill_content[:10000],
            trajectory_summary=trajectory_summary,
        )

        try:
            response = await self._optimizer.llm.invoke(
                model=self._optimizer.model,
                messages=[{"role": "user", "content": prompt}],
                timeout=60,
            )
            raw = response.content if hasattr(response, "content") else str(response)
        except Exception as exc:
            logger.warning("[TeamSkillRail] _detect_trajectory_issues LLM call failed: %s", exc)
            return []

        parsed = TeamSkillOptimizer.parse_json(raw)
        if not parsed or not isinstance(parsed, list):
            return []

        issues = []
        for item in parsed:
            if not isinstance(item, dict):
                continue
            severity = item.get("severity", "medium")
            if severity not in ("low", "medium", "high"):
                severity = "medium"
            issues.append(
                TrajectoryIssue(
                    issue_type=item.get("issue_type", "unknown"),
                    description=item.get("description", ""),
                    affected_role=item.get("affected_role", ""),
                    severity=severity,
                )
            )

        return [i for i in issues if i.severity in ("medium", "high")]

    async def _collect_messages(self, ctx: AgentCallbackContext) -> list[dict]:
        """Collect conversation messages from context."""
        messages: list[Any] = []
        if ctx.context is not None:
            try:
                messages = list(ctx.context.get_messages())
            except Exception as exc:
                logger.debug("[TeamSkillRail] failed to get messages from context: %s", exc)
        if not messages and ctx.session is not None:
            agent_obj = ctx.agent
            inner_agent = getattr(agent_obj, "_react_agent", None)
            if inner_agent is not None and hasattr(inner_agent, "context_engine"):
                try:
                    context = await inner_agent.context_engine.create_context(session=ctx.session)
                    messages = list(context.get_messages())
                except Exception as exc:
                    logger.debug("[TeamSkillRail] failed to get messages from context_engine: %s", exc)
        result = []
        for msg in messages:
            if isinstance(msg, dict):
                result.append(msg)
            else:
                result.append(
                    {
                        "role": getattr(msg, "role", "unknown"),
                        "content": str(getattr(msg, "content", "")),
                    }
                )
        return result

    async def _handle_patch_record(
        self,
        record: Any,  # EvolutionRecord
        skill_name: str,
    ) -> None:
        """Handle a generated patch record."""
        logger.info(
            "[TeamSkillRail] PATCH generated: section='%s', content_len=%d",
            record.change.section,
            len(record.change.content),
        )

        if self._auto_save:
            await self._store.append_record(skill_name, record)
            logger.info("[TeamSkillRail] PATCH auto-saved for '%s'", skill_name)
            self._emit_progress(f"patch auto-saved to '{skill_name}'")
        else:
            pending = PendingChange.make(skill_name, [record])
            pending.change_id = f"team_skill_evolve_{uuid.uuid4().hex[:8]}"
            self._pending_patch_snapshots[pending.change_id] = pending
            self._emit_patch_approval_event(skill_name, pending)
            logger.info(
                "[TeamSkillRail] PATCH buffered for approval: change_id=%s",
                pending.change_id,
            )
            self._emit_progress(f"patch for '{skill_name}' ready, awaiting approval")

    def _is_team_skill(self, name: str) -> bool:
        """Check whether a skill's SKILL.md contains ``kind: team-skill``."""
        skill_dir = self._store.resolve_skill_dir(name)
        if skill_dir is None:
            return False
        skill_md = skill_dir / "SKILL.md"
        if not skill_md.exists():
            return False
        try:
            text = skill_md.read_text(encoding="utf-8")
            frontmatter = parse_frontmatter(text) or {}
            return frontmatter.get("kind") == "team-skill"
        except OSError:
            return False

    def _dump_trajectory_debug(self, trajectory: Trajectory) -> None:
        """Dump trajectory to a JSON file for debugging."""
        try:
            debug_dir = self._store.base_dirs[0].parent / "_debug"
            debug_dir.mkdir(parents=True, exist_ok=True)
            ts = time.strftime("%Y%m%d_%H%M%S")
            path = debug_dir / f"trajectory_{ts}_{trajectory.execution_id[:8]}.json"

            steps_data = []
            for step in trajectory.steps:
                entry: dict[str, Any] = {"kind": step.kind}
                if step.detail:
                    if step.kind == "tool":
                        entry["tool_name"] = getattr(step.detail, "tool_name", "")
                        entry["call_args"] = str(getattr(step.detail, "call_args", ""))[:500]
                        entry["call_result"] = str(getattr(step.detail, "call_result", ""))[:500]
                    elif step.kind == "llm":
                        resp = getattr(step.detail, "response", None)
                        entry["response_preview"] = str(resp)[:300] if resp else ""
                if step.meta:
                    entry["meta"] = step.meta
                steps_data.append(entry)

            dump = {
                "execution_id": trajectory.execution_id,
                "session_id": trajectory.session_id,
                "source": trajectory.source,
                "step_count": len(trajectory.steps),
                "steps": steps_data,
            }
            path.write_text(json.dumps(dump, ensure_ascii=False, indent=2), encoding="utf-8")
            logger.info("[TeamSkillRail] trajectory dumped to %s", path)
        except Exception as exc:
            logger.warning("[TeamSkillRail] trajectory dump failed: %s", exc)

    def _emit_patch_approval_event(
        self,
        skill_name: str,
        pending: PendingChange,
    ) -> None:
        """Buffer a PATCH approval event."""
        questions = []
        for record in pending.payload:
            preview = record.change.content[:1000]
            questions.append(
                {
                    "question": (
                        f"**Team Skill '{skill_name}' evolution:**\n\n"
                        f"- **Section**: {record.change.section}\n\n"
                        f"{preview}"
                    ),
                    "header": "Team Skill Patch Approval",
                    "options": [
                        {"label": "Accept", "description": "Keep this evolution"},
                        {"label": "Reject", "description": "Discard this evolution"},
                    ],
                    "multi_select": False,
                }
            )

        event = OutputSchema(
            type="chat.ask_user_question",
            index=0,
            payload={
                "request_id": pending.change_id,
                "_evolution_meta": {"skill_name": skill_name, "request_id": pending.change_id},
                "questions": questions,
            },
        )
        self._pending_approval_events.append(event)

        sections = ", ".join(r.change.section for r in pending.payload)
        self._emit_progress(
            f"TEAM SKILL PATCH PROPOSED: '{skill_name}'\n"
            f"  sections: {sections}\n"
            f"  patch_count: {len(pending.payload)}\n"
            f"  change_id: {pending.change_id}\n"
            f"  ACTION: an approval dialog should pop up; if not visible, "
            f"check approval panel or rerun task"
        )


__all__ = ["TeamSkillRail", "TeamSignalType", "UserIntent", "TrajectoryIssue"]
