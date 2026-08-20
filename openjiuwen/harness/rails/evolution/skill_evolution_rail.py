# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""SkillEvolutionRail for online auto-evolution."""

from __future__ import annotations

import json
import os
import posixpath
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Union

from openjiuwen.agent_evolving.checkpointing import EvolutionStore
from openjiuwen.agent_evolving.checkpointing.types import EvolutionRecord
from openjiuwen.agent_evolving.experience import (
    ExperienceTracker,
    OnlineEvolutionOrchestrator,
)
from openjiuwen.agent_evolving.experience.draft_schema import normalize_evolution_subject_kind
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
)
from openjiuwen.agent_evolving.optimizer.llm_resilience import LLMInvokePolicy
from openjiuwen.agent_evolving.optimizer.skill_call import SkillExperienceOptimizer
from openjiuwen.agent_evolving.optimizer.skill_call.experience_optimizer import (
    GENERATE_RECORDS_LLM_POLICY,
)
from openjiuwen.agent_evolving.prompts.sections import build_evolution_protocol_section
from openjiuwen.agent_evolving.signal import (
    EvolutionSignal,
    SignalDetector,
    make_signal_fingerprint,
)
from openjiuwen.agent_evolving.skill_self_evolution import (
    resolve_skill_evolution_action,
)
from openjiuwen.agent_evolving.tools import create_main_evolution_tools
from openjiuwen.agent_evolving.trajectory import Trajectory, TrajectoryStore
from openjiuwen.agent_evolving.updater import SingleDimUpdater
from openjiuwen.agent_evolving.utils import infer_skill_from_texts, parse_top_level_frontmatter
from openjiuwen.core.common.logging import logger
from openjiuwen.core.foundation.llm.model import Model
from openjiuwen.core.operator.skill_call import SkillExperienceOperator
from openjiuwen.core.session.stream import OutputSchema
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext, RunKind, ToolCallInputs
from openjiuwen.core.sys_operation import SysOperation
from openjiuwen.harness.rails.evolution.approval_events import (
    attach_evolution_meta,
    build_evolution_progress_event,
    build_skill_approval_event,
)
from openjiuwen.harness.rails.evolution.approval_runtime import EvolutionApprovalRuntime
from openjiuwen.harness.rails.evolution.commands import (
    build_evolve_review_command_prompt,
    build_simplify_command_prompt,
)
from openjiuwen.harness.rails.evolution.contracts import (
    EvolutionRequestResult,
    SimplifyRequestResult,
)
from openjiuwen.harness.rails.evolution.evolution_rail import EvolutionRail, EvolutionTriggerPoint
from openjiuwen.harness.rails.evolution.review.materials import build_review_scoped_materials
from openjiuwen.harness.rails.evolution.review.runtime import EvolutionReviewRuntime
from openjiuwen.harness.rails.evolution.review.subagent import (
    EVOLUTION_REVIEW_AGENT_NAME,
    build_evolution_review_agent_config,
    ensure_evolution_review_agent_config,
    remove_evolution_review_agent_config,
)
from openjiuwen.harness.rails.evolution.skill_evolution_sharing import SkillEvolutionSharingMixin

_MAX_PROCESSED_SIGNAL_KEYS = 500
_DEFAULT_EVOLUTION_TOTAL_TIMEOUT_SECS = 600.0
_NON_REGULAR_SKILL_KINDS = {"team-skill", "swarm-skill"}
_AUTO_SKILL_EVOLUTION_FOLLOW_UP_TAG = "auto_skill_evolution_review_followup"
_FUZZY_REVIEW_PROMPT_CN = (
    "这是运行时自动插入的 Skill 演进 follow-up，不是用户的新需求。\n"
    "请参考“技能演进自检”规则，基于当前可见上下文和刚完成的执行过程，判断是否暴露了已使用 Skill 需要更新。"
)
_FUZZY_REVIEW_PROMPT_EN = (
    "This is a runtime-inserted Skill evolution follow-up; it is not a new user request.\n"
    'Refer to the "Skill Evolution Self-Check" rules and, based on the visible context\n'
    "and the recently completed execution,\n"
    "decide whether it exposed that a used Skill needs updating."
)
_EVAL_SNIPPET_MAX_MESSAGES = 20
_EVAL_SNIPPET_POST_PRESENT_MAX_CHARS = 800


def _normalize_skill_relative_file_path(relative_file_path: str) -> str:
    """Canonical relative path for the primary skill entry file (``SKILL.md``)."""
    raw = (relative_file_path or "").strip()
    if not raw:
        return "SKILL.md"
    normalized = raw.replace("\\", "/").removeprefix("./")
    if "/" in normalized:
        prefix, base = normalized.rsplit("/", 1)
    else:
        prefix, base = "", normalized
    if "." not in base and base.casefold() == "skill":
        return f"{prefix}/SKILL.md" if prefix else "SKILL.md"
    return raw


