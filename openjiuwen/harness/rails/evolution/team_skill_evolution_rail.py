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
from copy import deepcopy
from pathlib import Path
from typing import Any, Optional, Union

from openjiuwen.agent_evolving.checkpointing import EvolutionStore
from openjiuwen.agent_evolving.experience.scorer import (
    EVALUATE_LLM_POLICY,
    SIMPLIFY_LLM_POLICY,
    ExperienceScorer,
)
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
from openjiuwen.agent_evolving.prompts.sections import build_team_evolution_protocol_section
from openjiuwen.agent_evolving.signal import (
    EvolutionSignal,
    SignalDetector,
    make_signal_fingerprint,
)
from openjiuwen.agent_evolving.trajectory.model import (
    Trajectory,
)
from openjiuwen.agent_evolving.trajectory.processor import TrajectorySpanProcessor
from openjiuwen.agent_evolving.trajectory.spans import iter_spans, read_tool_call
from openjiuwen.agent_evolving.trajectory.team import span_category
from openjiuwen.agent_evolving.utils import infer_skill_from_texts, parse_top_level_frontmatter
from openjiuwen.core.common.logging import logger
from openjiuwen.core.foundation.llm.model import Model
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
    SimplifyRequestResult,
)
from openjiuwen.harness.rails.evolution.evolution_rail import (
    EvolutionTriggerPoint,
    _TeamTrajectoryCaptureMixin,
)
from openjiuwen.harness.rails.evolution.review.runtime import EvolutionReviewRuntime
from openjiuwen.harness.rails.evolution.skill_evolution_rail import (
    _SkillPreparedEvolutionInput,
    SkillEvolutionRail,
)

_TEAM_RECORD_LLM_POLICY = LLMInvokePolicy(
    attempt_timeout_secs=150,
    total_budget_secs=300,
    max_attempts=2,
)
_DEFAULT_TEAM_EVOLUTION_TOTAL_TIMEOUT_SECS = 720.0
_TEAM_TASK_NON_TERMINAL_STATES = ("pending", "claimed", "in_progress", "blocked")
_TEAM_SKILL_KINDS = {"team-skill", "swarm-skill"}
_AUTO_TEAM_SKILL_EVOLUTION_FOLLOW_UP_TAG = "auto_team_skill_evolution_review_followup"
_TEAM_COMPLETION_FOLLOWUP_PROMPT_CN = (
    "这是运行时插入的 Team/Swarm Skill 演进自检，不是用户的新需求。\n"
    "团队任务已完成；参考常驻“团队 Skill 演进自检”规则，只判断本轮是否存在可复用团队更新，不重新判断"
    "运行时触发门槛。\n"
    "如需建议，只在普通最终回复末尾追加一至两句，并同时包含可复用团队更新点和是否发起 Team/Swarm Skill "
    "演进的确认问题；否则自然回复，不提本提醒或内部判断。"
)
_TEAM_COMPLETION_FOLLOWUP_PROMPT_EN = (
    "This runtime-inserted Team/Swarm Skill evolution self-check is not a new user request.\n"
    'The team task is complete. Refer to the standing "Team Skill Evolution Self-Check" rules and judge only whether '
    "this round contains a reusable team update; do not re-evaluate the runtime trigger threshold.\n"
    "If suggesting, append only one or two sentences to the normal final reply and include both the reusable team "
    "update and the Team/Swarm Skill evolution question; otherwise reply naturally without mentioning this reminder "
    "or internal judgment."
)


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
    for span in iter_spans(trajectory):
        if span_category(span) != "tool":
            continue
        tool_call = read_tool_call(span)
        tool_name = tool_call.get("name", "")
        if tool_name == "skill_tool":
            skill_tool_payloads.append(tool_call.get("input"))
        texts.append(str(tool_call.get("input", "")))
        texts.append(str(tool_call.get("output", "")))

    return infer_skill_from_texts(
        known_team_skills,
        skill_tool_payloads=skill_tool_payloads,
        texts=texts,
    )


class _ReviewFeedbackGlobalSkillEvolutionRail(SkillEvolutionRail):
    """Regular global-Skill persistence using the existing team approval route."""

    def _online_request_id_prefix(self) -> str | None:
        return "team_skill_evolve"

    def _online_stage_source(self) -> str:
        return "scheduler_review_feedback"


