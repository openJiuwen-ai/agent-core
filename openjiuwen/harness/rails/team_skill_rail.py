# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""TeamSkillRail: online auto-evolution for multi-agent team skills.

Counterpart of SkillEvolutionRail for team skills:
- CREATE path: propose a new team skill from trajectory → user approval → persist
- PATCH path: generate evolution records → user approval (default) → append

Inherits EvolutionRail to gain automatic trajectory collection.
"""

from __future__ import annotations

import json
import re
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

import logging as _logging

from openjiuwen.agent_evolving.checkpointing import EvolutionStore
from openjiuwen.agent_evolving.checkpointing.types import (
    PendingChange,
    PendingTeamSkillCreation,
)
from openjiuwen.agent_evolving.optimizer.team_skill_optimizer import TeamSkillOptimizer
from openjiuwen.agent_evolving.trajectory import Trajectory, TrajectoryStore
from openjiuwen.core.session.stream import OutputSchema
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext, ToolCallInputs
from openjiuwen.harness.rails.evolution_rail import EvolutionRail

logger = _logging.getLogger("jiuwenclaw.agentserver.team.team_skill_rail")


class TeamSkillRail(EvolutionRail):
    """Team skill evolution rail — counterpart of SkillEvolutionRail.

    SkillEvolutionRail handles 1D skill CREATE / PATCH;
    TeamSkillRail handles team skill CREATE / PATCH.
    Both can coexist on the same agent via agent_customizer.
    """

    priority = 80
    _SKILL_MD_RE = re.compile(r"[/\\]([^/\\]+)[/\\]SKILL\.md", re.IGNORECASE)

    def __init__(
        self,
        skills_dir: Union[str, List[str]],
        *,
        llm: Any,
        model: str,
        language: str = "cn",
        trajectory_store: Optional[TrajectoryStore] = None,
        min_team_members_for_create: int = 2,
        auto_save: bool = False,
    ) -> None:
        super().__init__(
            trajectory_store=trajectory_store,
            accumulate_trajectory=True,
            trigger_evolution_after_invoke=False,
        )
        self._store = EvolutionStore(skills_dir)
        debug_dir = str(self._store.base_dirs[0].parent / "_debug")
        self._optimizer = TeamSkillOptimizer(llm, model, language, debug_dir=debug_dir)
        self._min_members = min_team_members_for_create
        self._auto_save = auto_save

        self._pending_approval_events: list[OutputSchema] = []
        self._pending_skill_proposals: Dict[str, PendingTeamSkillCreation] = {}
        self._pending_patch_snapshots: Dict[str, PendingChange] = {}
        self._evolution_triggered: bool = False

        logger.info(
            "[TeamSkillRail] initialized: skills_dir=%s, model=%s, auto_save=%s, min_members=%d",
            skills_dir, model, auto_save, min_team_members_for_create,
        )

    @property
    def store(self) -> EvolutionStore:
        return self._store

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

        This is the canonical entry point for evolution triggering.
        It can be called from two paths:

        1. **Internal** — ``_on_after_tool_call`` detects ``view_task``
           showing all tasks in terminal state and delegates here.
        2. **External** — coordination layer (e.g. dispatcher, cleanup
           flow) detects team completion and calls this directly,
           bypassing the ``view_task`` interception.

        The method is idempotent: once triggered, subsequent calls
        are no-ops within the same session.

        Args:
            ctx: Optional callback context.  May be ``None`` when
                called from outside the rail callback system.

        Returns:
            ``True`` if evolution was triggered, ``False`` if skipped
            (already triggered or no trajectory available).
        """
        if self._evolution_triggered:
            return False

        self._evolution_triggered = True
        self._emit_progress("all tasks completed, starting evolution analysis...")

        trajectory = self._build_trajectory()
        if trajectory is None:
            logger.warning(
                "[TeamSkillRail] notify_team_completed: no trajectory available "
                "(before_invoke may not have fired)"
            )
            return False

        self._save_trajectory(trajectory)
        await self.run_evolution(trajectory, ctx)

        step_count = len(trajectory.steps)
        tool_names = [
            getattr(s.detail, "tool_name", "?")
            for s in trajectory.steps if s.kind == "tool" and s.detail
        ]
        logger.info(
            "[TeamSkillRail] trajectory built: %d steps, tool_names=%s",
            step_count, tool_names,
        )

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
        ctx: AgentCallbackContext,
    ) -> None:
        """Triggered when view_task shows all member tasks completed.

        Decision tree:
        1. Detect whether trajectory used an existing team skill
           -> yes: PATCH path
           -> no:  CREATE path
        2. CREATE always requires approval
        3. PATCH defaults to approval (auto_save=False), opt-in auto-save
        """
        t0 = time.time()
        try:
            used_skill = self._detect_used_team_skill(trajectory)

            if used_skill:
                logger.info("[TeamSkillRail] detected existing skill '%s' -> PATCH path", used_skill)
                self._emit_progress(f"detected existing skill '{used_skill}', generating patch...")
                await self._handle_patch(trajectory, ctx, used_skill)
            else:
                logger.info("[TeamSkillRail] no existing skill detected -> CREATE path")
                self._emit_progress("no existing skill found, proposing new Team Skill...")
                await self._handle_create(trajectory, ctx)

            elapsed = time.time() - t0
            logger.info("[TeamSkillRail] run_evolution completed in %.1fs", elapsed)
        except Exception as exc:
            logger.warning("[TeamSkillRail] run_evolution failed: %s", exc, exc_info=True)
            self._emit_progress(f"evolution analysis failed: {exc}")

    # ===== CREATE path =====

    async def _handle_create(
        self,
        trajectory: Trajectory,
        ctx: AgentCallbackContext,
    ) -> None:
        if not self._meets_create_threshold(trajectory):
            spawn_count = 0
            all_tool_names: list[str] = []
            for step in trajectory.steps:
                if step.kind == "tool" and step.detail:
                    tool_name = getattr(step.detail, "tool_name", "?")
                    all_tool_names.append(tool_name)
                    if "spawn_member" in tool_name:
                        spawn_count += 1
            logger.info(
                "[TeamSkillRail] CREATE skipped: spawn_count=%d < threshold=%d, "
                "total_steps=%d, all_tools=%s",
                spawn_count, self._min_members, len(trajectory.steps), all_tool_names,
            )
            self._emit_progress(
                f"member count ({spawn_count}) below threshold ({self._min_members}), "
                f"skipping CREATE (total {len(trajectory.steps)} steps, "
                f"{len(all_tool_names)} tool calls)"
            )
            return

        existing = [n for n in self._store.list_skill_names() if self._is_team_skill(n)]
        logger.info("[TeamSkillRail] CREATE: existing team skills=%s, calling LLM to propose...", existing)
        self._emit_progress("analyzing trajectory with LLM to extract collaboration pattern...")

        t0 = time.time()
        proposal = await self._optimizer.propose_team_skill(
            trajectory=trajectory,
            existing_skills=existing,
        )
        elapsed = time.time() - t0

        if proposal:
            logger.info(
                "[TeamSkillRail] CREATE proposal ready: name='%s', %d extra files (%.1fs)",
                proposal.name, len(proposal.extra_files), elapsed,
            )
            self._emit_progress(
                f"Team Skill '{proposal.name}' proposed, awaiting your approval"
            )
            await self._emit_team_skill_approval(ctx, proposal)
        else:
            logger.info("[TeamSkillRail] CREATE: LLM decided not to create (%.1fs)", elapsed)
            self._emit_progress("LLM analysis: current pattern not worth extracting as Team Skill")

    async def on_approve_team_skill(self, request_id: str) -> Optional[str]:
        """Handle approval of team skill creation. Returns skill name or None."""
        pending = self._pending_skill_proposals.pop(request_id, None)
        if not pending:
            logger.warning("[TeamSkillRail] on_approve_team_skill: unknown request_id=%s", request_id)
            self._emit_progress(f"approval received but proposal not found: {request_id}")
            return None

        try:
            result = await self._store.create_skill(
                name=pending.name,
                description=pending.description,
                body=pending.body,
                frontmatter=pending.frontmatter or None,
            )
            if result is None:
                logger.error("[TeamSkillRail] create_skill returned None for '%s'", pending.name)
                self._emit_progress(f"create_skill returned None for '{pending.name}'")
                return None

            await self._persist_extra_files(pending, result)
            logger.info(
                "[TeamSkillRail] Team Skill '%s' created successfully at %s",
                pending.name, result,
            )
            self._emit_progress(
                f"Team Skill '{pending.name}' SAVED to {result}"
            )
            return pending.name
        except Exception as exc:
            logger.error("[TeamSkillRail] create failed for %s: %s", pending.name, exc, exc_info=True)
            self._emit_progress(f"create failed for '{pending.name}': {exc}")
            return None

    async def on_reject_team_skill(self, request_id: str) -> None:
        pending = self._pending_skill_proposals.pop(request_id, None)
        if pending:
            logger.info("[TeamSkillRail] user rejected Team Skill creation: '%s'", pending.name)
            self._emit_progress(f"user rejected Team Skill: '{pending.name}'")

    # ===== PATCH path =====

    async def _handle_patch(
        self,
        trajectory: Trajectory,
        ctx: AgentCallbackContext,
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
            patch.change.section, len(patch.change.content), elapsed,
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
                pending.change_id, skill_name,
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
            len(pending.payload), pending.skill_name,
        )

    async def on_reject_patch(self, request_id: str) -> None:
        pending = self._pending_patch_snapshots.pop(request_id, None)
        if pending:
            logger.info(
                "[TeamSkillRail] user rejected %d patch(es) for '%s'",
                len(pending.payload), pending.skill_name,
            )

    # ===== Shared: drain approval events =====

    def drain_pending_approval_events(self) -> list[OutputSchema]:
        """Return and clear buffered approval events for host delivery."""
        events = list(self._pending_approval_events)
        if events:
            logger.info("[TeamSkillRail] draining %d pending events", len(events))
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
        known_skills = {
            name for name in all_skill_names
            if self._is_team_skill(name)
        }
        if not known_skills:
            logger.info("[TeamSkillRail] no team-skill kind skills found among %d total skills", len(all_skill_names))
            return None

        hits: dict[str, int] = {}
        for step in trajectory.steps:
            if step.kind != "tool" or not step.detail:
                continue
            tool_name = getattr(step.detail, "tool_name", "")
            if "read" not in tool_name.lower():
                continue
            args_str = str(getattr(step.detail, "call_args", ""))
            for matched in self._SKILL_MD_RE.finditer(args_str):
                name = matched.group(1)
                if name in known_skills:
                    hits[name] = hits.get(name, 0) + 1

        if hits:
            best = max(hits, key=hits.get)  # type: ignore[arg-type]
            logger.info("[TeamSkillRail] skill detection hits: %s -> best='%s'", hits, best)
            return best

        logger.info("[TeamSkillRail] no skill SKILL.md reads found in trajectory")
        return None

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
            return "kind: team-skill" in text
        except OSError:
            return False

    def _meets_create_threshold(self, trajectory: Trajectory) -> bool:
        """Check if trajectory has enough spawn_member calls to warrant CREATE."""
        spawn_count = 0
        tool_name_counts: dict[str, int] = {}
        for step in trajectory.steps:
            if step.kind != "tool" or not step.detail:
                continue
            name = getattr(step.detail, "tool_name", "")
            tool_name_counts[name] = tool_name_counts.get(name, 0) + 1
            if "spawn_member" in name:
                spawn_count += 1

        logger.info(
            "[TeamSkillRail] threshold check: spawn_count=%d, min=%d, "
            "total_steps=%d, tool_distribution=%s",
            spawn_count, self._min_members, len(trajectory.steps), tool_name_counts,
        )
        return spawn_count >= self._min_members

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

    async def _emit_team_skill_approval(
        self,
        ctx: AgentCallbackContext,
        proposal: PendingTeamSkillCreation,
    ) -> None:
        """Buffer a CREATE approval event."""
        self._pending_skill_proposals[proposal.proposal_id] = proposal

        extra_file_list = "\n".join(f"- `{k}`" for k in proposal.extra_files) if proposal.extra_files else "(none)"
        questions = [
            {
                "question": (
                    f"**Team Skill proposal: '{proposal.name}'**\n\n"
                    f"{proposal.description}\n\n"
                    f"**Reason:** {proposal.reason}\n\n"
                    f"**Files:**\n{extra_file_list}\n\n"
                    "Create this Team Skill?"
                ),
                "header": "Team Skill Creation Approval",
                "options": [
                    {"label": "Create", "description": "Create this Team Skill"},
                    {"label": "Skip", "description": "Discard this proposal"},
                ],
                "multi_select": False,
            }
        ]

        event = OutputSchema(
            type="chat.ask_user_question",
            index=0,
            payload={
                "request_id": proposal.proposal_id,
                "_team_skill_data": {
                    "name": proposal.name,
                    "description": proposal.description,
                },
                "questions": questions,
            },
        )
        self._pending_approval_events.append(event)
        logger.info(
            "[TeamSkillRail] approval event buffered: proposal_id=%s, name='%s'",
            proposal.proposal_id, proposal.name,
        )

        # Mirror as a plain progress message so it always shows in TUI even if
        # the front-end ignores chat.ask_user_question events. Includes a brief
        # description and how to approve.
        files_inline = ", ".join(proposal.extra_files.keys()) if proposal.extra_files else "(none)"
        self._emit_progress(
            f"NEW TEAM SKILL PROPOSED: '{proposal.name}'\n"
            f"  description: {proposal.description}\n"
            f"  reason: {proposal.reason}\n"
            f"  files: {files_inline}\n"
            f"  proposal_id: {proposal.proposal_id}\n"
            f"  ACTION: an approval dialog should pop up; if not visible, "
            f"check approval panel or rerun task"
        )

    def _emit_patch_approval_event(
        self,
        skill_name: str,
        pending: PendingChange,
    ) -> None:
        """Buffer a PATCH approval event."""
        questions = []
        for record in pending.payload:
            preview = record.change.content[:1000]
            questions.append({
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
            })

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

    async def _persist_extra_files(
        self,
        pending: PendingTeamSkillCreation,
        skill_dir: Path,
    ) -> None:
        """Write extra files (roles/*.md, workflow.md, bind.md) into skill directory."""
        for relative_path, content in pending.extra_files.items():
            target = skill_dir / relative_path
            target.parent.mkdir(parents=True, exist_ok=True)
            try:
                target.write_text(content, encoding="utf-8")
                logger.info("[TeamSkillRail] wrote extra file: %s", target)
            except Exception as exc:
                logger.error("[TeamSkillRail] failed to write %s: %s", target, exc)


__all__ = ["TeamSkillRail"]