class EvolutionReviewScopeBuilder:
    """Build bounded review materials for active evolution review scopes."""

    def __init__(
        self,
        *,
        trajectory_provider: Callable[[], Trajectory | None],
        materials_builder: Callable[[Trajectory | None], dict[str, Any]],
    ) -> None:
        self._trajectory_provider = trajectory_provider
        self._materials_builder = materials_builder

    def build_scoped_materials(self) -> dict[str, Any]:
        return self._materials_builder(self._trajectory_provider())


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


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
    _SKILL_MD_FILE_READ_TOOLS = frozenset({"read", "read_file", "read_file_stream"})
    _EXPERIENCE_RECORD_HEADING_RE = re.compile(r"#+\s*\[([A-Za-z0-9_-]+)\]")
    # SKILL.md Evolution Index links look like: evolution/instructions.md#ev_037dea95
    _EXPERIENCE_INDEX_ID_RE = re.compile(r"(?:#|/)(ev_[A-Za-z0-9_-]+)\b")
    _SUBJECT_LABELS: dict[str, str] = {
        "skill": "skill",
        "swarm-skill": "swarm skill",
    }
    _subject_kind_default: str = "skill"

    def __init__(
        self,
        skills_dir: Union[str, List[str]],
        *,
        llm: Model,
        model: str,
        auto_save: bool = True,
        review_runtime: EvolutionReviewRuntime,
        language: str = "cn",
        subject_kind: str = "skill",
        trajectory_store: Optional[TrajectoryStore] = None,
        eval_interval: int = 5,
        evolution_total_timeout_secs: float = _DEFAULT_EVOLUTION_TOTAL_TIMEOUT_SECS,
        generate_records_llm_policy: LLMInvokePolicy = GENERATE_RECORDS_LLM_POLICY,
        evaluate_llm_policy: LLMInvokePolicy = EVALUATE_LLM_POLICY,
        simplify_llm_policy: LLMInvokePolicy = SIMPLIFY_LLM_POLICY,
        two_stage: bool = True,
        review_agent_max_iterations: int = 25,
        sharing_config: Optional[Dict[str, Any]] = None,
        disabled_skills: Optional[Union[str, List[str]]] = None,
        evolution_trigger: EvolutionTriggerPoint = EvolutionTriggerPoint.AFTER_INVOKE,
        async_evolution: bool = True,
        max_concurrent_evolution: int = 1,
        fuzzy_review: Optional[bool] = None,
        review_trigger: Optional[bool] = None,
        fuzzy_review_interval: int = 5,
    ) -> None:
        """Initialize SkillEvolutionRail.

        Args:
            skills_dir: Directory or list of directories containing skill definitions
            llm: LLM client for experience generation
            model: Model name for experience generation
            auto_save: Whether to auto-save generated experiences (default True)
            review_runtime: Externally-managed active-review runtime. Required. Shared instances enable
                cross-rail review state and keep rail-local orchestration stateless.
            language: Language for experience generation ("cn" or "en")
            trajectory_store: Optional trajectory store (inherited from EvolutionRail)
            eval_interval: Number of conversations between async evaluations
            sharing_config: Optional cross-user sharing settings (enabled, hub_path, etc.)
            disabled_skills: Optional deny-list of skill names excluded from self-optimization.
                Supports a single skill name (str) or multiple names (list[str]).
            review_trigger: Whether to periodically enqueue review self-check follow-ups.
            fuzzy_review: Backward-compatible alias for review_trigger.
            fuzzy_review_interval: Number of non-follow-up task iterations between fuzzy review checks.
        """
        if eval_interval < 1:
            raise ValueError("eval_interval must be >= 1")
        if fuzzy_review_interval < 1:
            raise ValueError("fuzzy_review_interval must be >= 1")
        if review_agent_max_iterations < 1:
            raise ValueError("review_agent_max_iterations must be >= 1")

        super().__init__(
            trajectory_store=trajectory_store,
            evolution_trigger=evolution_trigger,
            async_evolution=async_evolution,
            max_concurrent_evolution=max_concurrent_evolution,
            disabled_skills=disabled_skills,
        )
        self._subject_kind_value = normalize_evolution_subject_kind(subject_kind)
        self._evolution_store = self._make_evolution_store(skills_dir)
        self._evolver = self._make_skill_optimizer(
            llm,
            model,
            language,
            generate_records_llm_policy=generate_records_llm_policy,
            two_stage=two_stage,
        )
        self._scorer = ExperienceScorer(
            llm,
            model,
            language,
            evaluate_llm_policy=evaluate_llm_policy,
            simplify_llm_policy=simplify_llm_policy,
        )
        self._processed_signal_keys: set[tuple[str, ...]] = set()
        self._auto_save = auto_save
        # Optimizer path (for _auto_save=False): memory-staged records until user approval
        self._skill_ops: Dict[str, SkillExperienceOperator] = {}  # skill_name -> operator
        self._generate_records_llm_policy = generate_records_llm_policy
        self._two_stage = two_stage
        self._evaluate_llm_policy = evaluate_llm_policy
        self._simplify_llm_policy = simplify_llm_policy
        self._evolution_total_timeout_secs = evolution_total_timeout_secs
        # request_id → PendingChange: stable per-approval-prompt snapshot batches
        self._pending_approval_snapshots: Dict[str, PendingChange] = {}
        # Governance staging: request_id → {kind, skill_name, actions/new_body}
        self._pending_governance: Dict[str, Dict[str, Any]] = {}
        self._language = language
        self._review_runtime = review_runtime
        self._review_scope_builder = self._make_review_scope_builder()
        self._evolution_tools: list[Any] = []
        self._agent: Any | None = None
        self._skip_passive_evolution_this_invoke = False
        self._review_trigger = bool(review_trigger if review_trigger is not None else fuzzy_review or False)
        self._fuzzy_review_interval = fuzzy_review_interval
        self._fuzzy_review_non_followup_count = 0
        self._review_agent_max_iterations = review_agent_max_iterations
        self._manager = self._make_experience_manager()
        self._approval_runtime = EvolutionApprovalRuntime(
            manager=self._manager,
            pending_approval_snapshots=self._pending_approval_snapshots,
        )
        self._online_updater = SingleDimUpdater(self._evolver)
        self._online_orchestrator = self._make_online_orchestrator()
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
        # In-memory snapshot: session_id → skills used in this chat (cross-turn;
        # Session objects are recreated per request so setattr on session is lost).
        self._used_skills_by_session: Dict[str, Set[str]] = {}

    @property
    def subject_kind(self) -> str:
        """Active-review subject kind for this rail (e.g. 'skill', 'swarm-skill')."""
        return getattr(self, "_subject_kind_value", self._subject_kind_default)

    @property
    def subject_label(self) -> str:
        """Human-readable active-review subject label."""
        return self._SUBJECT_LABELS.get(self.subject_kind, self.subject_kind)

    def _resolve_store_subject_payload(self, skill_name: str) -> tuple[bool, dict[str, Any] | None]:
        """Resolve the subject envelope from the store when supported."""
        store = getattr(self, "_evolution_store", None) or getattr(self, "_store", None)
        resolver = getattr(store, "resolve_subject_payload", None)
        if not callable(resolver):
            return False, None
        try:
            payload = resolver(skill_name)
        except Exception:
            logger.debug("[SkillEvolutionRail] could not resolve subject payload for '%s'", skill_name)
            return False, None
        if payload is None:
            return True, None
        if isinstance(payload, dict):
            return True, payload
        return False, None

    def _subject_payload(self, skill_name: str) -> dict[str, Any]:
        """Build the canonical subject envelope for a named skill-like subject."""
        _, payload = self._resolve_store_subject_payload(skill_name)
        if payload is not None:
            return payload
        return {"kind": "skill", "name": skill_name}

    def _make_evolution_store(self, skills_dir: Union[str, List[str]]) -> EvolutionStore:
        """Build the persistent store used by evolution services."""
        return EvolutionStore(skills_dir)

    def _make_skill_optimizer(
        self,
        llm: Model,
        model: str,
        language: str,
        *,
        generate_records_llm_policy: LLMInvokePolicy,
        two_stage: bool,
    ):
        """Build the optimizer used by the online evolution updater."""
        return SkillExperienceOptimizer(
            llm,
            model,
            language,
            generate_records_llm_policy=generate_records_llm_policy,
            two_stage=two_stage,
        )

    def _make_experience_manager(self) -> ExperienceManager:
        """Build the experience manager for this rail subject."""
        return ExperienceManager(
            store=self._evolution_store,
            scorer=self._scorer,
            language=self._language,
            skill_ops=self._skill_ops,
            pending_approval_snapshots=self._pending_approval_snapshots,
            pending_governance=self._pending_governance,
            subject_kind=self.subject_kind,
        )

    def _make_review_scope_builder(self) -> EvolutionReviewScopeBuilder:
        """Build the active review material builder for this rail subject."""
        return EvolutionReviewScopeBuilder(
            trajectory_provider=lambda: self._build_trajectory(),
            materials_builder=build_review_scoped_materials,
        )

    def _online_request_id_prefix(self) -> str | None:
        """Return an optional request id prefix for staged online evolution."""
        return None

    def _online_stage_source(self) -> str:
        """Return the stage source recorded for generated online experiences."""
        return "experience_updater"

    def _make_online_orchestrator(self) -> OnlineEvolutionOrchestrator:
        """Build the orchestrator that stages optimizer-generated experiences."""
        return OnlineEvolutionOrchestrator(
            store=self._evolution_store,
            updater=self._online_updater,
            manager=self._manager,
            skill_ops=self._skill_ops,
            request_id_prefix=self._online_request_id_prefix(),
            stage_source=self._online_stage_source(),
        )

    def init(self, agent) -> None:
        """Register evolution tools and the stable review agent."""
        super().init(agent)
        self._agent = agent
        self._evolution_tools = self._register_evolution_tools(agent)
        self._register_evolution_review_agent(agent)

    def uninit(self, agent) -> None:
        """Unregister rail-owned evolution tools."""
        self._unregister_runtime_tools(agent, self._evolution_tools)
        self._evolution_tools = []
        self._unregister_evolution_review_agent(agent)
        self._agent = None
        super().uninit(agent)

    @staticmethod
    def _unregister_evolution_review_agent(agent) -> None:
        deep_config = getattr(agent, "deep_config", None)
        if deep_config is None:
            return
        subagents = list(getattr(deep_config, "subagents", None) or [])
        deep_config.subagents = remove_evolution_review_agent_config(subagents)

    def _register_evolution_tools(self, agent) -> list[Any]:
        """Register rail-owned active-review tools with the agent runtime."""
        agent_id = getattr(getattr(agent, "card", None), "id", None)
        evolution_tools = create_main_evolution_tools(
            query_service=self._manager.experience_query_service,
            submission_service=self._manager.experience_submission_service,
            prepare_scope=self._prepare_evolution_review_scope,
            review_runtime=self._review_runtime,
            language=self._language,
            agent_id=agent_id,
            parent_agent=agent,
        )
        self._register_runtime_tools(agent, evolution_tools)
        return evolution_tools

    def _register_evolution_review_agent(self, agent) -> None:
        """Ensure stable active-review subagent config on agent.deep_config."""
        deep_config = getattr(agent, "deep_config", None)
        if deep_config is None:
            return
        agent_id = getattr(getattr(agent, "card", None), "id", None)
        subagents = remove_evolution_review_agent_config(list(getattr(deep_config, "subagents", None) or []))
        deep_config.subagents = ensure_evolution_review_agent_config(
            subagents,
            build_evolution_review_agent_config(
                runtime=self._review_runtime,
                query_service=self._manager.experience_query_service,
                store=self._evolution_store,
                model=None,
                language=self._language,
                max_iterations=self._review_agent_max_iterations,
                agent_id=agent_id,
            ),
        )

    @staticmethod
    def _is_evolve_review_task_available(agent) -> bool:
        ability_manager = getattr(agent, "ability_manager", None)
        if ability_manager is None or not hasattr(ability_manager, "get"):
            return False
        return ability_manager.get("evolve_review_task") is not None

    def _ensure_evolve_review_task_available(self) -> None:
        """Avoid returning a follow-up prompt that references an absent tool."""
        if getattr(self, "_agent", None) is None:
            return
        if self._is_evolve_review_task_available(self._agent):
            return
        raise RuntimeError(
            "SkillEvolutionRail active review requires evolve_review_task to be registered. "
            "Ensure SkillEvolutionRail.init() registered its rail-owned active review tools."
        )

    def _prepare_evolution_review_scope(
        self,
        *,
        source: str,
        subject: dict[str, Any],
        session_id: str,
        user_intent: str = "",
    ):
        """Create a review scope with rail-owned bounded review materials."""
        self._skip_passive_evolution_this_invoke = True
        return self._review_runtime.create_scope(
            source=source,
            subject=subject,
            session_id=session_id,
            user_intent=user_intent,
            scoped_materials=self._review_scope_builder.build_scoped_materials(),
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
    def two_stage(self) -> bool:
        """Whether experience generation uses the analyzer + formatter pipeline."""
        return self._two_stage

    @property
    def evolution_config(self) -> Dict[str, Any]:
        """Get the effective evolution configuration."""
        return {
            "generate_records_llm_policy": self.generate_records_llm_policy,
            "evaluate_llm_policy": self.evaluate_llm_policy,
            "simplify_llm_policy": self.simplify_llm_policy,
            "evolution_total_timeout_secs": self.evolution_total_timeout_secs,
            "two_stage": self.two_stage,
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
        if self._is_background_run(ctx):
            return False
        if self._skip_passive_evolution_this_invoke:
            logger.info(
                "[SkillEvolutionRail] active evolution activity detected, skip passive evolution"
            )
            return False
        return True

    async def _on_before_invoke(self, ctx: AgentCallbackContext) -> None:
        """Reset per-invoke active evolution state."""
        self._skip_passive_evolution_this_invoke = False

    async def _on_after_task_iteration(self, ctx: AgentCallbackContext) -> None:
        """Periodically enqueue a fuzzy active-review self-check follow-up."""
        if not self._review_trigger:
            return
        if self._task_iteration_followup_blocked(ctx):
            return
        self._fuzzy_review_non_followup_count += 1
        if self._fuzzy_review_non_followup_count < self._fuzzy_review_interval:
            return
        self._fuzzy_review_non_followup_count = 0

        self._enqueue_task_iteration_followup(
            ctx,
            self._build_fuzzy_review_followup_prompt(),
            log_prefix="fuzzy review",
        )

    def _enqueue_task_iteration_followup(
        self,
        ctx: AgentCallbackContext,
        prompt: str,
        *,
        log_prefix: str,
    ) -> bool:
        """Enqueue a follow-up from a normal task-loop iteration."""
        if self._task_iteration_followup_blocked(ctx):
            return False

        agent = getattr(ctx, "agent", None)
        controller = getattr(agent, "_loop_controller", None)
        if controller is None:
            logger.warning(
                "[SkillEvolutionRail] %s follow-up dropped: no TaskLoopController available",
                log_prefix,
            )
            return False

        controller.enqueue_follow_up(prompt)
        return True

    def _task_iteration_followup_blocked(self, ctx: AgentCallbackContext) -> bool:
        """Return whether a task-loop follow-up must be suppressed."""
        if self._is_background_run(ctx):
            return True
        inputs = getattr(ctx, "inputs", None)
        return bool(getattr(inputs, "is_follow_up", False))

    @staticmethod
    def _is_background_run(ctx: AgentCallbackContext) -> bool:
        inputs = getattr(ctx, "inputs", None)
        for method_name in ("is_heartbeat", "is_cron"):
            method = getattr(inputs, method_name, None)
            if callable(method) and method():
                return True

        run_kind = getattr(inputs, "run_kind", None)
        if run_kind is None:
            run_kind = ctx.extra.get("run_kind")
        if run_kind in (RunKind.HEARTBEAT, RunKind.CRON):
            return True
        if isinstance(run_kind, str) and run_kind in {RunKind.HEARTBEAT.value, RunKind.CRON.value}:
            return True

        conversation_id = getattr(inputs, "conversation_id", None)
        return isinstance(conversation_id, str) and conversation_id.startswith(("heartbeat", "cron"))

    def _build_fuzzy_review_followup_prompt(self) -> str:
        """Build the active fuzzy review self-check follow-up prompt."""
        prompt = _FUZZY_REVIEW_PROMPT_EN if self._language == "en" else _FUZZY_REVIEW_PROMPT_CN
        return self._wrap_fuzzy_review_followup_prompt(prompt)

    @staticmethod
    def _wrap_fuzzy_review_followup_prompt(prompt: str) -> str:
        return f"<{_AUTO_SKILL_EVOLUTION_FOLLOW_UP_TAG}>\n{prompt}\n</{_AUTO_SKILL_EVOLUTION_FOLLOW_UP_TAG}>"

    @property
    def auto_save(self) -> bool:
        """Whether auto-save is enabled."""
        return self._auto_save

    @auto_save.setter
    def auto_save(self, value: bool) -> None:
        self._auto_save = bool(value)

    @property
    def experience_manager(self) -> ExperienceManager:
        """Read-only access to the rail-owned experience manager."""
        return self._manager

    @property
    def approval_submission_service(self) -> Any:
        """Submission service used by approval interrupt rails."""
        return self._manager.experience_submission_service

    @property
    def review_runtime(self) -> EvolutionReviewRuntime:
        """Review runtime used by active evolution tools."""
        return self._review_runtime

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

    @property
    def fuzzy_review(self) -> bool:
        """Backward-compatible alias for review_trigger."""
        return self._review_trigger

    @fuzzy_review.setter
    def fuzzy_review(self, value: bool) -> None:
        self._review_trigger = bool(value)

    @property
    def review_trigger(self) -> bool:
        """Whether review self-check follow-ups are enabled."""
        return self._review_trigger

    @review_trigger.setter
    def review_trigger(self, value: bool) -> None:
        self._review_trigger = bool(value)

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

    async def before_model_call(self, ctx: AgentCallbackContext) -> None:
        """Inject the stable evolution protocol section before model calls."""
        builder = getattr(getattr(ctx, "inputs", None), "system_prompt_builder", None)
        if builder is None:
            builder = getattr(getattr(ctx, "agent", None), "system_prompt_builder", None)
        if builder is not None:
            language = str(getattr(builder, "language", "") or self._language)
            builder.add_section(build_evolution_protocol_section(language))

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
        """Track experiences exposed to the agent without modifying tool results.

        Presentation sources (either counts as presented for later scoring):
        1. Reading a detail file under ``evolution/*.md`` with concrete record headings.
        2. Reading ``SKILL.md`` / ``skill_tool`` default entry that contains the Evolution
           Index summaries (agents often follow the summary without opening detail files).
        """
        inputs = ctx.inputs
        if not isinstance(inputs, ToolCallInputs):
            return
        tracked = self._resolve_tracked_skill_name(
            str(inputs.tool_name or ""),
            inputs.tool_args,
            inputs.tool_msg,
        )
        if tracked:
            session = ctx.session if hasattr(ctx, "session") else None
            self._remember_session_used_skill(session, tracked)
        await self._track_presented_from_tool_call(ctx, inputs)

    async def _track_presented_from_tool_call(
        self,
        ctx: AgentCallbackContext,
        inputs: ToolCallInputs,
    ) -> None:
        """Record presented experience IDs from detail reads or SKILL.md index reads."""
        session = ctx.session if hasattr(ctx, "session") else None
        content = self._extract_tool_content(inputs)

        detail_skill = self._detect_experience_detail_read(inputs)
        if detail_skill:
            record_ids = self._extract_presented_record_ids(content)
            if not record_ids:
                return
            await self._experience_tracker.record_presented_records(
                session=session,
                skill_name=detail_skill,
                presentation_snippet="",
                record_ids=record_ids,
            )
            return

        index_skill = self._detect_skill_index_read(inputs)
        if not index_skill:
            return

        record_ids = self._extract_index_record_ids(content)
        if not record_ids:
            return
        await self._experience_tracker.record_presented_records(
            session=session,
            skill_name=index_skill,
            presentation_snippet="",
            record_ids=record_ids,
        )

    async def _evaluate_presented_entries(
        self,
        presented_entries: list[tuple[str, Any, str]],
        messages: list[dict],
    ) -> None:
        """Evaluate presented experiences using conversation snippets rebuilt at after_invoke."""
        await self._experience_tracker.evaluate_presented(
            presented_entries,
            messages=messages,
            build_snippet=self._build_evaluation_snippet,
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
        logger.info("[SkillEvolutionRail] run_evolution called")

        try:
            # Async path: read from snapshot
            session = None
            session_skills: Set[str] = set()
            snapshot_session_id = ""
            if snapshot is not None:
                trajectory = snapshot.get("trajectory", trajectory)
                messages = snapshot["messages"]
                presented_entries = snapshot.get("presented_entries", [])
                session_skills = {
                    str(name).strip()
                    for name in (snapshot.get("session_used_skills") or [])
                    if str(name).strip()
                }
                snapshot_session_id = str(snapshot.get("session_id") or "").strip()
                if snapshot_session_id:
                    session_skills |= {
                        str(name).strip()
                        for name in (self._used_skills_by_session.get(snapshot_session_id) or set())
                        if str(name).strip()
                    }
            # Sync path: read from ctx (backward-compatible)
            elif ctx is not None:
                messages = self._collect_messages_from_trajectory(trajectory)
                session = ctx.session if hasattr(ctx, "session") else None
                presented_entries = self._experience_tracker.consume_eval_state(session)
                session_skills = self._get_session_used_skills(session)
            else:
                logger.warning("[SkillEvolutionRail] run_evolution abort: no snapshot and no ctx")
                return

            logger.info("[SkillEvolutionRail] collected %d messages", len(messages))
            self._emit_progress(
                "started",
                "starting regular skill evolution review for completed conversation",
            )
            if not messages:
                logger.info("[SkillEvolutionRail] no messages, skipping")
                self._emit_progress(
                    "cancelled",
                    "no conversation messages available; cancelling regular skill evolution review",
                )
                await self._evaluate_presented_entries(presented_entries, messages)
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
            traj_skills = detector.collect_skills_from_messages(messages)
            for name in traj_skills:
                self._remember_session_used_skill(session, name)
                if snapshot_session_id:
                    used = self._used_skills_by_session.get(snapshot_session_id)
                    if not isinstance(used, set):
                        used = set()
                        self._used_skills_by_session[snapshot_session_id] = used
                    used.add(name)
                session_skills.add(name)
            session_skills |= self._get_session_used_skills(session)
            logger.info(
                "[SkillEvolutionRail] session used skills=%s (traj=%s)",
                sorted(session_skills),
                traj_skills,
            )

            detected = detector.detect_trajectory_signals(
                trajectory,
                signal_types={"execution_failure", "script_artifact"},
            )
            try:
                feedback_signals = await detector.detect_user_intent(
                    messages,
                    extra_skills=sorted(session_skills),
                )
            except Exception as _fb_exc:
                logger.warning(
                    "[SkillEvolutionRail] user feedback signal detection failed: %s",
                    _fb_exc,
                )
                feedback_signals = []
            if feedback_signals:
                logger.info(
                    "[SkillEvolutionRail] detected %d user feedback signal(s), skills=%s",
                    len(feedback_signals),
                    sorted({s.skill_name for s in feedback_signals if getattr(s, "skill_name", None)}),
                )
                detected = [*detected, *feedback_signals]

            signals: List[EvolutionSignal] = []
            for signal in detected:
                fp = make_signal_fingerprint(signal)
                if fp not in self._processed_signal_keys:
                    self._processed_signal_keys.add(fp)
                    signals.append(signal)
            if len(self._processed_signal_keys) > _MAX_PROCESSED_SIGNAL_KEYS:
                self._processed_signal_keys.clear()
            logger.info("[SkillEvolutionRail] detected %d signal(s)", len(signals))

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
                        "cancelling regular skill evolution review"
                    )
                else:
                    message = (
                        "no skill usage of a regular skill or actionable evolution signal detected; "
                        "cancelling regular skill evolution review"
                    )
                # Evaluate before emitting terminal "cancelled": host watchers treat
                # cancelled as cycle end and must not race with scoring.
                await self._evaluate_presented_entries(presented_entries, messages)
                self._emit_progress("cancelled", message)
                return

            attributed_signal_count = sum(len(skill_signals) for skill_signals in skill_groups.values())
            self._emit_progress(
                "signals_attributed",
                f"detected {attributed_signal_count} signal(s) attributed to {len(skill_groups)} regular skill(s)",
            )

            # Download hub experiences before local generation
            incremental_messages = self._resolve_incremental_messages(messages, ctx, snapshot)
            downloaded_per_skill: dict[str, List[EvolutionRecord]] = {}
            if self.is_sharing_enabled and skill_groups:
                downloaded_per_skill = await self._download_shared_experiences(
                    messages,
                    list(skill_groups.keys()),
                    incremental_messages=incremental_messages,
                )

            # Evolve existing skills (when signals are attributed to known skills)
            skills_dirs = self._resolve_skills_dirs_for_self_evolution()
            deferred_cancelled: List[tuple[str, str]] = []
            for skill_name, skill_signals in skill_groups.items():
                action = resolve_skill_evolution_action(
                    skill_name,
                    default_auto_save=self._auto_save,
                    skills_dirs=skills_dirs,
                )
                if action == "off":
                    logger.info(
                        "[SkillEvolutionRail] selfEvolution=off after attribution, skipping skill=%s",
                        skill_name,
                    )
                    deferred_cancelled.append(
                        (
                            skill_name,
                            f"selfEvolution=off for '{skill_name}'; skipping online evolution",
                        )
                    )
                    continue
                evolved = await self._evolve_skill_with_sharing(
                    skill_name=skill_name,
                    skill_signals=skill_signals,
                    messages=messages,
                    trajectory=trajectory,
                    ctx=ctx,
                    shared_records=downloaded_per_skill.get(skill_name, []),
                    requires_approval=False,
                    # Same as enterprise-dev: review_status mirrors selfEvolution action.
                    review_status=action,
                )
                if evolved.status in {"staged", "auto_approved", "persistence_failed"}:
                    continue
                if evolved.status == "generation_failed":
                    deferred_cancelled.append(
                        (
                            skill_name,
                            f"generation failed for '{skill_name}'",
                        )
                    )
                    continue
                deferred_cancelled.append(
                    (
                        skill_name,
                        f"attributed optimizer signal for '{skill_name}' "
                        "produced no reusable evolution records",
                    )
                )

            # Score presented experiences before any terminal cancelled progress
            # (host watcher treats cancelled as cycle end).
            await self._evaluate_presented_entries(presented_entries, messages)
            for skill_name, message in deferred_cancelled:
                self._emit_progress("cancelled", message, skill_name=skill_name)
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
            True if a user evolution follow-up prompt was created.
        """
        user_intent = self._legacy_user_intent(user_query=user_query, signals=signals, messages=messages)
        result = await self._build_user_evolution_request(skill_name, user_intent)
        return result.has_changes

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

    async def _build_user_evolution_request(
        self,
        skill_name: str,
        user_intent: str = "",
    ) -> EvolutionRequestResult:
        """Build a host-delivered active evolution prompt that requires restricted review first."""
        self._ensure_evolve_review_task_available()
        subject = self._active_review_subject(skill_name)
        if subject is None:
            return EvolutionRequestResult(skill_name=skill_name)
        return EvolutionRequestResult(
            skill_name=skill_name,
            mode="agent_prompt",
            followup_prompt=self._build_active_review_prompt(subject=subject, user_intent=user_intent),
        )

    async def request_user_evolution(
        self,
        skill_name: str,
        user_intent: str = "",
        *,
        auto_approve: bool | None = None,
        max_index_records: int | None = None,
    ) -> EvolutionRequestResult:
        """Compatibility wrapper for active-review user evolution requests.

        Parameters are intentionally lenient to preserve call sites that still
        pass legacy flags. Current active-review behavior is unchanged and
        driven by :meth:`_build_user_evolution_request`.
        """
        del auto_approve
        del max_index_records
        return await self._build_user_evolution_request(skill_name, user_intent)

    async def request_simplify(
        self,
        skill_name: str,
        user_intent: str | None = None,
        *,
        mode: str = "agent_prompt",
        max_index_records: int = 100,
    ) -> SimplifyRequestResult:
        """Build a host-delivered simplify command prompt."""
        if mode != "agent_prompt":
            raise ValueError("regular Skill request_simplify only supports mode='agent_prompt'")
        subject = self._subject_payload(skill_name)
        index = await self._manager.experience_query_service.list_experiences(
            subject,
            min_score=None,
            limit=max_index_records,
            cursor=None,
            target=None,
            section=None,
            query=None,
            sort="score_desc",
        )
        return SimplifyRequestResult(
            skill_name=skill_name,
            mode="agent_prompt",
            followup_prompt=build_simplify_command_prompt(
                subject=subject,
                user_intent=user_intent,
                full_index=index,
                index_complete=not bool(index.get("has_more")),
                language=self._language,
            ),
        )

    async def request_rebuild(
        self,
        skill_name: str,
        user_intent: str | None = None,
        min_score: float = 0.5,
        max_context_records: int = 40,
        max_context_chars: int = 20000,
    ) -> Optional[str]:
        """Compatibility wrapper for rebuild prompt generation."""
        return await self._manager.request_rebuild(
            skill_name,
            user_intent=user_intent,
            min_score=min_score,
            max_context_records=max_context_records,
            max_context_chars=max_context_chars,
        )

    def _active_review_subject(self, skill_name: str) -> dict[str, Any] | None:
        """Build the active-review subject payload for a named skill-like subject."""
        store_supported, payload = self._resolve_store_subject_payload(skill_name)
        if store_supported:
            return payload
        return self._subject_payload(skill_name)

    def _build_active_review_prompt(
        self,
        *,
        subject: dict[str, Any],
        user_intent: str = "",
    ) -> str:
        """Build the host follow-up prompt for active review."""
        return build_evolve_review_command_prompt(
            subject=subject,
            user_intent=user_intent,
            review_agent_name=EVOLUTION_REVIEW_AGENT_NAME,
            language=self._language,
        )

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
        trajectory: Optional[Trajectory] = None,
        user_query: str = "",
        requires_approval: bool,
        review_status: Optional[str] = None,
    ) -> OnlineEvolutionResult:
        """Generate and stage skill experiences through the unified updater flow.

        Returns the structured orchestration result.
        """
        return await self._online_orchestrator.evolve(
            skill_name=skill_name,
            requires_approval=requires_approval,
            signals=signals,
            messages=messages,
            trajectory=trajectory,
            user_query=user_query,
            metadata={"language": self._language},
            source="experience_updater",
            review_status=review_status,
        )

    async def _handle_evolution_from_signals(
        self,
        *,
        skill_name: str,
        signals: List[EvolutionSignal],
        messages: List[dict],
        trajectory: Optional[Trajectory] = None,
        ctx: Optional[AgentCallbackContext],
        user_query: str = "",
        requires_approval: bool,
        emit_host_events: bool = True,
        review_status: Optional[str] = None,
    ) -> OnlineEvolutionResult:
        """Handle optimizer-driven evolution and return the structured orchestration status."""
        if emit_host_events:
            self._emit_progress(
                "optimizing",
                f"optimizing evolution records for '{skill_name}' by adding, merging, or refining experiences",
                skill_name=skill_name,
            )
        result = await self._stage_evolution_from_signals(
            skill_name=skill_name,
            signals=signals,
            messages=messages,
            trajectory=trajectory,
            user_query=user_query,
            requires_approval=requires_approval,
            review_status=review_status,
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
    def _extract_presented_record_ids(cls, content: str) -> list[str]:
        """Extract stable experience record IDs from rendered markdown headings."""
        seen: set[str] = set()
        record_ids: list[str] = []
        for match in cls._EXPERIENCE_RECORD_HEADING_RE.finditer(content):
            record_id = match.group(1)
            if record_id in seen:
                continue
            seen.add(record_id)
            record_ids.append(record_id)
        return record_ids

    @classmethod
    def _extract_index_record_ids(cls, content: str) -> list[str]:
        """Extract experience IDs from SKILL.md Evolution Index summaries/links."""
        if not content:
            return []
        section = content
        start = content.find("<!-- evolution-index-start -->")
        end = content.find("<!-- evolution-index-end -->")
        if start != -1 and end != -1 and end > start:
            section = content[start:end]
        elif "Evolution Experiences" in content:
            section_start = content.find("Evolution Experiences")
            section = content[section_start:]

        seen: set[str] = set()
        record_ids: list[str] = []
        for match in cls._EXPERIENCE_INDEX_ID_RE.finditer(section):
            record_id = match.group(1)
            if record_id in seen:
                continue
            seen.add(record_id)
            record_ids.append(record_id)
        return record_ids

    @staticmethod
    def _is_experience_detail_relative_path(relative_path: str) -> bool:
        """Return True when a skill-relative path points at a persisted experience detail."""
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

    @classmethod
    def _extract_file_path(cls, tool_args: Any) -> str:
        args = cls._extract_tool_args(tool_args)
        file_path = args.get("file_path", "")
        return str(file_path) if file_path else ""

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

    def _detect_skill_index_read(self, inputs: ToolCallInputs) -> Optional[str]:
        """Return skill name when the tool call loads SKILL.md (Evolution Index)."""
        tool_name = str(inputs.tool_name or "")
        args = self._extract_tool_args(inputs.tool_args)

        if tool_name == "skill_tool":
            skill_name = str(args.get("skill_name", "") or "").strip()
            relative_path = str(args.get("relative_file_path") or "SKILL.md").strip()
            if skill_name and self._is_skill_md_path(relative_path):
                return skill_name
            return None

        if not self._is_skill_md_file_read_tool(tool_name):
            return None

        file_path = str(args.get("file_path", "") or "").strip()
        if not file_path:
            return None
        matched = self._SKILL_MD_RE.search(file_path)
        if not matched:
            return None
        return matched.group(1)

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
    def _parse_tool_args_dict(cls, arguments: Any) -> dict[str, Any]:
        return cls._extract_tool_args(arguments)

    @classmethod
    def _is_skill_md_path(cls, relative_path: str) -> bool:
        normalized = _normalize_skill_relative_file_path(relative_path)
        norm = normalized.replace("\\", "/").removeprefix("./").lower()
        return norm == "skill.md" or norm.endswith("/skill.md")

    @classmethod
    def _find_skill_load_anchor(cls, messages: List[dict], skill_name: str) -> int:
        """Return the index of the last SKILL.md load for *skill_name*, or -1."""
        anchor = -1
        for index, msg in enumerate(messages):
            role = msg.get("role", "")
            if role != "assistant":
                continue
            for tool_call in msg.get("tool_calls", []) or []:
                tool = str(tool_call.get("name") or "").lower()
                arguments = tool_call.get("arguments", "")
                if tool == "skill_tool":
                    args = cls._parse_tool_args_dict(arguments)
                    if str(args.get("skill_name") or "").strip() != skill_name:
                        continue
                    rel = _normalize_skill_relative_file_path(str(args.get("relative_file_path") or ""))
                    if cls._is_skill_md_path(rel):
                        anchor = index
                elif cls._is_skill_md_file_read_tool(tool):
                    file_path = cls._extract_file_path(arguments)
                    matched = cls._SKILL_MD_RE.search(file_path)
                    if matched and matched.group(1) == skill_name:
                        anchor = index
        return anchor

    @classmethod
    def _is_skill_md_file_read_tool(cls, tool_name: str) -> bool:
        """True for known filesystem read tools that may load SKILL.md by path."""
        return tool_name.lower() in cls._SKILL_MD_FILE_READ_TOOLS

    @classmethod
    def _format_messages_snippet(
        cls,
        messages: List[dict],
        *,
        start: int = 0,
        max_messages: int = _EVAL_SNIPPET_MAX_MESSAGES,
        max_content_chars: int = _EVAL_SNIPPET_POST_PRESENT_MAX_CHARS,
    ) -> str:
        """Format conversation messages into a scorer-friendly snippet."""
        lines: List[str] = []
        window = messages[start:][-max_messages:]
        for msg in window:
            role = msg.get("role", "unknown")
            content = str(msg.get("content") or "")[:max_content_chars]
            if content:
                lines.append(f"[{role}] {content}")
            if role == "assistant":
                for tool_call in msg.get("tool_calls", []) or []:
                    tool = str(tool_call.get("name") or "")
                    args = str(tool_call.get("arguments") or "")[:max_content_chars]
                    if tool:
                        lines.append(f"[assistant/tool_call] {tool} {args}")
        return "\n".join(lines)

    @classmethod
    def _build_evaluation_snippet(cls, messages: List[dict], skill_name: str) -> str:
        """Build post-presentation snippet: from last SKILL.md load through turn end."""
        if not messages:
            return ""
        anchor = cls._find_skill_load_anchor(messages, skill_name)
        start = anchor if anchor >= 0 else max(0, len(messages) - _EVAL_SNIPPET_MAX_MESSAGES)
        return cls._format_messages_snippet(messages, start=start)

    @classmethod
    def _parse_messages(cls, messages: List[Any]) -> List[dict]:
        return cls._normalize_callback_messages(messages)

    async def _snapshot_for_evolution(
        self,
        trajectory: Trajectory,
        ctx: AgentCallbackContext,
    ) -> Optional[dict]:
        """Phase 1: Collect messages while ctx is alive."""
        snapshot = await super()._snapshot_for_evolution(trajectory, ctx)
        if snapshot is None:
            return None

        messages = snapshot["messages"]
        if not messages:
            return None

        session_id = ctx.inputs.conversation_id if ctx.inputs else ""
        session = ctx.session if hasattr(ctx, "session") else None
        presented_entries = self._experience_tracker.consume_eval_state(session)

        # Refresh session-used skills while ctx/session is still alive.
        detector = SignalDetector()
        for name in detector.collect_skills_from_messages(messages):
            self._remember_session_used_skill(session, name)
        session_key = self._resolve_used_skills_session_key(session) or str(session_id or "").strip()
        session_used_skills = sorted(self._get_session_used_skills(session))
        if session_key and not session_used_skills:
            session_used_skills = sorted(
                str(name).strip()
                for name in (self._used_skills_by_session.get(session_key) or set())
                if str(name).strip()
            )

        snapshot.update(
            {
                "session_id": session_key or session_id,
                "presented_entries": presented_entries,
                "skill_name": "skill-evolution",
                "incremental_messages": self._resolve_incremental_messages(messages, ctx, None),
                "session_used_skills": session_used_skills,
            }
        )
        return snapshot

    def _resolve_skills_dirs_for_self_evolution(self) -> Optional[List[Any]]:
        """Return EvolutionStore base dirs for capabilities.json lookup, if available."""
        raw = getattr(self._evolution_store, "base_dirs", None)
        if raw is None:
            return None
        try:
            return list(raw)
        except TypeError:
            return None

    @staticmethod
    def _resolve_used_skills_session_key(session: Any) -> Optional[str]:
        """Resolve stable key for in-memory used-skills snapshot (no disk)."""
        if session is None:
            return None
        getter = getattr(session, "get_session_id", None)
        if callable(getter):
            try:
                sid = str(getter() or "").strip()
            except Exception:
                sid = ""
            if sid:
                return sid
        # Tests / callers without get_session_id: same object only.
        return f"__obj__:{id(session)}"

    def _get_session_used_skills(self, session: Any) -> Set[str]:
        """Return skill names used earlier for this session_id (cross-turn)."""
        key = self._resolve_used_skills_session_key(session)
        if not key:
            return set()
        raw = self._used_skills_by_session.get(key)
        if not isinstance(raw, set):
            return set()
        return {str(name).strip() for name in raw if str(name).strip()}

    def _remember_session_used_skill(self, session: Any, skill_name: str) -> None:
        """Record a skill as used for this session_id (in-memory, no disk)."""
        name = (skill_name or "").strip()
        key = self._resolve_used_skills_session_key(session)
        if not key or not name:
            return
        used = self._used_skills_by_session.get(key)
        if not isinstance(used, set):
            used = set()
            self._used_skills_by_session[key] = used
        if name not in used:
            used.add(name)
            logger.info(
                "[SkillEvolutionRail] remember session used skill=%s session_id=%s total=%d",
                name,
                key,
                len(used),
            )

    @classmethod
    def _resolve_tracked_skill_name(
        cls,
        tool_name: str,
        tool_args: Any,
        tool_msg: Any = None,
    ) -> Optional[str]:
        """Resolve skill name from skill_tool / skill_complete for session tracking."""
        name = (tool_name or "").lower()
        args = cls._parse_tool_args_dict(tool_args)
        if name in ("skill_tool", "skill_complete") or name.endswith(".skill_tool"):
            skill = str(args.get("skill_name") or "").strip()
            if skill:
                return skill
        return None

    # ── Governance commands (shared by 1D and team skills) ──

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

    async def list_experiences(
        self,
        skill_name: str,
        *,
        min_score: Optional[float] = None,
        limit: int = 100,
        cursor: Optional[str] = None,
        target: Optional[str] = None,
        section: Optional[str] = None,
        query: Optional[str] = None,
        sort: str = "score_desc",
    ) -> dict[str, Any]:
        """Host query API for structured experience index entries."""
        return await self._manager.experience_query_service.list_experiences(
            self._subject_payload(skill_name),
            min_score=min_score,
            limit=limit,
            cursor=cursor,
            target=target,
            section=section,
            query=query,
            sort=sort,
        )

    async def rollback_skill(self, skill_name: str, version: Optional[str] = None) -> bool:
        """Rollback skill to an archived SemVer pair (no approval required).

        Restores the archived ``SKILL.md`` body and always clears live
        ``evolutions.json`` (empty entries, retained version). The
        pre-rollback snapshot archives an empty paired ``evolutions.v*.json``
        (all evolution archives are empty by design).
        """
        from openjiuwen.agent_evolving.experience.archive import EvolutionArchiveService

        store = self._evolution_store
        if not store.skill_exists(skill_name):
            return False

        archive_service = EvolutionArchiveService(store=store)
        target = version or "latest"
        restored = await archive_service.rollback_to_pair(skill_name, target, prune=False)
        if not restored:
            logger.warning(
                "[SkillEvolutionRail] rollback failed for %s -> %s",
                skill_name,
                target,
            )
            return False
        logger.info("[SkillEvolutionRail] rollback completed for %s -> %s", skill_name, target)
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