class TeamSkillEvolutionRail(_TeamTrajectoryCaptureMixin, SkillEvolutionRail):
    """Team skill evolution rail — counterpart of SkillEvolutionRail.

    SkillEvolutionRail handles 1D skill experience records;
    TeamSkillRail handles team skill experience records.
    New team skill creation is handled by TeamSkillCreateRail.
    Both can coexist on the same agent.
    """

    priority = 80
    _DEFAULT_MEMBER_ROLE = "leader"
    _SKILL_MD_RE = re.compile(r"[/\\]([^/\\]+)[/\\]SKILL\.md", re.IGNORECASE)
    _EXPERIENCE_RECORD_HEADING_RE = re.compile(r"#+\s*\[([A-Za-z0-9_-]+)\]")
    _subject_kind_default: str = "swarm-skill"

    def __init__(
        self,
        skills_dir: Union[str, list[str]],
        *,
        llm: Model,
        model: str,
        language: str = "cn",
        trajectory_span_processor: TrajectorySpanProcessor,
        member_role: Optional[str] = None,
        signal_trigger: Optional[bool] = None,
        auto_save: bool = False,
        review_runtime: EvolutionReviewRuntime,
        async_evolution: bool = True,
        max_concurrent_evolution: int = 1,
        team_id: Optional[str] = None,
        record_llm_policy: LLMInvokePolicy = _TEAM_RECORD_LLM_POLICY,
        evaluate_llm_policy: LLMInvokePolicy = EVALUATE_LLM_POLICY,
        simplify_llm_policy: LLMInvokePolicy = SIMPLIFY_LLM_POLICY,
        eval_interval: int = 5,
        evolution_total_timeout_secs: float = _DEFAULT_TEAM_EVOLUTION_TOTAL_TIMEOUT_SECS,
        disabled_skills: Optional[Union[str, list[str]]] = None,
        review_trigger: Optional[bool] = None,
        review_interval: int = 5,
        review_agent_max_iterations: int = 40,
    ) -> None:
        if eval_interval < 1:
            raise ValueError("eval_interval must be >= 1")

        self._record_llm_policy = record_llm_policy
        super().__init__(
            skills_dir,
            llm=llm,
            model=model,
            signal_trigger=signal_trigger,
            auto_save=auto_save,
            review_runtime=review_runtime,
            language=language,
            subject_kind="swarm-skill",
            trajectory_span_processor=trajectory_span_processor,
            eval_interval=eval_interval,
            evolution_total_timeout_secs=evolution_total_timeout_secs,
            generate_records_llm_policy=record_llm_policy,
            evaluate_llm_policy=evaluate_llm_policy,
            simplify_llm_policy=simplify_llm_policy,
            disabled_skills=disabled_skills,
            evolution_trigger=EvolutionTriggerPoint.AFTER_INVOKE,
            async_evolution=async_evolution,
            max_concurrent_evolution=max_concurrent_evolution,
            review_trigger=review_trigger,
            review_interval=review_interval,
            review_agent_max_iterations=review_agent_max_iterations,
        )
        self._max_concurrent_evolution = max_concurrent_evolution
        self._store = self._evolution_store
        self._generator = self._evolver
        self._experience_skill_ops = self._skill_ops
        self._passive_evolution_pending = False
        self._host_completion_pending_session_id: Optional[str] = None
        self._completion_followup_pending_session_id: Optional[str] = None
        self._review_feedback_coordinator = None
        self._review_feedback_global_rail: SkillEvolutionRail | None = None
        self._review_feedback_skill_create_rail = None
        self._review_feedback_approval_continuations: dict[str, str] = {}
        self.set_member_role(member_role or self._DEFAULT_MEMBER_ROLE)
        logger.info(
            "[TeamSkillEvolutionRail] initialized: skills_dir=%s, model=%s, auto_save=%s, team_id=%s",
            skills_dir,
            model,
            auto_save,
            team_id,
        )

    def _prepare_evolution_review_scope(
        self,
        *,
        source: str,
        subject: dict[str, Any],
        session_id: str,
        member_id: str | None = None,
        team_id: str | None = None,
        user_intent: str = "",
    ):
        """Prepare review against the Team identity captured by the active root."""
        del member_id, team_id
        _, root_team_id = self._team_span_identity()
        return super()._prepare_evolution_review_scope(
            source=source,
            subject=subject,
            session_id=session_id,
            member_id=None,
            team_id=root_team_id,
            user_intent=user_intent,
        )

    def configure_review_feedback_evolution(
        self,
        *,
        global_skills_dir: str | Path,
        trajectory_registry: Any,
        session_id: str,
        team_id: str,
        min_confidence: float = 0.7,
    ) -> None:
        """Attach reviewer-feedback evolution to this standard team Rail.

        The internal regular-Skill Rail owns global Skill persistence, while
        this mounted team Rail remains the sole lifecycle and host-event
        surface. Scheduler feedback therefore follows the same pending-event
        and approval path as ordinary team evolution.
        """
        from openjiuwen.agent_teams.agent.scheduling.review_feedback_evolution import (
            ReviewFeedbackEvolutionCoordinator,
        )

        global_rail = _ReviewFeedbackGlobalSkillEvolutionRail(
            str(global_skills_dir),
            llm=self.evolver.llm,
            model=self.evolver.model,
            review_runtime=EvolutionReviewRuntime(),
            # The child rail never subscribes on its own; sharing the mounted
            # rail's processor keeps the whole evolution stack on one instance.
            trajectory_span_processor=self.trajectory_span_processor,
            language=self._language,
            signal_trigger=False,
            review_trigger=False,
            auto_save=self.auto_save,
            disabled_skills=list(getattr(self, "_disabled_skills", set())),
        )
        self._review_feedback_global_rail = global_rail
        self._review_feedback_coordinator = ReviewFeedbackEvolutionCoordinator(
            session_id=session_id,
            team_id=team_id,
            trajectory_registry=trajectory_registry,
            global_rail_provider=lambda: self._review_feedback_global_rail,
            skill_create_rail_provider=lambda: self._review_feedback_skill_create_rail,
            event_sink=self._relay_review_feedback_events,
            min_confidence=min_confidence,
        )

    @property
    def review_feedback_evolution_enabled(self) -> bool:
        """Whether this mounted Rail accepts scheduler review feedback."""
        return self._review_feedback_coordinator is not None

    def bind_review_feedback_skill_create_rail(self, rail: Any | None) -> None:
        """Bind the already-mounted creation Rail used for repeated patterns."""
        self._review_feedback_skill_create_rail = rail

    async def handle_review_feedback(self, payload: dict[str, Any]) -> None:
        """Route one settled failed review through the Core coordinator."""
        if self._review_feedback_coordinator is not None:
            await self._review_feedback_coordinator(payload)

    async def finalize_review_feedback(
        self,
        payload: dict[str, Any] | None = None,
    ) -> bool:
        """Run the terminal global aggregation pass for scheduler feedback."""
        if self._review_feedback_coordinator is None:
            return False
        return await self._review_feedback_coordinator.on_team_completed(payload)

    async def _relay_review_feedback_events(
        self,
        event_group: str,
        events: list[Any],
    ) -> None:
        """Relay child-Rail events into the mounted Rail's normal host queue."""
        if event_group == "global_evolution" and self._review_feedback_global_rail is not None:
            child_snapshots = getattr(
                self._review_feedback_global_rail,
                "_pending_approval_snapshots",
                {},
            )
            for request_id, snapshot in child_snapshots.items():
                self._pending_approval_snapshots[request_id] = snapshot
        elif event_group == "skill_creation" and self._review_feedback_skill_create_rail is not None:
            proposals = getattr(
                self._review_feedback_skill_create_rail,
                "_pending_external_proposals",
                {},
            )
            for request_id in proposals:
                # Creation approvals have no experience-record payload, but a
                # placeholder lets the existing request-owner lookup find this
                # mounted team Rail.
                self._pending_approval_snapshots.setdefault(request_id, None)
        for event in events:
            self.emit_host_event(event)

    def owns_approval_request(self, request_id: str) -> bool:
        """Return whether this Rail or one of its Core-owned children owns it."""
        return request_id in getattr(
            self,
            "_pending_approval_snapshots",
            {},
        ) or request_id in getattr(self, "_pending_governance", {})

    def pop_approval_continuation(self, request_id: str) -> str | None:
        """Return a post-approval continuation, if this request created one."""
        return getattr(
            self,
            "_review_feedback_approval_continuations",
            {},
        ).pop(request_id, None)

    def _make_evolution_store(self, skills_dir: Union[str, list[str]]) -> EvolutionStore:
        """Build and alias the team/swarm skill evolution store."""
        store = EvolutionStore(skills_dir)
        self._store = store
        return store

    def _make_skill_optimizer(
        self,
        llm: Model,
        model: str,
        language: str,
        *,
        generate_records_llm_policy: LLMInvokePolicy,
        two_stage: bool,
    ) -> SkillExperienceOptimizer:
        """Build the team/swarm optimizer used by the shared online pipeline."""
        return SkillExperienceOptimizer(
            llm,
            model,
            language,
            generate_records_llm_policy=generate_records_llm_policy,
            two_stage=two_stage,
            profile="team",
        )

    def _online_request_id_prefix(self) -> str | None:
        """Return the team/swarm request id prefix for passive evolution."""
        return "team_skill_evolve"

    def _online_stage_source(self) -> str:
        """Return the team/swarm stage source for generated experiences."""
        return "team_skill_experience_updater"

    @property
    def store(self) -> EvolutionStore:
        return self._store

    @property
    def scorer(self) -> ExperienceScorer:
        """Get the experience scorer."""
        return self._scorer

    @property
    def generator(self) -> SkillExperienceOptimizer:
        """Get the team skill evolution generator."""
        return self._generator

    async def before_model_call(self, ctx: AgentCallbackContext) -> None:
        """Inject only the team-specific evolution protocol section."""
        builder = getattr(getattr(ctx, "inputs", None), "system_prompt_builder", None)
        if builder is None:
            builder = getattr(getattr(ctx, "agent", None), "system_prompt_builder", None)
        if builder is not None:
            language = str(getattr(builder, "language", "") or self._language)
            builder.add_section(build_team_evolution_protocol_section(language))

    @property
    def record_llm_policy(self) -> LLMInvokePolicy:
        """Get the configured experience record generation policy."""
        return self._generator.generate_records_llm_policy

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
            "record_llm_policy": self.record_llm_policy,
            "evaluate_llm_policy": self.evaluate_llm_policy,
            "simplify_llm_policy": self.simplify_llm_policy,
            "eval_interval": self._eval_interval,
            "evolution_total_timeout_secs": self.evolution_total_timeout_secs,
            "max_concurrent_evolution": self._max_concurrent_evolution,
            "two_stage": self.two_stage,
        }

    @property
    def signal_trigger(self) -> bool:
        """Whether deterministic team-skill signal triggering is enabled."""
        return self._signal_trigger

    @signal_trigger.setter
    def signal_trigger(self, value: bool) -> None:
        self._signal_trigger = bool(value)

    @property
    def review_trigger(self) -> bool:
        """Whether team completion enqueues a review follow-up."""
        return self._review_trigger

    @review_trigger.setter
    def review_trigger(self, value: bool) -> None:
        self._review_trigger = bool(value)

    @property
    def auto_save(self) -> bool:
        """Whether generated team-skill records are auto-approved."""
        return self._auto_save

    @auto_save.setter
    def auto_save(self, value: bool) -> None:
        self._auto_save = bool(value)

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
        await super()._on_before_invoke(ctx)
        self._passive_evolution_pending = False
        self._skip_signal_trigger_this_invoke = False

    async def _prepare_evolution_input(
        self,
        trajectory: Trajectory,
        ctx: AgentCallbackContext,
    ) -> Optional[_SkillPreparedEvolutionInput]:
        """Capture one detached team-skill input while callback state is live."""
        prepared = await super()._prepare_evolution_input(trajectory, ctx)
        if prepared is None:
            return None
        return _SkillPreparedEvolutionInput(
            trajectory=prepared.trajectory,
            messages=prepared.messages,
            skill_name=self.subject_kind,
            presented_entries=prepared.presented_entries,
            incremental_messages=prepared.incremental_messages,
        )

    async def request_user_evolution(
        self,
        skill_name: str,
        user_intent: str = "",
        *,
        auto_approve: bool | None = None,
        max_index_records: int | None = None,
    ):
        """Compatibility wrapper for user-requested team skill evolution."""
        del auto_approve
        del max_index_records
        if not self._is_active_request_subject(skill_name):
            return EvolutionRequestResult(skill_name=skill_name)
        return await super().request_user_evolution(skill_name, user_intent)

    async def request_simplify(
        self,
        skill_name: str,
        user_intent: str | None = None,
        *,
        mode: str = "agent_prompt",
    ) -> SimplifyRequestResult:
        """Stage simplify governance for a team skill and emit approval event."""
        del mode
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

    async def request_rebuild(
        self,
        skill_name: str,
        user_intent: str | None = None,
        min_score: float = 0.5,
        *,
        max_context_records: int = 40,
        max_context_chars: int = 20000,
    ) -> Optional[str]:
        """Compatibility wrapper for deterministic team-skill rebuild prompt generation."""
        return await super().request_rebuild(
            skill_name,
            user_intent=user_intent,
            min_score=min_score,
            max_context_records=max_context_records,
            max_context_chars=max_context_chars,
        )

    async def _on_after_tool_call(
        self,
        ctx: AgentCallbackContext,
        trajectory: Trajectory | None,
    ) -> None:
        """Detect team completion during the invoke and mark the round for after_invoke."""
        inputs = ctx.inputs
        if not isinstance(inputs, ToolCallInputs):
            return

        await self._record_presented_experience_detail(ctx, inputs)

        if not self._signal_trigger and not self._review_trigger:
            return
        if trajectory is None:
            return

        if inputs.tool_name != "view_task":
            return

        result_preview = str(inputs.tool_result)[:300]
        logger.info("[TeamSkillEvolutionRail] view_task intercepted, result preview: %s", result_preview)

        completed = self._all_tasks_completed(inputs.tool_result)
        logger.debug(
            "[TeamSkillEvolutionRail] view_task completion check result=%s, session_id=%s",
            completed,
            self._current_trajectory_session_id(),
        )
        if not completed:
            logger.info("[TeamSkillEvolutionRail] view_task: tasks still in progress, skipping")
            return

        self._mark_team_completion_pending()

    def _allow_evolution_trigger(
        self,
        trigger_point: EvolutionTriggerPoint,
        ctx: AgentCallbackContext,
    ) -> bool:
        """Trigger passive evolution only if this invoke has observed team completion."""
        if self._skip_signal_trigger_this_invoke:
            logger.info("[TeamSkillEvolutionRail] active evolution activity detected, skip passive signal scan")
            return False
        if self._review_trigger:
            return False
        return self._signal_trigger and (
            self._passive_evolution_pending
            or self._host_completion_pending_session_id == self._current_trajectory_session_id()
        )

    async def _on_after_task_iteration(
        self,
        ctx: AgentCallbackContext,
        trajectory: Trajectory | None,
    ) -> None:
        """Enqueue team completion active-review follow-up while the task loop can schedule it."""
        if not self._review_trigger:
            return
        pending_session_id = self._completion_followup_pending_session_id
        if pending_session_id is None:
            return

        session_id = self._resolve_trajectory_session_id(ctx, ctx.inputs)
        if pending_session_id != session_id:
            return

        enqueued = self._enqueue_task_iteration_followup(
            ctx,
            self._build_team_completion_followup_prompt(),
            log_prefix="team completion",
        )
        if not enqueued:
            return

        self._completion_followup_pending_session_id = None
        self._host_completion_pending_session_id = None

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
        """Mark the current invoke for configured team completion evolution handling."""
        if not self._signal_trigger and not self._review_trigger:
            logger.info("[TeamSkillEvolutionRail] notify_team_completed ignored because signal_trigger is disabled")
            return False
        capture = self._resolve_capture(ctx=ctx)
        if capture is None:
            logger.warning(
                "[TeamSkillEvolutionRail] notify_team_completed: no active trajectory capture "
                "(before_invoke may not have fired or after_invoke already completed)"
            )
            return False
        trajectory = self.get_trajectory(
            session_id=capture.session_id,
            member_id=capture.member_id,
            team_id=capture.team_id,
        )
        if trajectory is None:
            logger.warning("[TeamSkillEvolutionRail] notify_team_completed: no clean trajectory available")
            return False

        self._mark_team_completion_pending()
        logger.debug(
            "[TeamSkillEvolutionRail] notify_team_completed marked session_id=%s",
            self._current_trajectory_session_id(),
        )
        return True

    def _current_trajectory_session_id(self) -> Optional[str]:
        """Return the active trajectory capture session id, if available."""
        capture = self._current_capture()
        return capture.session_id if capture is not None else None

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

    async def run_evolution(self, prepared: _SkillPreparedEvolutionInput) -> None:
        """Triggered when view_task shows all member tasks completed."""
        if not getattr(self, "_signal_trigger", True):
            logger.info("[TeamSkillEvolutionRail] signal_trigger disabled, skipping")
            return
        t0 = time.time()
        try:
            self._emit_progress(
                "started",
                "team tasks completed; starting team skill evolution analysis",
            )
            trajectory = prepared.trajectory
            messages = [deepcopy(message) for message in prepared.messages]
            presented_entries = [deepcopy(entry) for entry in prepared.presented_entries]

            used_skill = self._detect_used_team_skill(trajectory)
            if not used_skill:
                logger.info("[TeamSkillEvolutionRail] no existing skill detected, skipping")
                self._emit_progress(
                    "cancelled",
                    "no skill usage of a team/swarm skill detected in trajectory; "
                    "cancelling team skill evolution analysis",
                )
                await self._evaluate_presented_entries(presented_entries, messages)
                return

            logger.info("[TeamSkillEvolutionRail] detected existing skill '%s'", used_skill)

            signals = self._detect_rule_signals(trajectory=trajectory, skill_name=used_skill)

            if not signals:
                logger.info("[TeamSkillEvolutionRail] no signals detected for '%s'", used_skill)
                self._emit_progress(
                    "cancelled",
                    f"no actionable evolution signals detected for '{used_skill}'; "
                    "cancelling team skill evolution analysis",
                    skill_name=used_skill,
                )
                await self._evaluate_presented_entries(presented_entries, messages)
                return

            self._emit_progress(
                "detecting_signals",
                f"detected {len(signals)} deterministic rule signal(s) for '{used_skill}'",
                skill_name=used_skill,
            )
            online_result = await self._handle_evolution_from_signals_with_result(
                skill_name=used_skill,
                trajectory=trajectory,
                signals=signals,
                auto_approve=getattr(self, "_auto_save", False),
                user_query="",
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

            await self._evaluate_presented_entries(presented_entries, messages)

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

    def _mark_team_completion_pending(self) -> None:
        """Mark team completion for either passive scan or active follow-up mode."""
        session_id = self._current_trajectory_session_id()
        if self._review_trigger:
            self._completion_followup_pending_session_id = session_id
            return
        self._host_completion_pending_session_id = session_id
        self._mark_passive_evolution_pending()

    def _build_team_completion_followup_prompt(self) -> str:
        """Build the active team completion review follow-up prompt."""
        prompt = _TEAM_COMPLETION_FOLLOWUP_PROMPT_EN if self._language == "en" else _TEAM_COMPLETION_FOLLOWUP_PROMPT_CN
        return f"<{_AUTO_TEAM_SKILL_EVOLUTION_FOLLOW_UP_TAG}>\n{prompt}\n</{_AUTO_TEAM_SKILL_EVOLUTION_FOLLOW_UP_TAG}>"

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
            presentation_snippet="",
            record_ids=record_ids,
        )

    def _consume_presented_entries(self, session: Any) -> list[tuple[str, Any, str]]:
        tracker = getattr(self, "_experience_tracker", None)
        if tracker is None:
            return []
        return tracker.consume_eval_state(session)

    async def approve_record(
        self,
        request_id: str,
        *,
        approved_record_ids: Optional[list[str]] = None,
    ) -> None:
        """Handle approval of staged evolution records."""
        creation_rail = getattr(self, "_review_feedback_skill_create_rail", None)
        if creation_rail is not None and creation_rail.owns_external_proposal(request_id):
            continuation = creation_rail.resolve_external_proposal(
                request_id,
                accepted=True,
            )
            self._pending_approval_snapshots.pop(request_id, None)
            if continuation:
                continuations = getattr(
                    self,
                    "_review_feedback_approval_continuations",
                    None,
                )
                if continuations is None:
                    continuations = {}
                    self._review_feedback_approval_continuations = continuations
                continuations[request_id] = continuation
            return

        global_rail = getattr(self, "_review_feedback_global_rail", None)
        if global_rail is not None and request_id in getattr(
            global_rail,
            "_pending_approval_snapshots",
            {},
        ):
            await global_rail.approve_record(
                request_id,
                approved_record_ids=approved_record_ids,
            )
            child_snapshots = getattr(global_rail, "_pending_approval_snapshots", {})
            if request_id in child_snapshots:
                # A partial approval keeps the remaining records pending. Keep
                # the mounted Rail's mirror in sync so a later approval/reject
                # can still be routed through the standard team endpoint.
                self._pending_approval_snapshots[request_id] = child_snapshots[request_id]
            else:
                self._pending_approval_snapshots.pop(request_id, None)
            return

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
        creation_rail = getattr(self, "_review_feedback_skill_create_rail", None)
        if creation_rail is not None and creation_rail.owns_external_proposal(request_id):
            creation_rail.resolve_external_proposal(request_id, accepted=False)
            self._pending_approval_snapshots.pop(request_id, None)
            return

        global_rail = getattr(self, "_review_feedback_global_rail", None)
        if global_rail is not None and request_id in getattr(
            global_rail,
            "_pending_approval_snapshots",
            {},
        ):
            await global_rail.reject_record(request_id)
            self._pending_approval_snapshots.pop(request_id, None)
            return

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

    async def on_approve_simplify(self, request_id: str) -> dict[str, int]:
        """Execute a staged simplify request after approval."""
        result = await self._manager.approve_simplify(request_id)
        logger.info("[TeamSkillEvolutionRail] simplify approved (request=%s): %s", request_id, result)
        return result

    async def on_reject_simplify(self, request_id: str) -> None:
        """Discard a staged simplify request."""
        await self._manager.reject_simplify(request_id)
        logger.info("[TeamSkillEvolutionRail] simplify rejected (request=%s)", request_id)

    async def _detect_active_request_signals(
        self,
        *,
        skill_name: str,
        trajectory: Trajectory,
    ) -> list[EvolutionSignal]:
        """Detect deterministic team trajectory signals for an explicit active request."""
        return self._detect_rule_signals(
            trajectory=trajectory,
            skill_name=skill_name,
        )

    @staticmethod
    def _append_unique_signal(signals: list[EvolutionSignal], signal: EvolutionSignal) -> None:
        fingerprint = make_signal_fingerprint(signal)
        if any(make_signal_fingerprint(existing) == fingerprint for existing in signals):
            return
        signals.append(signal)

    def _is_active_request_subject(self, skill_name: str) -> bool:
        """Return whether a requested subject exists for active evolution."""
        try:
            return bool(self._store.skill_exists(skill_name))
        except Exception:
            logger.debug("[TeamSkillEvolutionRail] could not validate skill existence for '%s'", skill_name)
            return False

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

    def _detect_rule_signals(self, *, trajectory: Trajectory, skill_name: str) -> list[EvolutionSignal]:
        """Detect deterministic execution/script signals and attribute them to the team skill."""
        try:
            detected = SignalDetector(existing_skills={skill_name}).detect_trajectory_signals(
                trajectory,
                signal_types={"execution_failure", "script_artifact"},
            )
        except Exception as exc:
            logger.warning(
                "[TeamSkillEvolutionRail] rule signal detection failed for '%s': %s",
                skill_name,
                exc,
            )
            return []

        signals: list[EvolutionSignal] = []
        for signal in detected:
            if signal.skill_name and signal.skill_name != skill_name:
                continue
            signal.skill_name = skill_name
            self._append_unique_signal(signals, signal)
        return signals

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
    "infer_team_skill_from_trajectory",
    "is_completed_team_task_view",
]
