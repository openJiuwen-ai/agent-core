# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""SkillEvolutionRail for online auto-evolution."""

from __future__ import annotations

import json
import os
import posixpath
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from openjiuwen.agent_evolving.checkpointing import EvolutionStore
from openjiuwen.agent_evolving.checkpointing.types import EvolutionRecord
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
from openjiuwen.agent_evolving.optimizer.skill_call import SkillExperienceOptimizer
from openjiuwen.agent_evolving.optimizer.skill_call.experience_optimizer import (
    GENERATE_RECORDS_LLM_POLICY,
)
from openjiuwen.agent_evolving.protocols import (
    CONVERSATION_REVIEW_SIGNAL,
    USER_INTENT_SIGNAL,
)
from openjiuwen.agent_evolving.signal import (
    EvolutionSignal,
    SignalDetector,
    make_evolution_signal,
    make_signal_fingerprint,
)
from openjiuwen.agent_evolving.trajectory import Trajectory, TrajectoryStore
from openjiuwen.agent_evolving.updater import SingleDimUpdater
from openjiuwen.agent_evolving.utils import infer_skill_from_texts, parse_top_level_frontmatter
from openjiuwen.core.common.logging import logger
from openjiuwen.core.foundation.llm.model import Model
from openjiuwen.core.operator.skill_call import SkillExperienceOperator
from openjiuwen.core.session.stream import OutputSchema
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext, ToolCallInputs
from openjiuwen.core.sys_operation import SysOperation
from openjiuwen.harness.rails.evolution.approval_events import (
    attach_evolution_meta,
    build_evolution_progress_event,
    build_simplify_approval_event,
    build_skill_approval_event,
)
from openjiuwen.harness.rails.evolution.approval_runtime import EvolutionApprovalRuntime
from openjiuwen.harness.rails.evolution.contracts import (
    EvolutionRequestResult,
    SimplifyRequestResult,
)
from openjiuwen.harness.rails.evolution.evolution_rail import EvolutionRail
from openjiuwen.harness.rails.evolution.skill_evolution_sharing import SkillEvolutionSharingMixin

_MAX_PROCESSED_SIGNAL_KEYS = 500
_DEFAULT_EVOLUTION_TOTAL_TIMEOUT_SECS = 600.0
_NON_REGULAR_SKILL_KINDS = {"team-skill", "swarm-skill"}


