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
from typing import Any, Callable, Dict, List, Optional, Union

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
from openjiuwen.agent_evolving.signal import (
    EvolutionSignal,
    SignalDetector,
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
from openjiuwen.agent_evolving.prompts.sections import build_evolution_protocol_section
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
from openjiuwen.harness.rails.subagent import SubagentRail
from openjiuwen.agent_evolving.tools import create_main_evolution_tools

_MAX_PROCESSED_SIGNAL_KEYS = 500
_DEFAULT_EVOLUTION_TOTAL_TIMEOUT_SECS = 600.0
_NON_REGULAR_SKILL_KINDS = {"team-skill", "swarm-skill"}
_FUZZY_REVIEW_PROMPT_CN = (
    "请检查近期上下文是否有可复用 Skill 经验线索；这不是处理本次错误的请求。\n"
    "当用户纠正上一轮 agent 的执行步骤、顺序、确认条件或交付流程时，"
    "先判定为高优先级可复用流程线索；该反馈可作为演进候选，但不表示必然演进。\n"
    "只有问题可归入已使用或可从近期任务上下文推断的相关 Skill、可归因到 Skill "
    "指令缺口、改法可复用且非重复时，才算可演进机会。\n"
    "执行失败本身不作为本次处理对象；\n"
    "它可作为判断 Skill 是否缺少 precheck、fallback、verification 或排错指引的线索。\n"
    "当用户用“你应该/应该先/先...再.../确认后再.../不要直接...”等规则化表达，"
    "且内容是可复用工作流或可复用执行规则，先确认该建议是否要沉淀为相关 "
    "Skill 经验。\n"
    "一次性偏好、不可复用、已有经验覆盖或无法归入相关 Skill 场景时，不要询问演进。\n"
    "没有可演进机会时，不要打扰用户；如果你需要回复本次自检，"
    "只能说：本次技能演进自检未发现需要更新的 Skill。\n"
    "有可演进机会时，用一句话询问：这条反馈可以沉淀为以后处理同类任务时的"
    "流程经验，是否需要我记录到相关 Skill？\n"
    "用户确认后，才使用 prepare_skill_evolution、evolve_review_task、evolve_skill_experiences。\n"
    "不要直接编辑 Skill 文件；未确认时不要提交演进变更。"
)
_FUZZY_REVIEW_PROMPT_EN = """Check recent context for reusable Skill lessons; this is not a request to handle the
current error.
When the user corrects prior-agent execution steps, order, confirmation gates, or delivery flow,
it is a high-priority reusable workflow clue (not a mandatory evolution signal).
Treat something as an evolution opportunity only when it is linked to a used or inferable Skill context from recent
task context, attribution to a Skill-guidance gap, reusable guidance, and no duplicate coverage.
Use concrete execution failures only as evidence when deciding whether the Skill lacks precheck, fallback,
verification, or troubleshooting guidance.
If the user gives reusable rule-style guidance (for example “you should”, “should first”, “do X then Y”,
“confirm before doing”, or “do not do this directly”) and it is about a reusable workflow or execution
rule, first confirm whether to distill it as a Skill experience (related Skill).
Do not ask to evolve for one-off preferences, non-reusable feedback, duplicate coverage, or feedback that cannot
fit any related Skill context.
If there is no evolution opportunity, do not bother the user; if you need to respond to this self-check, only say:
This skill evolution self-check did not find any Skill that needs updating.
If there is an evolution opportunity, ask this sentence in one line: This feedback can be distilled into a workflow
lesson for similar future tasks. Should I record it in the related Skill?
Only after user confirmation, use prepare_skill_evolution, evolve_review_task, and evolve_skill_experiences.
Do not edit Skill files directly, and do not submit evolution changes before confirmation."""


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
    _EXPERIENCE_RECORD_HEADING_RE = re.compile(r"#+\s*\[([A-Za-z0-9_-]+)\]")
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
        auto_scan: bool = True,
        auto_save: bool = False,
        review_runtime: EvolutionReviewRuntime,
        language: str = "cn",
        subject_kind: str = "skill",
        trajectory_store: Optional[TrajectoryStore] = None,
        eval_interval: int = 5,
        evolution_total_timeout_secs: float = _DEFAULT_EVOLUTION_TOTAL_TIMEOUT_SECS,
        generate_records_llm_policy: LLMInvokePolicy = GENERATE_RECORDS_LLM_POLICY,
        evaluate_llm_policy: LLMInvokePolicy = EVALUATE_LLM_POLICY,
        simplify_llm_policy: LLMInvokePolicy = SIMPLIFY_LLM_POLICY,
        review_agent_max_iterations: int = 10,
        sharing_config: Optional[Dict[str, Any]] = None,
        disabled_skills: Optional[Union[str, List[str]]] = None,
        evolution_trigger: EvolutionTriggerPoint = EvolutionTriggerPoint.AFTER_INVOKE,
        async_evolution: bool = True,
        max_concurrent_evolution: int = 1,
        fuzzy_review: bool = True,
        fuzzy_review_interval: int = 5,
    ) -> None:
        """Initialize SkillEvolutionRail.

        Args:
            skills_dir: Directory or list of directories containing skill definitions
            llm: LLM client for experience generation
            model: Model name for experience generation
            auto_scan: Whether to auto-detect evolution signals
            auto_save: Whether to auto-save generated experiences
            review_runtime: Externally-managed active-review runtime. Required. Shared instances enable
                cross-rail review state and keep rail-local orchestration stateless.
            language: Language for experience generation ("cn" or "en")
            trajectory_store: Optional trajectory store (inherited from EvolutionRail)
            eval_interval: Number of conversations between async evaluations
            sharing_config: Optional cross-user sharing settings (enabled, hub_path, etc.)
            disabled_skills: Optional deny-list of skill names excluded from self-optimization.
                Supports a single skill name (str) or multiple names (list[str]).
            fuzzy_review: Whether to periodically enqueue active fuzzy review self-check follow-ups.
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
        self._review_runtime = review_runtime
        self._review_scope_builder = self._make_review_scope_builder()
        self._evolution_tools: list[Any] = []
        self._agent: Any | None = None
        self._skip_auto_scan_this_invoke = False
        self._fuzzy_review = bool(fuzzy_review)
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
    ):
        """Build the optimizer used by the online evolution updater."""
        return SkillExperienceOptimizer(
            llm,
            model,
            language,
            generate_records_llm_policy=generate_records_llm_policy,
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
        self._refresh_initialized_subagent_rails(agent)

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
        subagents = list(getattr(deep_config, "subagents", None) or [])
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
    def _refresh_initialized_subagent_rails(agent) -> None:
        """Refresh already-initialized SubagentRail available-agents metadata."""
        subagent_rails = [rail for rail in getattr(agent, "_registered_rails", []) if isinstance(rail, SubagentRail)]
        for rail in subagent_rails:
            refresh_available_agents = getattr(rail, "refresh_available_agents", None)
            if callable(refresh_available_agents):
                refresh_available_agents(agent)

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
        self._skip_auto_scan_this_invoke = True
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
        if not self._auto_scan:
            return False
        if self._skip_auto_scan_this_invoke:
            logger.info("[SkillEvolutionRail] active evolution activity detected, skip passive auto_scan")
            return False
        return True

    async def _on_before_invoke(self, ctx: AgentCallbackContext) -> None:
        """Reset per-invoke active evolution state."""
        self._skip_auto_scan_this_invoke = False

    async def _on_after_task_iteration(self, ctx: AgentCallbackContext) -> None:
        """Periodically enqueue a fuzzy active-review self-check follow-up."""
        if not self._fuzzy_review:
            return
        inputs = getattr(ctx, "inputs", None)
        if getattr(inputs, "is_follow_up", False):
            return
        self._fuzzy_review_non_followup_count += 1
        if self._fuzzy_review_non_followup_count < self._fuzzy_review_interval:
            return
        self._fuzzy_review_non_followup_count = 0

        agent = getattr(ctx, "agent", None)
        controller = getattr(agent, "_loop_controller", None)
        if controller is None:
            logger.warning("[SkillEvolutionRail] fuzzy review follow-up dropped: no TaskLoopController available")
            return

        controller.enqueue_follow_up(self._build_fuzzy_review_followup_prompt())

    def _build_fuzzy_review_followup_prompt(self) -> str:
        """Build the active fuzzy review self-check follow-up prompt."""
        return _FUZZY_REVIEW_PROMPT_EN if self._language == "en" else _FUZZY_REVIEW_PROMPT_CN

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
    def auto_scan(self) -> bool:
        """Whether auto-scan is enabled."""
        return self._auto_scan

    @auto_scan.setter
    def auto_scan(self, value: bool) -> None:
        self._auto_scan = bool(value)

    @property
    def fuzzy_review(self) -> bool:
        """Whether active fuzzy-review follow-ups are enabled."""
        return self._fuzzy_review

    @fuzzy_review.setter
    def fuzzy_review(self, value: bool) -> None:
        self._fuzzy_review = bool(value)

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
            builder.add_section(build_evolution_protocol_section(language, fuzzy_review=self._fuzzy_review))

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
                "starting regular skill evolution review for completed conversation",
            )
            if not messages:
                logger.info("[SkillEvolutionRail] no messages, skipping")
                self._emit_progress(
                    "cancelled",
                    "no conversation messages available; cancelling regular skill evolution review",
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
            detected = detector.detect_trajectory_signals(
                trajectory,
                signal_types={"execution_failure", "script_artifact"},
            )
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
                self._emit_progress("cancelled", message)
                await self._experience_tracker.evaluate_presented(presented_entries)
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
            for skill_name, skill_signals in skill_groups.items():
                generated = await self._evolve_skill_with_sharing(
                    skill_name=skill_name,
                    skill_signals=skill_signals,
                    messages=messages,
                    trajectory=trajectory,
                    ctx=ctx,
                    shared_records=downloaded_per_skill.get(skill_name, []),
                    requires_approval=not self._auto_save,
                )
                if not generated:
                    self._emit_progress(
                        "cancelled",
                        f"attributed optimizer signal for '{skill_name}' produced no reusable evolution records",
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
            metadata={},
            source="experience_updater",
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
        return cls._normalize_callback_messages(messages)

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
