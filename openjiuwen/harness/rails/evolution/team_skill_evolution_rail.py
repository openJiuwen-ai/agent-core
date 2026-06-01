# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""TeamSkillEvolutionRail: online auto-evolution for multi-agent team skills.

Counterpart of SkillEvolutionRail for team skills:
generate evolution records → user approval (default) → append

Inherits EvolutionRail to gain automatic trajectory collection.
"""

from __future__ import annotations

import json
import posixpath
import re
import time
from pathlib import Path
from typing import Any, Optional, Union

from openjiuwen.agent_evolving.checkpointing import EvolutionStore
from openjiuwen.agent_evolving.experience import (
    ExperienceTracker,
    OnlineEvolutionOrchestrator,
)
from openjiuwen.agent_evolving.experience.scorer import (
    EVALUATE_LLM_POLICY,
    SIMPLIFY_LLM_POLICY,
    ExperienceScorer,
)
from openjiuwen.agent_evolving.experience.skill_experience_manager import ExperienceManager
from openjiuwen.agent_evolving.experience.types import (
    ONLINE_EVOLUTION_OUTCOME_STATUSES,
    ExperienceApprovalRequest,
    ExperienceProposal,
    OnlineEvolutionResult,
    PendingChange,
    request_for_online_evolution_result,
)
from openjiuwen.agent_evolving.optimizer.llm_resilience import LLMInvokePolicy
from openjiuwen.agent_evolving.optimizer.skill_call.team_skill_experience_optimizer import (
    TeamSkillExperienceOptimizer,
)
from openjiuwen.agent_evolving.signal import (
    EvolutionSignal,
    TeamSignalDetector,
    TeamSignalType,
    TrajectoryIssue,
    UserIntent,
    get_team_trajectory_issues,
    make_signal_fingerprint,
    make_team_user_intent_signal,
)
from openjiuwen.agent_evolving.trajectory import (
    Trajectory,
    TrajectorySink,
    TrajectorySource,
    TrajectoryStore,
)
from openjiuwen.agent_evolving.updater import SingleDimUpdater
from openjiuwen.agent_evolving.utils import infer_skill_from_texts, parse_top_level_frontmatter
from openjiuwen.core.common.logging import logger
from openjiuwen.core.foundation.llm.model import Model
from openjiuwen.core.operator.skill_call import SkillExperienceOperator
from openjiuwen.core.session.stream import OutputSchema
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext, ToolCallInputs
from openjiuwen.harness.rails.evolution.approval_events import (
    attach_evolution_meta,
    build_evolution_progress_event,
    build_simplify_approval_event,
    build_team_skill_approval_event_from_records,
)
from openjiuwen.harness.rails.evolution.approval_runtime import EvolutionApprovalRuntime
from openjiuwen.harness.rails.evolution.contracts import (
    EvolutionRequestResult,
    EvolutionSnapshot,
    SimplifyRequestResult,
)
from openjiuwen.harness.rails.evolution.evolution_rail import EvolutionRail, EvolutionTriggerPoint

_TEAM_USER_REQUEST_LLM_POLICY = LLMInvokePolicy(
    attempt_timeout_secs=60,
    total_budget_secs=120,
    max_attempts=2,
)
_TEAM_TRAJECTORY_ISSUE_LLM_POLICY = LLMInvokePolicy(
    attempt_timeout_secs=150,
    total_budget_secs=300,
    max_attempts=2,
)
_TEAM_RECORD_LLM_POLICY = LLMInvokePolicy(
    attempt_timeout_secs=150,
    total_budget_secs=300,
    max_attempts=2,
)
_DEFAULT_TEAM_EVOLUTION_TOTAL_TIMEOUT_SECS = 720.0
_TEAM_TASK_NON_TERMINAL_STATES = ("pending", "claimed", "in_progress", "blocked")
_TEAM_SKILL_KINDS = {"team-skill", "swarm-skill"}


def is_completed_team_task_view(result: Any) -> bool:
    """Return True when a ``view_task`` result shows completed work only."""
    text = str(result).lower()
    if "completed" not in text:
        return False
    return not any(state in text for state in _TEAM_TASK_NON_TERMINAL_STATES)


def infer_team_skill_from_trajectory(
    trajectory: Trajectory,
    known_team_skills: set[str],
) -> Optional[str]:
    """Attribute a trajectory to a known team skill via SKILL.md read traces.

    This is an attribution heuristic for passive evolution routing, not a
    guarantee that the skill was semantically responsible for the run.
    """
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

    return infer_skill_from_texts(
        known_team_skills,
        skill_tool_payloads=skill_tool_payloads,
        texts=texts,
    )


class TeamSkillEvolutionRail(EvolutionRail):
    """Team skill evolution rail — counterpart of SkillEvolutionRail.

    SkillEvolutionRail handles 1D skill experience records;
    TeamSkillRail handles team skill experience records.
    New team skill creation is handled by TeamSkillCreateRail.
    Both can coexist on the same agent via agent_customizer.
    """

    priority = 80
    _DEFAULT_MEMBER_ROLE = "leader"
    _SKILL_MD_RE = re.compile(r"[/\\]([^/\\]+)[/\\]SKILL\.md", re.IGNORECASE)
    _EXPERIENCE_RECORD_HEADING_RE = re.compile(r"#+\s*\[([A-Za-z0-9_-]+)\]")

    def __init__(
        self,
        skills_dir: Union[str, list[str]],
        *,
        llm: Model,
        model: str,
        language: str = "cn",
        trajectory_store: Optional[TrajectoryStore] = None,
        team_trajectory_store: Optional[TrajectoryStore] = None,
        trajectory_source: Optional[TrajectorySource] = None,
        trajectory_sink: Optional[TrajectorySink] = None,
        member_role: Optional[str] = None,
        auto_scan: bool = True,
        auto_save: bool = False,
        async_evolution: bool = True,
        max_concurrent_evolution: int = 1,
        team_id: Optional[str] = None,
        trajectories_dir: Optional[Path] = None,
        user_request_llm_policy: LLMInvokePolicy = _TEAM_USER_REQUEST_LLM_POLICY,
        trajectory_issue_llm_policy: LLMInvokePolicy = _TEAM_TRAJECTORY_ISSUE_LLM_POLICY,
        record_llm_policy: LLMInvokePolicy = _TEAM_RECORD_LLM_POLICY,
        evaluate_llm_policy: LLMInvokePolicy = EVALUATE_LLM_POLICY,
        simplify_llm_policy: LLMInvokePolicy = SIMPLIFY_LLM_POLICY,
        eval_interval: int = 5,
        evolution_total_timeout_secs: float = _DEFAULT_TEAM_EVOLUTION_TOTAL_TIMEOUT_SECS,
        disabled_skills: Optional[Union[str, list[str]]] = None,
    ) -> None:
        if eval_interval < 1:
            raise ValueError("eval_interval must be >= 1")

        super().__init__(
            trajectory_store=trajectory_store,
            team_trajectory_store=team_trajectory_store,
            evolution_trigger=EvolutionTriggerPoint.AFTER_INVOKE,
            async_evolution=async_evolution,
            max_concurrent_evolution=max_concurrent_evolution,
            disabled_skills=disabled_skills,
        )
        self._store = EvolutionStore(skills_dir)
        debug_dir = str(self._store.base_dirs[0].parent / "_debug")
        self._generator = TeamSkillExperienceOptimizer(
            llm,
            model,
            language,
            debug_dir=debug_dir,
            record_llm_policy=record_llm_policy,
            evolution_store=self._store,
        )
        self._scorer = ExperienceScorer(
            llm,
            model,
            language,
            evaluate_llm_policy=evaluate_llm_policy,
            simplify_llm_policy=simplify_llm_policy,
        )
        self._auto_scan = auto_scan
        self._auto_save = auto_save
        self._user_request_llm_policy = user_request_llm_policy
        self._trajectory_issue_llm_policy = trajectory_issue_llm_policy
        self._evolution_total_timeout_secs = evolution_total_timeout_secs
        self._max_concurrent_evolution = max_concurrent_evolution
        self._eval_interval = eval_interval

        self._pending_approval_snapshots: dict[str, PendingChange] = {}
        self._pending_governance: dict[str, dict[str, Any]] = {}
        self._experience_skill_ops: dict[str, SkillExperienceOperator] = {}
        self._passive_evolution_pending = False
        self._host_completion_pending_session_id: Optional[str] = None
        self._team_id = team_id
        self._trajectory_source = trajectory_source
        if trajectory_sink is not None:
            self.set_trajectory_sink(
                trajectory_sink,
                team_id=team_id,
                member_role=member_role,
            )
        self._trajectories_dir = trajectories_dir
        self._manager = ExperienceManager(
            store=self._store,
            scorer=self._scorer,
            kind="team-skill",
            language=language,
            skill_ops=self._experience_skill_ops,
            pending_approval_snapshots=self._pending_approval_snapshots,
            pending_governance=self._pending_governance,
        )
        self._approval_runtime = EvolutionApprovalRuntime(
            manager=self._manager,
            pending_approval_snapshots=self._pending_approval_snapshots,
        )
        self._online_updater = SingleDimUpdater(self._generator)
        self._online_orchestrator = OnlineEvolutionOrchestrator(
            store=self._store,
            updater=self._online_updater,
            manager=self._manager,
            skill_ops=self._experience_skill_ops,
            request_id_prefix="team_skill_evolve",
            stage_source="team_skill_experience_updater",
        )
        self._team_signal_detector = TeamSignalDetector(
            llm=llm,
            model=model,
            language=language,
            trajectory_issue_llm_policy=trajectory_issue_llm_policy,
            user_intent_llm_policy=user_request_llm_policy,
        )
        self._experience_tracker = ExperienceTracker(
            store=self._store,
            scorer=self._scorer,
            eval_interval=self._eval_interval,
        )

        logger.info(
            "[TeamSkillEvolutionRail] initialized: skills_dir=%s, model=%s, auto_save=%s, team_id=%s",
            skills_dir,
            model,
            auto_save,
            team_id,
        )

    def set_trajectory_source(self, source: Optional[TrajectorySource]) -> None:
        """Bind this rail to a runtime team trajectory source."""
        self._trajectory_source = source

    @property
    def store(self) -> EvolutionStore:
        return self._store

    @property
    def scorer(self) -> ExperienceScorer:
        """Get the experience scorer."""
        return self._scorer

    @property
    def generator(self) -> TeamSkillExperienceOptimizer:
        """Get the team skill evolution generator."""
        return self._generator

    @property
    def user_request_llm_policy(self) -> LLMInvokePolicy:
        """Get the configured user-intent detection policy."""
        return self._user_request_llm_policy

    @property
    def trajectory_issue_llm_policy(self) -> LLMInvokePolicy:
        """Get the configured trajectory issue detection policy."""
        return self._trajectory_issue_llm_policy

    @property
    def team_signal_detector(self) -> TeamSignalDetector:
        """Get the detector that turns passive team trajectory evidence into signals."""
        detector = getattr(self, "_team_signal_detector", None)
        if detector is None:
            generator = self._generator
            detector = TeamSignalDetector(
                llm=generator.llm,
                model=generator.model,
                language=getattr(generator, "language", getattr(generator, "_language", "cn")),
                trajectory_issue_llm_policy=getattr(
                    self,
                    "_trajectory_issue_llm_policy",
                    _TEAM_TRAJECTORY_ISSUE_LLM_POLICY,
                ),
                user_intent_llm_policy=getattr(
                    self,
                    "_user_request_llm_policy",
                    _TEAM_USER_REQUEST_LLM_POLICY,
                ),
            )
            self._team_signal_detector = detector
        return detector

    @property
    def record_llm_policy(self) -> LLMInvokePolicy:
        """Get the configured experience record generation policy."""
        return self._generator.record_llm_policy

    @property
    def evaluate_llm_policy(self) -> LLMInvokePolicy:
        """Get the configured experience evaluation policy."""
        return self._scorer.evaluate_llm_policy

    @property
    def simplify_llm_policy(self) -> LLMInvokePolicy:
        """Get the configured experience maintenance policy."""
        return self._scorer.simplify_llm_policy

    @property
    def evolution_total_timeout_secs(self) -> float:
        """Get the configured background evolution timeout budget."""
        return self._evolution_total_timeout_secs

    @property
    def evolution_config(self) -> dict[str, LLMInvokePolicy | float | int]:
        """Get the effective evolution configuration."""
        return {
            "user_request_llm_policy": self.user_request_llm_policy,
            "trajectory_issue_llm_policy": self.trajectory_issue_llm_policy,
            "record_llm_policy": self.record_llm_policy,
            "evaluate_llm_policy": self.evaluate_llm_policy,
            "simplify_llm_policy": self.simplify_llm_policy,
            "eval_interval": self._eval_interval,
            "evolution_total_timeout_secs": self.evolution_total_timeout_secs,
            "max_concurrent_evolution": self._max_concurrent_evolution,
        }

    @property
    def auto_scan(self) -> bool:
        """Whether passive team-skill evolution scanning is enabled."""
        return self._auto_scan

    @auto_scan.setter
    def auto_scan(self, value: bool) -> None:
        self._auto_scan = value

    @property
    def auto_save(self) -> bool:
        """Whether generated team-skill records are auto-approved."""
        return self._auto_save

    @auto_save.setter
    def auto_save(self, value: bool) -> None:
        self._auto_save = value

    @property
    def approval_runtime(self) -> EvolutionApprovalRuntime:
        """Approval lifecycle helper for team skill evolution."""
        runtime = getattr(self, "_approval_runtime", None)
        if (
            runtime is None
            or getattr(runtime, "_manager", None) is not self._manager
            or getattr(runtime, "_pending_approval_snapshots", None) is not self._pending_approval_snapshots
        ):
            runtime = EvolutionApprovalRuntime(
                manager=self._manager,
                pending_approval_snapshots=self._pending_approval_snapshots,
            )
            self._approval_runtime = runtime
        return runtime

    # ===== TUI progress helper =====

    def _emit_progress(
        self,
        stage: str,
        message: str,
        *,
        skill_name: Optional[str] = None,
        request_id: Optional[str] = None,
    ) -> None:
        """Push a progress message to TUI (rendered as reasoning step) and log it.

        Trailing newline is required because the TUI concatenates consecutive
        reasoning chunks without separators (see appendThinkingChunk).
        """
        logger.info("[TeamSkillEvolutionRail] %s", message)
        event = build_evolution_progress_event(
            rail_kind="team",
            stage=stage,
            message=message,
            skill_name=skill_name,
            request_id=request_id,
            prefix="[Team Skill Evolution]",
        )
        self.emit_host_event(event)

    # ===== Lifecycle hooks =====

    async def _on_before_invoke(self, ctx: AgentCallbackContext) -> None:
        """Reset invoke-local passive completion state on each invoke boundary."""
        self._passive_evolution_pending = False

    async def _snapshot_for_evolution(
        self,
        trajectory: Trajectory,
        ctx: Optional[AgentCallbackContext],
    ) -> Optional[dict]:
        """Phase 1: Capture trajectory plus callback-visible messages for async evolution."""
        if not getattr(self, "_auto_scan", True):
            return None
        if ctx is None:
            return EvolutionSnapshot(
                trajectory=trajectory,
                messages=[],
                skill_name="team-skill",
            ).to_legacy_dict()
        snapshot = await super()._snapshot_for_evolution(trajectory, ctx)
        if snapshot is None:
            return None
        base_snapshot = EvolutionSnapshot.from_legacy_dict(snapshot)
        session = ctx.session if hasattr(ctx, "session") else None
        presented_entries = self._consume_presented_entries(session)
        team_snapshot = EvolutionSnapshot(
            trajectory=base_snapshot.trajectory,
            messages=list(base_snapshot.messages),
            skill_name="team-skill",
        ).to_legacy_dict()
        team_snapshot["presented_entries"] = presented_entries
        return team_snapshot

    async def _on_after_tool_call(self, ctx: AgentCallbackContext) -> None:
        """Detect team completion during the invoke and mark the round for after_invoke."""
        inputs = ctx.inputs
        if not isinstance(inputs, ToolCallInputs):
            return

        await self._record_presented_experience_detail(ctx, inputs)

        if not self._auto_scan:
            return

        if inputs.tool_name != "view_task":
            return

        result_preview = str(inputs.tool_result)[:300]
        logger.info("[TeamSkillEvolutionRail] view_task intercepted, result preview: %s", result_preview)

        completed = self._all_tasks_completed(inputs.tool_result)
        logger.debug(
            "[TeamSkillEvolutionRail] view_task completion check result=%s, session_id=%s",
            completed,
            self._current_builder_session_id(),
        )
        if not completed:
            logger.info("[TeamSkillEvolutionRail] view_task: tasks still in progress, skipping")
            return

        self._mark_passive_evolution_pending()

    def _allow_evolution_trigger(
        self,
        trigger_point: EvolutionTriggerPoint,
        ctx: AgentCallbackContext,
    ) -> bool:
        """Trigger passive evolution only if this invoke has observed team completion."""
        return self._auto_scan and (
            self._passive_evolution_pending
            or self._host_completion_pending_session_id == self._current_builder_session_id()
        )

    async def _on_after_evolution_triggered(
        self,
        trajectory: Trajectory,
        ctx: AgentCallbackContext,
    ) -> None:
        """Consume host completion marks after the after-invoke trigger fires."""
        if self._host_completion_pending_session_id == trajectory.session_id:
            self._host_completion_pending_session_id = None

    # ===== Public API: external completion notification =====

    async def notify_team_completed(
        self,
        ctx: Optional[AgentCallbackContext] = None,
    ) -> bool:
        """Mark the current invoke for passive evolution at the next after_invoke boundary."""
        if not self._auto_scan:
            logger.info("[TeamSkillEvolutionRail] notify_team_completed ignored because auto_scan is disabled")
            return False
        if self.builder is None:
            logger.warning(
                "[TeamSkillEvolutionRail] notify_team_completed: no trajectory available "
                "(before_invoke may not have fired)"
            )
            return False

        self._host_completion_pending_session_id = self.builder.session_id
        logger.debug(
            "[TeamSkillEvolutionRail] notify_team_completed marked session_id=%s",
            self._host_completion_pending_session_id,
        )
        return True

    def _current_builder_session_id(self) -> Optional[str]:
        """Return the current trajectory builder session id, if available."""
        if self.builder is None:
            return None
        return self.builder.session_id

    async def record_presented_experiences(
        self,
        skill_name: str,
        presentation_snippet: str,
        *,
        session: Any = None,
        record_ids: Optional[list[str]] = None,
    ) -> None:
        """Record team-skill experiences presented by a non-rail presentation path."""
        if record_ids is not None:
            await self._experience_tracker.record_presented_records(
                session=session,
                skill_name=skill_name,
                presentation_snippet=presentation_snippet,
                record_ids=record_ids,
            )
            return

        await self._experience_tracker.record_presented(
            session=session,
            skill_name=skill_name,
            presentation_snippet=presentation_snippet,
        )

    @staticmethod
    def _all_tasks_completed(result: Any) -> bool:
        """Check view_task result: True if >=1 completed and 0 non-terminal tasks."""
        return is_completed_team_task_view(result)

    # ===== EvolutionRail hook =====

    async def run_evolution(
        self,
        trajectory: Trajectory,
        ctx: Optional[AgentCallbackContext] = None,
        *,
        snapshot: Optional[dict] = None,
    ) -> None:
        """Triggered when view_task shows all member tasks completed."""
        if not getattr(self, "_auto_scan", True):
            logger.info("[TeamSkillEvolutionRail] auto_scan disabled, skipping")
            return
        t0 = time.time()
        try:
            self._emit_progress(
                "started",
                "team tasks completed; starting team skill evolution analysis",
            )
            messages: list[dict] = []
            presented_entries = []
            using_async_snapshot = snapshot is not None
            if using_async_snapshot:
                messages = list(snapshot["messages"])
                presented_entries = snapshot.get("presented_entries", [])
            elif ctx is not None:
                messages = self._collect_messages_from_trajectory(trajectory)
                session = ctx.session if hasattr(ctx, "session") else None
                presented_entries = self._consume_presented_entries(session)

            team_trajectory = self._aggregate_team_trajectory(trajectory)
            if team_trajectory is not trajectory:
                trajectory = team_trajectory
                if not using_async_snapshot:
                    messages = self._collect_messages_from_trajectory(trajectory)

            used_skill = self._detect_used_team_skill(trajectory)
            if not used_skill:
                logger.info("[TeamSkillEvolutionRail] no existing skill detected, skipping")
                self._emit_progress(
                    "cancelled",
                    "no skill usage of a team/swarm skill detected in trajectory; "
                    "cancelling team skill evolution analysis",
                )
                await self._evaluate_presented_entries(presented_entries)
                return

            logger.info("[TeamSkillEvolutionRail] detected existing skill '%s'", used_skill)

            # Load skill content for signal detection
            current_content = await self._store.read_skill_content(used_skill)

            signals = await self.team_signal_detector.detect_trajectory_signals(
                trajectory=trajectory,
                skill_name=used_skill,
                skill_content=current_content,
            )
            user_intent = None
            try:
                user_intent = await self._detect_user_request(messages, current_content)
            except Exception as exc:
                logger.warning("[TeamSkillEvolutionRail] user intent detection failed: %s", exc)
            if user_intent is not None and user_intent.intent:
                signals.append(
                    make_team_user_intent_signal(
                        skill_name=used_skill,
                        user_intent=user_intent.intent,
                    )
                )

            if not signals:
                logger.info("[TeamSkillEvolutionRail] no signals detected for '%s'", used_skill)
                self._emit_progress(
                    "cancelled",
                    f"no actionable evolution signals detected for '{used_skill}'; "
                    "cancelling team skill evolution analysis",
                    skill_name=used_skill,
                )
                await self._evaluate_presented_entries(presented_entries)
                return

            trajectory_issue_signals = [
                signal for signal in signals if signal.signal_type == TeamSignalType.TRAJECTORY_ISSUE.value
            ]
            issue_count = sum(len(get_team_trajectory_issues(signal)) for signal in trajectory_issue_signals)
            self._emit_progress(
                "detecting_signals",
                f"evolution signals detected: {issue_count} trajectory issues, "
                f"{len(signals) - len(trajectory_issue_signals)} user intents",
            )
            online_result = await self._handle_evolution_from_signals_with_result(
                skill_name=used_skill,
                trajectory=trajectory,
                signals=signals,
                auto_approve=getattr(self, "_auto_save", False),
                user_query=user_intent.intent if user_intent is not None else "",
                messages=messages,
            )
            request = request_for_online_evolution_result(online_result)
            if online_result.status in ONLINE_EVOLUTION_OUTCOME_STATUSES:
                if online_result.status == "no_evolution_no_records":
                    self._emit_progress("completed", "no evolution records generated")
            elif request is None:
                self._emit_progress("completed", "no evolution records generated")
            else:
                self._emit_progress(
                    "completed",
                    f"evolution request ready for '{used_skill}'",
                    skill_name=used_skill,
                    request_id=request.request_id,
                )

            await self._evaluate_presented_entries(presented_entries)

            elapsed = time.time() - t0
            logger.info("[TeamSkillEvolutionRail] run_evolution completed in %.1fs", elapsed)
        except Exception as exc:
            logger.warning("[TeamSkillEvolutionRail] run_evolution failed: %s", exc, exc_info=True)
            self._emit_background_outcome_event({"status": "failed", "message": f"team skill evolution failed: {exc}"})
            self._emit_progress("failed", f"evolution analysis failed: {exc}")

    def _mark_passive_evolution_pending(self) -> None:
        """Mark the current invoke as having observed a completed team state once."""
        if self._passive_evolution_pending:
            return
        self._passive_evolution_pending = True

    async def _record_presented_experience_detail(
        self,
        ctx: AgentCallbackContext,
        inputs: ToolCallInputs,
    ) -> None:
        skill_name = self._detect_experience_detail_read(inputs)
        if not skill_name:
            return

        content = self._extract_tool_content(inputs)
        record_ids = self._extract_presented_record_ids(content)
        if not record_ids:
            return

        session = ctx.session if hasattr(ctx, "session") else None
        await self._experience_tracker.record_presented_records(
            session=session,
            skill_name=skill_name,
            presentation_snippet=content,
            record_ids=record_ids,
        )

    def _consume_presented_entries(self, session: Any) -> list[tuple[str, Any, str]]:
        tracker = getattr(self, "_experience_tracker", None)
        if tracker is None:
            return []
        return tracker.consume_eval_state(session)

    async def _evaluate_presented_entries(self, presented_entries: list[tuple[str, Any, str]]) -> None:
        tracker = getattr(self, "_experience_tracker", None)
        if tracker is None:
            return
        await tracker.evaluate_presented(presented_entries)

    async def approve_record(
        self,
        request_id: str,
        *,
        approved_record_ids: Optional[list[str]] = None,
    ) -> None:
        """Handle approval of staged evolution records."""
        approve_kwargs: dict[str, list[str]] = {}
        if approved_record_ids is not None:
            approve_kwargs["approved_record_ids"] = approved_record_ids
        pending, result = await self.approval_runtime.approve_pending_request(
            request_id,
            rail_name="TeamSkillEvolutionRail",
            action_name="approve_record",
            **approve_kwargs,
        )
        if pending is None:
            return
        if result.pending_count > 0:
            return
        self._pending_approval_snapshots.pop(request_id, None)
        logger.info(
            "[TeamSkillEvolutionRail] user approved %d record(s) for '%s'",
            result.applied_count,
            pending.skill_name,
        )

    async def reject_record(self, request_id: str) -> None:
        pending, result = await self.approval_runtime.reject_pending_request(
            request_id,
            rail_name="TeamSkillEvolutionRail",
            action_name="reject_record",
        )
        if pending is None:
            return
        if result.rejected_count > 0:
            logger.info(
                "[TeamSkillEvolutionRail] user rejected %d record(s) for '%s'",
                result.rejected_count,
                pending.skill_name,
            )

    async def on_approve_record(self, request_id: str) -> None:
        """Compatibility alias for approve_record."""
        await self.approve_record(request_id)

    async def on_reject_record(self, request_id: str) -> None:
        """Compatibility alias for reject_record."""
        await self.reject_record(request_id)

    async def request_simplify(
        self,
        skill_name: str,
        user_intent: Optional[str] = None,
    ) -> SimplifyRequestResult:
        """Stage simplify governance for a team skill and emit approval event."""
        request_id = await self._manager.request_simplify(skill_name, user_intent=user_intent)
        if request_id is None:
            return SimplifyRequestResult(skill_name=skill_name)

        governance = self._pending_governance.get(request_id)
        if governance is None:
            return SimplifyRequestResult(skill_name=skill_name, request_id=request_id)

        actions = governance.get("actions", [])
        event = build_simplify_approval_event(
            skill_name=skill_name,
            request_id=request_id,
            actions=actions,
            language=getattr(self._manager, "_language", "cn"),
            rail_kind="team",
        )
        logger.info("[TeamSkillEvolutionRail] simplify staged for '%s' (request=%s)", skill_name, request_id)
        return SimplifyRequestResult(
            skill_name=skill_name,
            request_id=request_id,
            approval_event=event,
            actions=actions,
        )

    async def on_approve_simplify(self, request_id: str) -> dict[str, int]:
        """Execute a staged simplify request after approval."""
        result = await self._manager.approve_simplify(request_id)
        logger.info("[TeamSkillEvolutionRail] simplify approved (request=%s): %s", request_id, result)
        return result

    async def on_reject_simplify(self, request_id: str) -> None:
        """Discard a staged simplify request."""
        await self._manager.reject_simplify(request_id)
        logger.info("[TeamSkillEvolutionRail] simplify rejected (request=%s)", request_id)

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
        followup_text = await self._manager.request_rebuild(
            skill_name,
            user_intent=user_intent,
            min_score=min_score,
        )
        if followup_text is None:
            return None

        logger.info("[TeamSkillEvolutionRail] rebuild prompt generated for '%s'", skill_name)
        return followup_text

    async def request_user_evolution(
        self,
        skill_name: str,
        user_intent: str = "",
        *,
        auto_approve: bool = False,
    ) -> EvolutionRequestResult:
        """User-triggered evolution entry point.

        Allows user to explicitly provide improvement suggestions for a team skill,
        and is the only user-driven team-skill evolution path.

        Args:
            skill_name: Target team skill name.
            user_intent: User's explicit improvement suggestion.
            auto_approve: If True, records are stored directly without approval.
                          If False (default), records are staged for user approval.

        Returns:
            Structured result containing request_id, records, and optional approval event.

        Example:
            result = await rail.request_user_evolution(
                "research-team",
                "增加 reviewer 角色，限制 research 时间不超过 10 分钟"
            )
        """
        if not self._is_active_request_subject(skill_name):
            return EvolutionRequestResult(skill_name=skill_name)

        # Get current trajectory (from builder or use minimal placeholder)
        trajectory = self._build_trajectory()
        if trajectory is None:
            trajectory = Trajectory(
                execution_id="user_triggered",
                session_id="user_triggered",
                source="user_triggered",
                steps=[],
            )
        trajectory = self._aggregate_team_trajectory(trajectory)
        messages = self._collect_messages_from_trajectory(trajectory) if trajectory.steps else []

        signals = (
            await self._detect_active_request_signals(skill_name=skill_name, trajectory=trajectory)
            if trajectory.steps
            else []
        )

        if user_intent:
            self._append_unique_signal(
                signals,
                make_team_user_intent_signal(
                    skill_name=skill_name,
                    user_intent=user_intent,
                ),
            )

        if not signals:
            logger.info(
                "[TeamSkillEvolutionRail] request_user_evolution: no evidence or user intent for '%s'",
                skill_name,
            )
            return EvolutionRequestResult(skill_name=skill_name)

        if not messages and user_intent:
            messages = [{"role": "user", "content": user_intent}]

        online_result = await self._handle_evolution_from_signals_with_result(
            skill_name=skill_name,
            trajectory=trajectory,
            signals=signals,
            auto_approve=auto_approve,
            user_query=user_intent,
            messages=messages,
            emit_host_events=False,
        )
        request = request_for_online_evolution_result(online_result)

        if request is None:
            logger.info(
                "[TeamSkillEvolutionRail] request_user_evolution: no records generated for '%s'",
                skill_name,
            )
            return EvolutionRequestResult(
                skill_name=skill_name,
                status=online_result.status,
                message=online_result.message,
            )

        proposal = getattr(request, "proposal", None)
        records = list(getattr(proposal, "records", []) or [])
        approval_event = None
        if not auto_approve:
            approval_event = self._build_record_approval_event(
                skill_name,
                request,
                proposal=proposal,
            )
        return EvolutionRequestResult(
            skill_name=skill_name,
            request_id=request.request_id,
            approval_event=approval_event,
            records=records,
            auto_approved=auto_approve,
            status=online_result.status,
            message=online_result.message,
        )

    async def _detect_active_request_signals(
        self,
        *,
        skill_name: str,
        trajectory: Trajectory,
    ) -> list[EvolutionSignal]:
        """Detect team trajectory signals for an explicit active request."""
        current_content = await self._store.read_skill_content(skill_name)
        try:
            detected = await self.team_signal_detector.detect_trajectory_signals(
                trajectory=trajectory,
                skill_name=skill_name,
                skill_content=current_content,
            )
        except Exception as exc:
            logger.warning(
                "[TeamSkillEvolutionRail] active request trajectory detection failed for '%s': %s",
                skill_name,
                exc,
            )
            return []

        signals: list[EvolutionSignal] = []
        for signal in detected:
            if signal.skill_name and signal.skill_name != skill_name:
                continue
            self._append_unique_signal(signals, signal)
        return signals

    @staticmethod
    def _append_unique_signal(signals: list[EvolutionSignal], signal: EvolutionSignal) -> None:
        fingerprint = make_signal_fingerprint(signal)
        if any(make_signal_fingerprint(existing) == fingerprint for existing in signals):
            return
        signals.append(signal)

    def _is_active_request_subject(self, skill_name: str) -> bool:
        """Return whether a requested team-skill subject is eligible for active evolution."""
        try:
            if not self._store.skill_exists(skill_name):
                return False
        except Exception:
            logger.debug("[TeamSkillEvolutionRail] could not validate skill existence for '%s'", skill_name)
            return False

        try:
            skill_dir = self._store.resolve_skill_dir(skill_name)
        except Exception:
            return True
        if isinstance(skill_dir, (str, Path)):
            return self._is_team_skill(skill_name)
        return True

    # ===== Private helpers =====

    def _detect_used_team_skill(self, trajectory: Trajectory) -> Optional[str]:
        """Scan trajectory for SKILL.md read traces to identify which team skill was used.

        Only considers skills whose SKILL.md frontmatter declares a
        team/swarm skill kind so regular skills in the shared directory
        are not mistakenly matched.
        """
        all_skill_names = set(self._store.list_skill_names())
        if not all_skill_names:
            logger.info("[TeamSkillEvolutionRail] no existing team skills on disk")
            return None

        # Filter to only team/swarm skill kinds.
        known_skills = {name for name in all_skill_names if self._is_team_skill(name)}
        if self._disabled_skills:
            known_skills = {name for name in known_skills if name not in self._disabled_skills}
        if not known_skills:
            logger.info(
                "[TeamSkillEvolutionRail] no team-skill kind skills found among %d total skills",
                len(all_skill_names),
            )
            return None

        best = infer_team_skill_from_trajectory(trajectory, known_skills)
        if best:
            logger.info("[TeamSkillEvolutionRail] detected team skill '%s' from trajectory", best)
            return best

        logger.info("[TeamSkillEvolutionRail] no skill SKILL.md reads found in trajectory")
        return None

    def _detect_experience_detail_read(self, inputs: ToolCallInputs) -> Optional[str]:
        tool_name = str(inputs.tool_name or "")
        args = self._extract_tool_args(inputs.tool_args)

        if tool_name == "skill_tool":
            skill_name = str(args.get("skill_name", "") or "").strip()
            relative_path = str(args.get("relative_file_path") or "SKILL.md").strip()
            is_experience_detail = self._is_experience_detail_relative_path(relative_path)
            if skill_name and self._is_team_skill(skill_name) and is_experience_detail:
                return skill_name
            return None

        if "read" not in tool_name.lower() or "file" not in tool_name.lower():
            return None

        file_path = str(args.get("file_path", "") or "").strip()
        if not file_path:
            return None
        if "/evolution/" not in file_path.replace("\\", "/"):
            return None
        return self._team_skill_for_experience_detail_file(file_path)

    @staticmethod
    def _extract_tool_args(tool_args: Any) -> dict:
        if isinstance(tool_args, dict):
            return tool_args
        if isinstance(tool_args, str):
            try:
                parsed = json.loads(tool_args)
                return parsed if isinstance(parsed, dict) else {}
            except json.JSONDecodeError:
                return {}
        return {}

    @classmethod
    def _extract_presented_record_ids(cls, content: str) -> list[str]:
        seen: set[str] = set()
        record_ids: list[str] = []
        for match in cls._EXPERIENCE_RECORD_HEADING_RE.finditer(content):
            record_id = match.group(1)
            if record_id in seen:
                continue
            seen.add(record_id)
            record_ids.append(record_id)
        return record_ids

    @staticmethod
    def _extract_tool_content(inputs: ToolCallInputs) -> str:
        result = inputs.tool_result
        data = getattr(result, "data", None)
        if isinstance(data, dict):
            content = data.get("skill_content") or data.get("content") or ""
            if content:
                return content if isinstance(content, str) else str(content)

        tool_msg = inputs.tool_msg
        if tool_msg is not None and hasattr(tool_msg, "content"):
            content = tool_msg.content
            return content if isinstance(content, str) else str(content)

        content = getattr(result, "content", "")
        return content if isinstance(content, str) else str(content)

    @staticmethod
    def _is_experience_detail_relative_path(relative_path: str) -> bool:
        normalized = posixpath.normpath(relative_path.replace("\\", "/")).strip("/")
        if normalized.startswith("../") or "/../" in normalized:
            return False
        if not normalized.startswith("evolution/"):
            return False
        if normalized.startswith("evolution/scripts/"):
            return False
        if normalized.endswith("/SKILL.md") or normalized == "SKILL.md":
            return False
        if normalized.endswith("evolutions.json"):
            return False
        return normalized.lower().endswith(".md")

    def _team_skill_for_experience_detail_file(self, file_path: str) -> Optional[str]:
        try:
            read_path = Path(file_path).expanduser().resolve()
        except OSError:
            read_path = Path(file_path).expanduser()

        try:
            skill_names = self._store.list_skill_names()
        except Exception:
            return None

        for skill_name in skill_names:
            if not self._is_team_skill(skill_name):
                continue
            skill_dir = self._store.resolve_skill_dir(skill_name)
            if skill_dir is None:
                continue
            skill_path = Path(skill_dir).expanduser()
            try:
                relative = read_path.relative_to(skill_path.resolve())
            except (OSError, ValueError):
                continue
            if self._is_experience_detail_relative_path(str(relative)):
                return skill_name
        return None

    async def _detect_user_request(
        self,
        messages: list[dict],
        team_skill_content: str,
    ) -> Optional[UserIntent]:
        """Bridge to the team signal detector for explicit user-intent parsing."""
        if not messages:
            return None
        return await self.team_signal_detector.detect_user_intent(
            messages=messages,
            team_skill_content=team_skill_content,
        )

    def _aggregate_team_trajectory(self, trajectory: Trajectory) -> Trajectory:
        """Return aggregated team trajectory when a runtime source has data."""
        source = getattr(self, "_trajectory_source", None)
        if source is None:
            return trajectory

        team_id = getattr(self, "_team_id", None) or "unknown"
        team_traj = source.get_trajectory(
            team_id=team_id,
            session_id=trajectory.session_id or "",
            filter_collaborative=True,
        )
        if team_traj is None or not team_traj.steps:
            return trajectory

        self._emit_progress(
            "detecting_signals",
            f"aggregated {team_traj.meta.get('member_count', 0)} members, {len(team_traj.steps)} collaborative steps",
        )
        return team_traj

    async def _handle_evolution_from_signals_with_result(
        self,
        *,
        skill_name: str,
        trajectory: Trajectory,
        signals: list[EvolutionSignal],
        auto_approve: bool,
        user_query: str = "",
        messages: Optional[list[dict]] = None,
        emit_host_events: bool = True,
    ) -> OnlineEvolutionResult:
        """Handle evolution and retain the orchestrator status for active APIs."""
        if emit_host_events:
            self._emit_progress(
                "generating_updates",
                f"generating evolution records for '{skill_name}'",
                skill_name=skill_name,
            )
        result = await self._stage_evolution_from_signals(
            skill_name=skill_name,
            trajectory=trajectory,
            signals=signals,
            auto_approve=auto_approve,
            user_query=user_query,
            messages=messages,
        )
        request = result.request
        if result.status in ONLINE_EVOLUTION_OUTCOME_STATUSES:
            if emit_host_events:
                self._emit_background_outcome_event(
                    {
                        "status": result.status,
                        "message": result.message or f"online evolution finished with status={result.status}",
                        "rail_kind": "team",
                        "skill_name": result.skill_name,
                        "request_id": getattr(request, "request_id", None),
                        "stage": "completed" if result.status == "no_evolution_no_records" else "failed",
                        "source": "team_skill_experience_updater",
                    }
                )
            return result

        if request is None:
            return result

        def _emit_approval_request(staged_request: ExperienceApprovalRequest) -> None:
            pending = staged_request.pending_change
            if pending is not None:
                self._emit_record_approval_event(
                    skill_name,
                    pending,
                    proposal=staged_request.proposal,
                )
            logger.info(
                "[TeamSkillEvolutionRail] signal consumed and records staged for approval, change_id=%s",
                staged_request.request_id,
            )
            if emit_host_events:
                self._emit_progress(
                    "approval_required",
                    f"experience records for '{skill_name}' ready, awaiting approval",
                    skill_name=skill_name,
                    request_id=staged_request.request_id,
                )

        def _on_auto_approved(staged_request: ExperienceApprovalRequest) -> None:
            logger.info(
                "[TeamSkillEvolutionRail] signal consumed and records auto-approved for '%s'",
                skill_name,
            )
            if emit_host_events:
                self._emit_progress(
                    "auto_approved",
                    f"experience records auto-saved to '{skill_name}'",
                    skill_name=skill_name,
                    request_id=staged_request.request_id,
                )

        await self.approval_runtime.finalize_staged_evolution_request(
            request,
            requires_approval=not auto_approve,
            emit_approval_request=_emit_approval_request if emit_host_events else (lambda staged_request: None),
            on_auto_approved=_on_auto_approved,
        )
        return result

    async def _stage_evolution_from_signals(
        self,
        skill_name: str,
        *,
        trajectory: Trajectory,
        signals: list[EvolutionSignal],
        auto_approve: bool,
        user_query: str = "",
        messages: Optional[list[dict]] = None,
    ) -> OnlineEvolutionResult:
        """Stage team-skill evolution from normalized signals through the shared orchestrator."""
        self._emit_progress("staging", f"staging evolution request for '{skill_name}'", skill_name=skill_name)
        return await self._online_orchestrator.evolve(
            skill_name=skill_name,
            signals=signals,
            messages=messages or [],
            user_query=user_query,
            trajectory=trajectory,
            requires_approval=not auto_approve,
            metadata={},
            source="team_skill_experience_updater",
        )

    def _is_team_skill(self, name: str) -> bool:
        """Check whether a skill's SKILL.md declares a team/swarm skill kind."""
        skill_dir = self._store.resolve_skill_dir(name)
        if skill_dir is None:
            return False
        skill_md = skill_dir / "SKILL.md"
        if not skill_md.exists():
            return False
        try:
            text = skill_md.read_text(encoding="utf-8")
            frontmatter = parse_top_level_frontmatter(text)
            return frontmatter.get("kind") in _TEAM_SKILL_KINDS
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
            logger.info("[TeamSkillEvolutionRail] trajectory dumped to %s", path)
        except Exception as exc:
            logger.warning("[TeamSkillEvolutionRail] trajectory dump failed: %s", exc)

    def _emit_record_approval_event(
        self,
        skill_name: str,
        pending: PendingChange,
        proposal: ExperienceProposal,
    ) -> None:
        """Buffer a team-skill evolution approval event."""
        event = self._build_record_approval_event(skill_name, pending, proposal=proposal)
        self.emit_host_event(event)

        sections = ", ".join(r.change.section for r in pending.payload)
        self._emit_progress(
            "approval_required",
            f"TEAM SKILL EVOLUTION PROPOSED: '{skill_name}'\n"
            f"  sections: {sections}\n"
            f"  record_count: {len(pending.payload)}\n"
            f"  change_id: {pending.change_id}\n"
            f"  ACTION: an approval dialog should pop up; if not visible, "
            f"check approval panel or rerun task",
        )

    def _build_record_approval_event(
        self,
        skill_name: str,
        pending: PendingChange | ExperienceApprovalRequest,
        *,
        proposal: Optional[ExperienceProposal],
    ) -> OutputSchema:
        if isinstance(pending, ExperienceApprovalRequest):
            pending_change = pending.pending_change
            if pending_change is None:
                records = pending.proposal.records
                request_id = pending.request_id or ""
            else:
                records = pending_change.payload
                request_id = pending_change.change_id
        else:
            records = pending.payload
            request_id = pending.change_id
        event = build_team_skill_approval_event_from_records(
            skill_name=skill_name,
            request_id=request_id,
            records=records,
            language=getattr(getattr(self, "_generator", None), "language", "en"),
            rail_kind="team",
        )
        attach_evolution_meta(
            event,
            rail_kind="team",
            signal_type=getattr(proposal, "signal_type", None),
            signal_source=getattr(proposal, "signal_source", None),
        )
        return event

    @property
    def _pending_record_snapshots(self) -> dict[str, PendingChange]:
        """Compatibility alias for the old team-rail snapshot field."""
        return self._pending_approval_snapshots

    @_pending_record_snapshots.setter
    def _pending_record_snapshots(self, value: dict[str, PendingChange]) -> None:
        self._pending_approval_snapshots = value


__all__ = [
    "TeamSkillEvolutionRail",
    "TeamSignalType",
    "TrajectoryIssue",
    "UserIntent",
    "infer_team_skill_from_trajectory",
    "is_completed_team_task_view",
]