class SkillEvolutionRail(SkillEvolutionSharingMixin, EvolutionRail):
    """Online auto-evolution rail for skill patching and persistence.

    Inherits EvolutionRail to gain automatic trajectory collection.
    Evolution logic runs in after_invoke (after complete conversation) because
    skill evolution needs full conversation context for signal detection.

    Note: This class uses two stores:
    - trajectory_store (from EvolutionRail): stores execution trajectories
    - evolution_store (EvolutionStore): stores skill evolution data (experiences, SKILL.md)
    """

    priority = 80
    _DEFAULT_MEMBER_ROLE = "teammate"
    _SKILL_MD_RE = re.compile(r"[/\\]([^/\\]+)[/\\]SKILL\.md", re.IGNORECASE)
    _EXPERIENCE_RECORD_HEADING_RE = re.compile(r"#+\s*\[([A-Za-z0-9_-]+)\]")

    def __init__(
        self,
        skills_dir: Union[str, List[str]],
        *,
        llm: Model,
        model: str,
        auto_scan: bool = True,
        auto_save: bool = True,
        language: str = "cn",
        trajectory_store: Optional[TrajectoryStore] = None,
        team_trajectory_store: Optional[TrajectoryStore] = None,
        eval_interval: int = 5,
        evolution_total_timeout_secs: float = _DEFAULT_EVOLUTION_TOTAL_TIMEOUT_SECS,
        generate_records_llm_policy: LLMInvokePolicy = GENERATE_RECORDS_LLM_POLICY,
        evaluate_llm_policy: LLMInvokePolicy = EVALUATE_LLM_POLICY,
        simplify_llm_policy: LLMInvokePolicy = SIMPLIFY_LLM_POLICY,
        sharing_config: Optional[Dict[str, Any]] = None,
        disabled_skills: Optional[Union[str, List[str]]] = None,
    ) -> None:
        """Initialize SkillEvolutionRail.

        Args:
            skills_dir: Directory or list of directories containing skill definitions
            llm: LLM client for experience generation
            model: Model name for experience generation
            auto_scan: Whether to auto-detect evolution signals
            auto_save: Whether to auto-save generated experiences
            language: Language for experience generation ("cn" or "en")
            trajectory_store: Optional trajectory store (inherited from EvolutionRail)
            eval_interval: Number of conversations between async evaluations
            sharing_config: Optional cross-user sharing settings (enabled, hub_path, etc.)
            disabled_skills: Optional deny-list of skill names excluded from self-optimization.
                Supports a single skill name (str) or multiple names (list[str]).
        """
        if eval_interval < 1:
            raise ValueError("eval_interval must be >= 1")

        super().__init__(
            trajectory_store=trajectory_store,
            team_trajectory_store=team_trajectory_store,
            disabled_skills=disabled_skills,
        )
        self._evolution_store = EvolutionStore(skills_dir)
        self._evolver = SkillExperienceOptimizer(
            llm,
            model,
            language,
            generate_records_llm_policy=generate_records_llm_policy,
        )
        self._scorer = ExperienceScorer(
            llm,
            model,
            language,
            evaluate_llm_policy=evaluate_llm_policy,
            simplify_llm_policy=simplify_llm_policy,
        )
        self._auto_scan = auto_scan
        self._processed_signal_keys: set[tuple[str, ...]] = set()
        self._auto_save = auto_save
        # Optimizer path (for _auto_save=False): memory-staged records until user approval
        self._skill_ops: Dict[str, SkillExperienceOperator] = {}  # skill_name -> operator
        self._generate_records_llm_policy = generate_records_llm_policy
        self._evaluate_llm_policy = evaluate_llm_policy
        self._simplify_llm_policy = simplify_llm_policy
        self._evolution_total_timeout_secs = evolution_total_timeout_secs
        # request_id → PendingChange: stable per-approval-prompt snapshot batches
        self._pending_approval_snapshots: Dict[str, PendingChange] = {}
        # Governance staging: request_id → {kind, skill_name, actions/new_body}
        self._pending_governance: Dict[str, Dict[str, Any]] = {}
        self._language = language
        self._manager = ExperienceManager(
            store=self._evolution_store,
            scorer=self._scorer,
            kind="skill",
            language=self._language,
            skill_ops=self._skill_ops,
            pending_approval_snapshots=self._pending_approval_snapshots,
            pending_governance=self._pending_governance,
        )
        self._approval_runtime = EvolutionApprovalRuntime(
            manager=self._manager,
            pending_approval_snapshots=self._pending_approval_snapshots,
        )
        self._online_updater = SingleDimUpdater(self._evolver)
        self._online_orchestrator = OnlineEvolutionOrchestrator(
            store=self._evolution_store,
            updater=self._online_updater,
            manager=self._manager,
            skill_ops=self._skill_ops,
            stage_source="experience_updater",
        )
        # Evaluation settings
        self._eval_interval = eval_interval
        self._experience_tracker = ExperienceTracker(
            store=self._evolution_store,
            scorer=self._scorer,
            eval_interval=self._eval_interval,
        )
        self._init_sharing(
            sharing_config,
            llm=llm,
            model=model,
            language=language,
            evolution_store=self._evolution_store,
        )

    @property
    def store(self) -> EvolutionStore:
        """Deprecated: Use evolution_store instead. Kept for backward compatibility."""
        return self._evolution_store

    @property
    def evolution_store(self) -> EvolutionStore:
        """Get the evolution store (for skill data, not trajectories)."""
        return self._evolution_store

    @property
    def scorer(self) -> ExperienceScorer:
        """Get the experience scorer."""
        return self._scorer

    @property
    def evolver(self) -> SkillExperienceOptimizer:
        """Get the experience optimizer."""
        return self._evolver

    @property
    def generate_records_llm_policy(self) -> LLMInvokePolicy:
        """Get the configured record generation policy."""
        return self._generate_records_llm_policy

    @property
    def evaluate_llm_policy(self) -> LLMInvokePolicy:
        """Get the configured experience evaluation policy."""
        return self._evaluate_llm_policy

    @property
    def simplify_llm_policy(self) -> LLMInvokePolicy:
        """Get the configured experience maintenance policy."""
        return self._simplify_llm_policy

    @property
    def evolution_total_timeout_secs(self) -> float:
        """Get the configured background evolution timeout budget."""
        return self._evolution_total_timeout_secs

    @property
    def evolution_config(self) -> Dict[str, Any]:
        """Get the effective evolution configuration."""
        return {
            "generate_records_llm_policy": self.generate_records_llm_policy,
            "evaluate_llm_policy": self.evaluate_llm_policy,
            "simplify_llm_policy": self.simplify_llm_policy,
            "evolution_total_timeout_secs": self.evolution_total_timeout_secs,
        }

    @property
    def processed_signal_keys(self) -> set[tuple[str, ...]]:
        """Get processed signal fingerprints."""
        return self._processed_signal_keys

    def _get_evolution_total_timeout_secs(self) -> Optional[float]:
        return self._evolution_total_timeout_secs

    def _allow_evolution_trigger(
        self,
        trigger_point,
        ctx: AgentCallbackContext,
    ) -> bool:
        return self._auto_scan

    @property
    def auto_save(self) -> bool:
        """Whether auto-save is enabled."""
        return self._auto_save

    @property
    def approval_runtime(self) -> EvolutionApprovalRuntime:
        """Approval lifecycle helper for regular skill evolution."""
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

    @auto_save.setter
    def auto_save(self, value: bool) -> None:
        self._auto_save = value

    @property
    def auto_scan(self) -> bool:
        """Whether auto-scan is enabled."""
        return self._auto_scan

    @auto_scan.setter
    def auto_scan(self, value: bool) -> None:
        self._auto_scan = value

    def set_sys_operation(self, sys_operation: SysOperation) -> None:
        """Set sys_operation for both EvolutionRail and EvolutionStore."""
        super().set_sys_operation(sys_operation)
        self._evolution_store.sys_operation = sys_operation

    def update_llm(self, llm: Model, model: str) -> None:
        """Hot-update LLM client and model."""
        self._evolver.update_llm(llm, model)
        self._scorer.update_llm(llm, model)
        if self._keyword_extractor is not None:
            self._keyword_extractor.update_llm(llm, model)

    def clear_processed_signals(self) -> None:
        """Clear signal fingerprints, typically on conversation boundary."""
        self._processed_signal_keys.clear()

    async def record_presented_experiences(
        self,
        skill_name: str,
        presentation_snippet: str,
        *,
        session: Any = None,
        record_ids: Optional[List[str]] = None,
    ) -> None:
        """Record experiences presented by a non-rail presentation path.

        This preserves scoring maintenance without letting the rail mutate
        SKILL.md read results.
        """
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

    async def _on_after_tool_call(self, ctx: AgentCallbackContext) -> None:
        """Track explicit experience detail reads without modifying tool results.

        SKILL.md reads are index discovery only. BODY experiences are counted
        as presented only when a detail file under evolution/*.md is read and
        the returned content includes concrete record headings.
        """
        inputs = ctx.inputs
        if not isinstance(inputs, ToolCallInputs):
            return

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

    async def run_evolution(
        self,
        trajectory: Trajectory,
        ctx: Optional[AgentCallbackContext] = None,
        *,
        snapshot: Optional[dict] = None,
    ) -> None:
        """Run skill evolution based on the collected trajectory.

        In async mode: ctx=None, snapshot contains data captured by _snapshot_for_evolution.
        In sync mode: ctx is active, snapshot=None (backward-compatible).
        """
        logger.info("[SkillEvolutionRail] run_evolution called, auto_scan=%s", self._auto_scan)
        if not self._auto_scan:
            logger.info("[SkillEvolutionRail] auto_scan disabled, skipping")
            return

        try:
            # Async path: read from snapshot
            if snapshot is not None:
                trajectory = snapshot.get("trajectory", trajectory)
                messages = snapshot["messages"]
                presented_entries = snapshot.get("presented_entries", [])
            # Sync path: read from ctx (backward-compatible)
            elif ctx is not None:
                messages = self._collect_messages_from_trajectory(trajectory)
                session = ctx.session if hasattr(ctx, "session") else None
                presented_entries = self._experience_tracker.consume_eval_state(session)
            else:
                return

            logger.info("[SkillEvolutionRail] collected %d messages", len(messages))
            self._emit_progress(
                "started",
                "starting regular skill evolution analysis for completed conversation",
            )
            if not messages:
                logger.info("[SkillEvolutionRail] no messages, skipping")
                self._emit_progress(
                    "cancelled",
                    "no conversation messages available; cancelling regular skill evolution analysis",
                )
                await self._experience_tracker.evaluate_presented(presented_entries)
                return

            all_skill_names = self._evolution_store.list_skill_names()
            skill_names = [name for name in all_skill_names if self._is_regular_skill(name)]
            if self._disabled_skills:
                skill_names = [name for name in skill_names if name not in self._disabled_skills]
            logger.info(
                "[SkillEvolutionRail] found %d regular skills (filtered from %d local skills)",
                len(skill_names),
                len(all_skill_names),
            )
            self._emit_progress(
                "detecting_signals",
                f"checking {len(skill_names)} regular skill(s) for evolution signals "
                f"(filtered from {len(all_skill_names)} local skill(s))",
            )

            detector = SignalDetector(
                existing_skills={name for name in skill_names if self._evolution_store.skill_exists(name)}
            ).bind_llm(
                llm=self._evolver.llm,
                model=self._evolver.model,
                language=self._language,
            )
            detected = detector.detect_trajectory_signals(trajectory, messages=messages)
            signals: List[EvolutionSignal] = []
            existing_fingerprints: set[tuple[str, str, str, str]] = set()
            for signal in detected:
                fp = make_signal_fingerprint(signal)
                existing_fingerprints.add(fp)
                if fp not in self._processed_signal_keys:
                    self._processed_signal_keys.add(fp)
                    signals.append(signal)
            user_intent_signals = await detector.detect_user_intent(messages)
            for signal in user_intent_signals:
                fp = make_signal_fingerprint(signal)
                if fp in existing_fingerprints:
                    continue
                if fp not in self._processed_signal_keys:
                    self._processed_signal_keys.add(fp)
                    existing_fingerprints.add(fp)
                    signals.append(signal)
            if len(self._processed_signal_keys) > _MAX_PROCESSED_SIGNAL_KEYS:
                self._processed_signal_keys.clear()
            logger.info("[SkillEvolutionRail] detected %d signal(s)", len(signals))

            if not signals:
                primary_skill = self._infer_primary_skill(messages, skill_names)
                logger.info("[SkillEvolutionRail] no signals, inferred primary skill: %s", primary_skill)
                if primary_skill:
                    signals = [
                        make_evolution_signal(
                            signal_type=CONVERSATION_REVIEW_SIGNAL,
                            section="",
                            excerpt="[Auto] No rule-based signals. Analyze conversation for implicit experiences.",
                            skill_name=primary_skill,
                            source="passive_conversation",
                        )
                    ]
                # Continue to new skill detection when no signals and no primary skill

            attributed_skills = {s.skill_name for s in signals if s.skill_name}
            unattributed = [s for s in signals if not s.skill_name]
            if len(attributed_skills) == 1 and unattributed:
                fallback_skill = next(iter(attributed_skills))
                for s in unattributed:
                    s.skill_name = fallback_skill

            skill_groups: dict[str, List[EvolutionSignal]] = {}
            for signal in signals:
                if not signal.skill_name:
                    continue
                skill_groups.setdefault(signal.skill_name, []).append(signal)

            if not skill_groups:
                if signals:
                    message = (
                        "detected evolution signals but no regular skill could be attributed; "
                        "cancelling regular skill evolution analysis"
                    )
                else:
                    message = (
                        "no skill usage of a regular skill or actionable evolution signal detected; "
                        "cancelling regular skill evolution analysis"
                    )
                self._emit_progress("cancelled", message)
                await self._experience_tracker.evaluate_presented(presented_entries)
                return

            # Download hub experiences before local generation
            incremental_messages = self._resolve_incremental_messages(messages, ctx, snapshot)
            downloaded_per_skill: dict[str, List[EvolutionRecord]] = {}
            if self.is_sharing_enabled and skill_groups:
                downloaded_per_skill = await self._download_shared_experiences(
                    messages,
                    list(skill_groups.keys()),
                    incremental_messages=incremental_messages,
                )

            for skill_name, skill_signals in skill_groups.items():
                generated = await self._evolve_skill_with_sharing(
                    skill_name=skill_name,
                    skill_signals=skill_signals,
                    messages=messages,
                    ctx=ctx,
                    shared_records=downloaded_per_skill.get(skill_name, []),
                )
                if not generated:
                    self._emit_progress(
                        "completed",
                        f"no evolution records generated for '{skill_name}'",
                        skill_name=skill_name,
                    )

            await self._experience_tracker.evaluate_presented(presented_entries)
        except Exception as exc:
            logger.warning("[SkillEvolutionRail] auto evolution failed: %s", exc)

    async def generate_and_emit_experience(
        self,
        skill_name: str,
        signals: List[EvolutionSignal],
        messages: List[dict],
        user_query: str = "",
    ) -> bool:
        """Backward-compatible wrapper for user-triggered skill evolution.

        Args:
            skill_name: Name of the skill to evolve.
            signals: Legacy detected signals. Used only to derive a fallback user intent.
            messages: Legacy conversation messages. Used only to derive a fallback user intent.
            user_query: Optional user-specified optimization direction.

        Returns:
            True if a user evolution request was created.
        """
        user_intent = self._legacy_user_intent(user_query=user_query, signals=signals, messages=messages)
        result = await self.request_user_evolution(skill_name, user_intent, auto_approve=False)
        return result.request_id is not None

    @staticmethod
    def _legacy_user_intent(
        *,
        user_query: str,
        signals: List[EvolutionSignal],
        messages: List[dict],
    ) -> str:
        if user_query:
            return user_query
        for signal in signals:
            if signal.excerpt:
                return signal.excerpt
        for message in reversed(messages):
            # Handle both dict and Pydantic model objects (SystemMessage, AssistantMessage, etc.)
            content = message.get("content") if isinstance(message, dict) else getattr(message, "content", "")
            if isinstance(content, str) and content:
                return content
        return ""

    async def request_user_evolution(
        self,
        skill_name: str,
        user_intent: str = "",
        *,
        auto_approve: bool = False,
    ) -> EvolutionRequestResult:
        """User-triggered evolution entry point for regular skills."""
        trajectory = self._build_trajectory()
        messages: List[dict] = []
        signals: List[EvolutionSignal] = []
        if trajectory is not None and trajectory.steps:
            messages = self._collect_messages_from_trajectory(trajectory)
            signals = await self._detect_active_request_signals(
                skill_name=skill_name,
                trajectory=trajectory,
                messages=messages,
            )

        if user_intent:
            self._append_unique_signal(
                signals,
                make_evolution_signal(
                    signal_type=USER_INTENT_SIGNAL,
                    section="Instructions",
                    excerpt=user_intent,
                    skill_name=skill_name,
                    source="explicit_request",
                ),
            )

        if not signals:
            return EvolutionRequestResult(skill_name=skill_name)

        if not messages and user_intent:
            messages = [{"role": "user", "content": user_intent}]

        online_result = await self._handle_evolution_from_signals_with_result(
            skill_name=skill_name,
            signals=signals,
            messages=messages,
            ctx=None,
            user_query=user_intent,
            requires_approval=not auto_approve,
            emit_host_events=False,
        )
        request = request_for_online_evolution_result(online_result)

        if request is None:
            return EvolutionRequestResult(
                skill_name=skill_name,
                status=online_result.status,
                message=online_result.message,
            )

        proposal = getattr(request, "proposal", None)
        records = list(getattr(proposal, "records", []) or [])
        approval_event = None if auto_approve else self._build_generated_records_event(skill_name, request)
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
        messages: List[dict],
    ) -> List[EvolutionSignal]:
        """Detect evidence-window signals for an explicit regular-skill request."""
        detector = SignalDetector(existing_skills=self._active_request_regular_skill_names(skill_name)).bind_llm(
            llm=self._evolver.llm,
            model=self._evolver.model,
            language=self._language,
        )
        detected: List[EvolutionSignal] = []
        try:
            detected.extend(detector.detect_trajectory_signals(trajectory, messages=messages))
        except Exception as exc:
            logger.warning("[SkillEvolutionRail] active request trajectory detection failed: %s", exc)
        try:
            detected.extend(await detector.detect_user_intent(messages))
        except Exception as exc:
            logger.warning("[SkillEvolutionRail] active request user-feedback detection failed: %s", exc)

        signals: List[EvolutionSignal] = []
        for signal in detected:
            if signal.skill_name and signal.skill_name != skill_name:
                continue
            self._append_unique_signal(signals, signal)
        return signals

    def _active_request_regular_skill_names(self, skill_name: str) -> set[str]:
        """Return known regular skills for active-request attribution."""
        try:
            all_skill_names = list(self._evolution_store.list_skill_names())
        except Exception:
            return {skill_name}
        skill_names = {name for name in all_skill_names if self._is_regular_skill(name)}
        skill_names.add(skill_name)
        return skill_names

    @staticmethod
    def _append_unique_signal(signals: List[EvolutionSignal], signal: EvolutionSignal) -> None:
        fingerprint = make_signal_fingerprint(signal)
        if any(make_signal_fingerprint(existing) == fingerprint for existing in signals):
            return
        signals.append(signal)

    def _emit_progress(
        self,
        stage: str,
        message: str,
        *,
        skill_name: Optional[str] = None,
        request_id: Optional[str] = None,
    ) -> None:
        logger.info("[SkillEvolutionRail] %s", message)
        self.emit_host_event(
            build_evolution_progress_event(
                rail_kind="regular",
                stage=stage,
                message=message,
                skill_name=skill_name,
                request_id=request_id,
                prefix="[Skill Evolution]",
            )
        )

    def _build_generated_records_event(
        self,
        skill_name: str,
        approval_request: Optional[ExperienceApprovalRequest | ExperienceProposal] = None,
    ) -> Optional[OutputSchema]:
        if approval_request is None:
            return None
        pending = approval_request.pending_change
        if pending is None or approval_request.request_id is None:
            return None
        event = build_skill_approval_event(
            skill_name=skill_name,
            request_id=approval_request.request_id,
            records=pending.payload,
            language=self._language,
            is_shared_records=bool(getattr(pending, "is_shared_records", False)),
            rail_kind="regular",
        )
        proposal = getattr(approval_request, "proposal", None)
        attach_evolution_meta(
            event,
            rail_kind="regular",
            signal_type=getattr(proposal, "signal_type", None),
            signal_source=getattr(proposal, "signal_source", None),
        )
        return event

    async def _emit_generated_records(
        self,
        ctx: Optional[AgentCallbackContext],
        skill_name: str,
        approval_request: Optional[ExperienceApprovalRequest | ExperienceProposal] = None,
    ) -> None:
        """Buffer an approval-request OutputSchema for later delivery.

        The event is stored in the shared host event buffer because
        ``after_invoke`` runs after the session stream is already
        closed; writing to the stream at this point would be lost.
        The host drains these events via ``drain_pending_approval_events``.

        The pending records must already be snapshotted into the
        ``ExperienceApprovalRequest`` by ExperienceManager.
        """
        event = self._build_generated_records_event(skill_name, approval_request)
        if event is None:
            return
        self.emit_host_event(event)
        self._emit_progress(
            "approval_required",
            f"experience records for '{skill_name}' ready, awaiting approval",
            skill_name=skill_name,
            request_id=approval_request.request_id,
        )
        logger.info(
            "[SkillEvolutionRail] buffered approval request (%s) with %d record(s) for skill=%s",
            approval_request.request_id,
            approval_request.proposal.record_count,
            skill_name,
        )

    def _detect_signals(
        self,
        messages: List[dict],
        skill_names: List[str],
    ) -> List[EvolutionSignal]:
        existing_skills = {name for name in skill_names if self._evolution_store.skill_exists(name)}
        detector = SignalDetector(existing_skills=existing_skills)
        detected = detector.detect(messages)

        new_signals: List[EvolutionSignal] = []
        for signal in detected:
            fp = make_signal_fingerprint(signal)
            if fp not in self._processed_signal_keys:
                self._processed_signal_keys.add(fp)
                new_signals.append(signal)

        if len(self._processed_signal_keys) > _MAX_PROCESSED_SIGNAL_KEYS:
            self._processed_signal_keys.clear()

        if new_signals:
            logger.info(
                "[SkillEvolutionRail] detected %d new signal(s), filtered=%d",
                len(new_signals),
                len(detected) - len(new_signals),
            )
        return new_signals

    def _infer_primary_skill(
        self,
        messages: List[dict],
        skill_names: List[str],
    ) -> Optional[str]:
        """Infer the most likely active skill from SKILL.md read traces in the conversation."""
        skill_tool_payloads: list[Any] = []
        texts: list[str] = []
        for msg in messages:
            # Handle both dict and Pydantic model objects (SystemMessage, AssistantMessage, etc.)
            if isinstance(msg, dict):
                role = msg.get("role", "")
                if role in ("tool", "function"):
                    texts.append(str(msg.get("content", "")))
                elif role == "assistant":
                    for tool_call in msg.get("tool_calls", []):
                        texts.append(str(tool_call.get("arguments", "")))
                        if tool_call.get("name") == "skill_tool":
                            skill_tool_payloads.append(tool_call.get("arguments"))
            else:
                # Pydantic model: use attribute access
                role = getattr(msg, "role", "")
                if role in ("tool", "function"):
                    content = getattr(msg, "content", "")
                    texts.append(str(content))
                elif role == "assistant":
                    tool_calls = getattr(msg, "tool_calls", None) or []
                    for tool_call in tool_calls:
                        args = getattr(tool_call, "arguments", "")
                        texts.append(str(args))
                        if getattr(tool_call, "name", "") == "skill_tool":
                            skill_tool_payloads.append(args)

        return infer_skill_from_texts(
            skill_names,
            skill_tool_payloads=skill_tool_payloads,
            texts=texts,
        )

    def _is_regular_skill(self, name: str) -> bool:
        """Exclude team/swarm skills from 1D skill evolution detection.

        If the skill directory cannot be resolved from a mocked or incomplete
        store, keep the skill eligible rather than silently disabling
        evolution for it.
        """
        skill_dir = self._evolution_store.resolve_skill_dir(name)
        if skill_dir is None:
            return True

        if isinstance(skill_dir, (str, os.PathLike)):
            skill_dir = Path(skill_dir)
        elif not isinstance(skill_dir, Path):
            return True

        skill_md = skill_dir / "SKILL.md"
        if not skill_md.exists():
            return True

        try:
            text = skill_md.read_text(encoding="utf-8")
            frontmatter = parse_top_level_frontmatter(text)
            return frontmatter.get("kind") not in _NON_REGULAR_SKILL_KINDS
        except Exception:
            return True

    async def _stage_evolution_from_signals(
        self,
        skill_name: str,
        signals: List[EvolutionSignal],
        messages: List[dict],
        *,
        user_query: str = "",
        requires_approval: bool,
    ) -> OnlineEvolutionResult:
        """Generate and stage skill experiences through the unified updater flow.

        Returns the structured orchestration result.
        """
        return await self._online_orchestrator.evolve(
            skill_name=skill_name,
            requires_approval=requires_approval,
            signals=signals,
            messages=messages,
            user_query=user_query,
            metadata={},
            source="experience_updater",
        )

    async def _handle_evolution_from_signals(
        self,
        *,
        skill_name: str,
        signals: List[EvolutionSignal],
        messages: List[dict],
        ctx: Optional[AgentCallbackContext],
        user_query: str = "",
        requires_approval: bool,
        emit_host_events: bool = True,
    ) -> Optional[ExperienceApprovalRequest]:
        """Shared downstream handler for both passive and explicit skill evolution."""
        result = await self._handle_evolution_from_signals_with_result(
            skill_name=skill_name,
            signals=signals,
            messages=messages,
            ctx=ctx,
            user_query=user_query,
            requires_approval=requires_approval,
            emit_host_events=emit_host_events,
        )
        return request_for_online_evolution_result(result)

    async def _handle_evolution_from_signals_with_result(
        self,
        *,
        skill_name: str,
        signals: List[EvolutionSignal],
        messages: List[dict],
        ctx: Optional[AgentCallbackContext],
        user_query: str = "",
        requires_approval: bool,
        emit_host_events: bool = True,
    ) -> OnlineEvolutionResult:
        """Handle evolution and retain the orchestrator status for active APIs."""
        if emit_host_events:
            self._emit_progress(
                "generating_updates", f"generating evolution records for '{skill_name}'", skill_name=skill_name
            )
        result = await self._stage_evolution_from_signals(
            skill_name=skill_name,
            signals=signals,
            messages=messages,
            user_query=user_query,
            requires_approval=requires_approval,
        )
        request = result.request
        if result.status in ONLINE_EVOLUTION_OUTCOME_STATUSES:
            if emit_host_events:
                self._emit_background_outcome_event(
                    {
                        "status": result.status,
                        "message": result.message or f"online evolution finished with status={result.status}",
                        "rail_kind": "regular",
                        "skill_name": result.skill_name,
                        "request_id": getattr(request, "request_id", None),
                        "stage": "completed" if result.status == "no_evolution_no_records" else "failed",
                        "source": "experience_updater",
                    }
                )
            return result

        if request is None:
            return result

        async def _on_auto_approved(staged_request: ExperienceApprovalRequest) -> None:
            logger.info(
                "[SkillEvolutionRail] auto-approved evolution request for skill=%s (request=%s)",
                skill_name,
                staged_request.request_id,
            )
            if emit_host_events:
                self._emit_progress(
                    "auto_approved",
                    f"experience records auto-saved to '{skill_name}'",
                    skill_name=skill_name,
                    request_id=staged_request.request_id,
                )
            await self._sharing_after_auto_approved(
                skill_name=skill_name,
                staged_request=staged_request,
            )

        await self.approval_runtime.finalize_staged_evolution_request(
            request,
            requires_approval=requires_approval,
            emit_approval_request=(
                (lambda staged_request: self._emit_generated_records(ctx, skill_name, staged_request))
                if emit_host_events
                else (lambda staged_request: None)
            ),
            on_auto_approved=_on_auto_approved,
        )
        return result

    async def approve_record(
        self,
        request_id: str,
        *,
        approved_record_ids: Optional[List[str]] = None,
    ) -> None:
        """Approve staged evolution records.

        The ``request_id`` matches ``payload["request_id"]`` from the approval event.
        When ``approved_record_ids`` is provided, only those records are written.
        """
        snapshot_pending = self._pending_approval_snapshots.get(request_id)
        approved_records: List[EvolutionRecord] = []
        approval_messages = None
        is_shared = False
        if snapshot_pending is not None:
            payload = getattr(snapshot_pending, "payload", None)
            if isinstance(payload, list):
                approved_records = list(payload)
            approval_messages = getattr(snapshot_pending, "messages", None)
            is_shared = bool(getattr(snapshot_pending, "is_shared_records", False))
        if approved_record_ids is not None and approved_records:
            approved_id_set = set(approved_record_ids)
            approved_records = [record for record in approved_records if record.id in approved_id_set]

        approve_kwargs: Dict[str, List[str]] = {}
        if approved_record_ids is not None:
            approve_kwargs["approved_record_ids"] = approved_record_ids
        pending, result = await self.approval_runtime.approve_pending_request(
            request_id,
            rail_name="SkillEvolutionRail",
            action_name="approve_record",
            **approve_kwargs,
        )
        if pending is None:
            return
        if result.pending_count:
            return
        logger.info(
            "[SkillEvolutionRail] user approved %d record(s) for skill=%s (request=%s, is_shared=%s)",
            result.applied_count,
            pending.skill_name,
            request_id,
            is_shared,
        )
        if not is_shared and approved_records:
            await self._stage_records_for_share(
                skill_name=pending.skill_name,
                messages=approval_messages,
                records=approved_records,
            )
            await self._flush_share_uploads(pending.skill_name)

    async def reject_record(self, request_id: str) -> None:
        """Reject staged evolution records without writing them.

        The ``request_id`` matches ``payload["request_id"]`` from the approval event.
        """
        pending, result = await self.approval_runtime.reject_pending_request(
            request_id,
            rail_name="SkillEvolutionRail",
            action_name="reject_record",
        )
        if pending is None:
            return
        logger.info(
            "[SkillEvolutionRail] user rejected %d record(s) for skill=%s (request=%s)",
            result.rejected_count,
            pending.skill_name,
            request_id,
        )

    async def on_approve(self, request_id: str) -> None:
        """Compatibility alias for approve_record."""
        await self.approve_record(request_id)

    async def on_reject(self, request_id: str) -> None:
        """Compatibility alias for reject_record."""
        await self.reject_record(request_id)

    @classmethod
    def _extract_file_path(cls, tool_args: Any) -> str:
        args = cls._extract_tool_args(tool_args)
        file_path = args.get("file_path", "")
        return str(file_path) if file_path else ""

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

    def _detect_experience_detail_read(self, inputs: ToolCallInputs) -> Optional[str]:
        tool_name = str(inputs.tool_name or "")
        args = self._extract_tool_args(inputs.tool_args)

        if tool_name == "skill_tool":
            skill_name = str(args.get("skill_name", "") or "").strip()
            relative_path = str(args.get("relative_file_path") or "SKILL.md").strip()
            if skill_name and self._is_experience_detail_relative_path(relative_path):
                return skill_name
            return None

        if "read" not in tool_name.lower() or "file" not in tool_name.lower():
            return None

        file_path = str(args.get("file_path", "") or "").strip()
        if not file_path:
            return None
        if "/evolution/" not in file_path.replace("\\", "/"):
            return None
        return self._skill_for_experience_detail_file(file_path)

    @classmethod
    def _extract_presented_record_ids(cls, content: str) -> List[str]:
        seen: set[str] = set()
        record_ids: List[str] = []
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

    def _skill_for_experience_detail_file(self, file_path: str) -> Optional[str]:
        try:
            read_path = Path(file_path).expanduser().resolve()
        except OSError:
            read_path = Path(file_path).expanduser()

        try:
            skill_names = self._evolution_store.list_skill_names()
        except Exception:
            return None

        for skill_name in skill_names:
            skill_dir = self._evolution_store.resolve_skill_dir(skill_name)
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

    @classmethod
    def _parse_messages(cls, messages: List[Any]) -> List[dict]:
        result: List[dict] = []
        for message in messages:
            if isinstance(message, dict):
                result.append(message)
                continue

            role = getattr(message, "role", "")
            content = str(getattr(message, "content", "") or "")

            item: dict = {"role": role, "content": content}

            tool_calls = getattr(message, "tool_calls", None)
            if tool_calls:
                item["tool_calls"] = [
                    {
                        "id": getattr(tool_call, "id", ""),
                        "name": getattr(tool_call, "name", ""),
                        "arguments": getattr(tool_call, "arguments", ""),
                    }
                    for tool_call in tool_calls
                ]

            name = getattr(message, "name", None)
            if name:
                item["name"] = name

            result.append(item)
        return result

    async def _snapshot_for_evolution(
        self,
        trajectory: Trajectory,
        ctx: AgentCallbackContext,
    ) -> Optional[dict]:
        """Phase 1: Collect messages while ctx is alive."""
        if not self._auto_scan:
            return None

        snapshot = await super()._snapshot_for_evolution(trajectory, ctx)
        if snapshot is None:
            return None

        messages = snapshot["messages"]
        if not messages:
            return None

        session_id = ctx.inputs.conversation_id if ctx.inputs else ""
        session = ctx.session if hasattr(ctx, "session") else None
        presented_entries = self._experience_tracker.consume_eval_state(session)

        snapshot.update(
            {
                "session_id": session_id,
                "presented_entries": presented_entries,
                "skill_name": "skill-evolution",
                "incremental_messages": self._resolve_incremental_messages(messages, ctx, None),
            }
        )
        return snapshot

    # ── Governance commands (shared by 1D and team skills) ──

    async def request_simplify(
        self,
        skill_name: str,
        user_intent: Optional[str] = None,
    ) -> SimplifyRequestResult:
        """Stage a simplify proposal and emit an approval event.

        Returns a proposal result with an approval event when actions are proposed.
        """
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
            language=self._language,
            rail_kind="regular",
        )
        return SimplifyRequestResult(
            skill_name=skill_name,
            request_id=request_id,
            approval_event=event,
            actions=actions,
        )

    async def on_approve_simplify(self, request_id: str) -> Dict[str, int]:
        """Execute a previously staged simplify proposal."""
        result = await self._manager.approve_simplify(request_id)
        if result:
            logger.info("[SkillEvolutionRail] simplify executed for request=%s: %s", request_id, result)
        return result

    async def on_reject_simplify(self, request_id: str) -> None:
        gov = self._pending_governance.get(request_id)
        await self._manager.reject_simplify(request_id)
        if gov:
            logger.info("[SkillEvolutionRail] simplify rejected for %s", gov["skill_name"])

    async def request_rebuild(
        self,
        skill_name: str,
        user_intent: Optional[str] = None,
        min_score: float = 0.5,
    ) -> Optional[str]:
        """Build a rebuild prompt for skill.

        Archive old version FIRST, then return prompt containing filtered
        evolution records. The caller (slash command handler or host) injects
        this prompt into agent loop, and agent executes skill-creator to
        generate new SKILL.md.

        Args:
            skill_name: Name of the skill to rebuild.
            user_intent: Optional user-specified optimization direction.
            min_score: Minimum score threshold for evolution records to include.
                Default 0.5 filters out low-quality experiences.

        Returns the followup prompt text on success, None if skill not found.
        """
        followup_text = await self._manager.request_rebuild(
            skill_name,
            user_intent=user_intent,
            min_score=min_score,
        )
        if followup_text is None:
            return None
        logger.info(
            "[SkillEvolutionRail] rebuild prompt built for skill=%s, min_score=%.2f",
            skill_name,
            min_score,
        )
        return followup_text

    async def rollback_skill(self, skill_name: str, version: Optional[str] = None) -> bool:
        """Rollback skill to an archived version (no approval required)."""
        store = self._evolution_store
        skill_dir = store.resolve_skill_dir(skill_name)
        if skill_dir is None:
            return False

        archive = skill_dir / "archive"
        if not archive.is_dir():
            logger.warning("[SkillEvolutionRail] no archive dir for %s", skill_name)
            return False

        if version:
            body_archive = archive / version
            evo_version = version.replace("SKILL.", "evolutions.").replace(".md", ".json")
            evo_archive = archive / evo_version
        else:
            body_files = sorted(
                [f for f in archive.iterdir() if f.name.startswith("SKILL.v")],
                key=lambda p: p.name,
                reverse=True,
            )
            if not body_files:
                logger.warning("[SkillEvolutionRail] no archived body for %s", skill_name)
                return False
            body_archive = body_files[0]
            evo_version = body_archive.name.replace("SKILL.", "evolutions.").replace(".md", ".json")
            evo_archive = archive / evo_version

        # Archive current state first
        await store.archive_skill_body(skill_name)
        await store.archive_evolutions(skill_name)

        old_body = await store.read_file_text(body_archive)
        await store.write_skill_content(skill_name, old_body)

        if evo_archive.is_file():
            evo_content = await store.read_file_text(evo_archive)
            evo_path = skill_dir / "evolutions.json"
            await store.write_file_text(evo_path, evo_content)
        else:
            await store.clear_evolutions(skill_name)

        await store.render_evolution_markdown(skill_name)
        logger.info("[SkillEvolutionRail] rollback completed for %s -> %s", skill_name, body_archive.name)
        return True

    def should_hint_simplify_or_rebuild(self, skill_name: str) -> bool:
        """Check if a skill has enough evolutions to suggest simplify/rebuild."""
        store = self._evolution_store
        skill_dir = store.resolve_skill_dir(skill_name)
        if skill_dir is None:
            return False
        evo_path = skill_dir / "evolutions.json"
        if not evo_path.is_file():
            return False
        try:
            data = json.loads(evo_path.read_text(encoding="utf-8"))
            entries = data.get("entries", [])
            return len(entries) >= 10
        except Exception:
            return False


__all__ = ["SkillEvolutionRail"]
