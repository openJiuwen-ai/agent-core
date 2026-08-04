# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Shared data contracts for the auto-coordinating harness."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any, Protocol, TypeAlias

CaseMapping: TypeAlias = dict[str, Any]


class OrchestratorPhase(str, Enum):
    """Lifecycle phases persisted in ``orchestrator_context.yaml``."""

    INITIALIZING = "initializing"
    GENERATING_DATASET = "generating_dataset"
    EVALUATING = "evaluating"
    OPTIMIZING_TEAM_SKILL = "optimizing_team_skill"
    OPTIMIZING_MEMBER = "optimizing_member"
    SAVING_CHECKPOINT = "saving_checkpoint"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class DatasetArtifact:
    """Filesystem-backed JSON dataset artifact consumed by the evaluator."""

    dataset_id: str
    dataset_dir: str
    dataset_files: list[str] = field(default_factory=list)
    cases: int | None = None


@dataclass(frozen=True, slots=True)
class DatasetCurationArtifact:
    """Replay dataset artifact mined from an evaluation result."""

    status: str
    eval_ref_path: str
    output_dir: str
    dataset_file: str = ""
    targeted_seed_file: str = ""
    report_path: str = ""
    accepted_cases: int = 0
    rejected_cases: int = 0


@dataclass(frozen=True, slots=True)
class CurrentArtifactRefs:
    """Current artifact references stored in the orchestrator context."""

    dataset: DatasetArtifact | None = None
    team_skill_ref_path: str = ""
    harness_refs_path: str = ""
    harness_refs: dict[str, str] = field(default_factory=dict)
    eval_ref_path: str | None = None


@dataclass(frozen=True, slots=True)
class BestArtifactRefs:
    """Best known artifact references and score for a run."""

    team_skill_ref_path: str | None = None
    harness_refs_path: str | None = None
    harness_refs: dict[str, str] = field(default_factory=dict)
    eval_ref_path: str | None = None
    score: float | None = None


@dataclass(frozen=True, slots=True)
class EvaluationHistoryItem:
    """One evaluation result recorded in run history."""

    eval_ref_path: str
    phase: str
    score: float | None = None


@dataclass(frozen=True, slots=True)
class BatchOptimizationResult:
    """Current artifact refs produced by optimizing one dataset batch."""

    team_skill_ref_path: str
    harness_refs_path: str
    eval_ref_paths: list[str] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class TeamSkillOptimizationHistoryItem:
    """One Team Skill optimization event recorded in run history."""

    before_team_skill_ref_path: str
    after_team_skill_ref_path: str
    eval_ref_path: str


@dataclass(frozen=True, slots=True)
class MemberOptimizationHistoryItem:
    """One member harness optimization event recorded in run history."""

    before_harness_refs_path: str
    after_harness_refs_path: str
    eval_ref_path: str
    role: str = ""
    before_role_harness_ref_path: str = ""
    after_role_harness_ref_path: str = ""


@dataclass(frozen=True, slots=True)
class CallAuditRecord:
    """Auditable module call record persisted by the orchestrator."""

    call_id: str
    module: str
    method: str
    inputs: dict[str, Any] = field(default_factory=dict)
    output: dict[str, Any] = field(default_factory=dict)
    status: str = "pending"
    error: str = ""


@dataclass(frozen=True, slots=True)
class RunStrategyMetadata:
    """Scheduling strategy metadata persisted with a run context."""

    evaluation_strategy: str = "hybrid"
    coordination_strategy: str = "team_first_single_pass"
    promotion_policy: str = "epoch_full_evaluation"
    full_evaluation_enabled: bool = True
    strategy_name: str = "hybrid_team_first_single_pass"
    enabled_at: datetime = field(default_factory=lambda: datetime.now(UTC).astimezone())


@dataclass(frozen=True, slots=True)
class OrchestratorHistory:
    """Optimization history accumulated across epochs."""

    evaluations: list[EvaluationHistoryItem] = field(default_factory=list)
    team_skill_optimizations: list[TeamSkillOptimizationHistoryItem] = field(default_factory=list)
    member_optimizations: list[MemberOptimizationHistoryItem] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class OrchestratorRunContext:
    """Source-of-truth state represented by ``orchestrator_context.yaml``."""

    task_id: str
    task: str
    context_path: str
    checkpoint_dir: str
    phase: OrchestratorPhase = OrchestratorPhase.INITIALIZING
    epoch: int = 0
    created_at: datetime | None = None
    updated_at: datetime | None = None
    current: CurrentArtifactRefs = field(default_factory=CurrentArtifactRefs)
    best: BestArtifactRefs = field(default_factory=BestArtifactRefs)
    history: OrchestratorHistory = field(default_factory=OrchestratorHistory)
    strategy: RunStrategyMetadata = field(default_factory=RunStrategyMetadata)
    calls: list[CallAuditRecord] = field(default_factory=list)
    last_checkpoint_at: datetime | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class EvaluationInvocation:
    """Evaluator invocation assembled by the orchestrator."""

    dataset: DatasetArtifact
    team_skill_ref_path: str
    harness_refs_path: str
    output_dir: str
    phase: str


@dataclass(frozen=True, slots=True)
class EvaluationCaseTraceRef:
    """Trace and result references for one evaluated case."""

    case_id: str
    case_path: str
    trace_path: str
    result_path: str
    status: str
    score: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class EvaluationResultAnalysisInvocation:
    """Evaluation-result analyzer invocation assembled by the orchestrator."""

    eval_ref_path: str
    case_results_dir: str
    case_traces_dir: str
    team_skill_ref_path: str
    harness_refs_path: str
    output_dir: str
    source_stage: str = ""
    prior_candidate_feedback: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class TeamIssue:
    """A structured issue found in the current Team behavior."""

    issue_id: str
    category: str
    severity: str
    summary: str
    affected_cases: list[str] = field(default_factory=list)
    evidence: list[dict[str, Any]] = field(default_factory=list)
    suspected_team_scope: str = ""
    optimization_target: str = ""
    target_members: list[str] = field(default_factory=list)
    recommendation: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class EvaluationResultAnalysisArtifact:
    """Filesystem-backed Team issue analysis artifact."""

    analysis_id: str
    analysis_ref_path: str
    issues_path: str = ""
    issues: list[TeamIssue] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class TeamSkillOptimizationInvocation:
    """Team Skill optimizer invocation assembled by the orchestrator."""

    eval_ref_path: str
    team_skill_ref_path: str
    output_dir: str


@dataclass(frozen=True, slots=True)
class MemberOptimizationInvocation:
    """Member optimizer invocation assembled by the orchestrator."""

    eval_ref_path: str
    analysis_result_path: str
    harness_refs_path: str
    output_dir: str


@dataclass(frozen=True, slots=True)
class ActionDefinition:
    """Declarative member-harness optimization action."""

    name: str
    group: str
    operation: str
    function: str
    purpose: str
    optimizable_modules: list[str] = field(default_factory=list)
    requires_search: bool = False
    requires_install: bool = False
    dependency_resources: dict[str, str] = field(default_factory=dict)
    parameters_schema: dict[str, Any] = field(default_factory=dict)
    is_destructive: bool = False
    validation_rules: list[str] = field(default_factory=list)


class EvaluationScript(Protocol):
    """Extension point for evaluator case execution scripts."""

    @property
    def name(self) -> str:
        """Stable script name used in evaluator config."""
        ...

    async def setup(self, config_path: str) -> None:
        """TODO: prepare external resources required by this script."""
        ...

    async def evaluate_case(
        self,
        team_config_path: str,
        case_path: str,
        context_path: str,
    ) -> str:
        """TODO: run one case and return the case result artifact path."""
        ...

    async def teardown(self) -> None:
        """TODO: release script resources."""
        ...


__all__ = [
    "ActionDefinition",
    "BatchOptimizationResult",
    "BestArtifactRefs",
    "CallAuditRecord",
    "CaseMapping",
    "CurrentArtifactRefs",
    "DatasetArtifact",
    "EvaluationHistoryItem",
    "EvaluationInvocation",
    "EvaluationCaseTraceRef",
    "EvaluationResultAnalysisArtifact",
    "EvaluationResultAnalysisInvocation",
    "EvaluationScript",
    "MemberOptimizationHistoryItem",
    "MemberOptimizationInvocation",
    "OrchestratorHistory",
    "OrchestratorPhase",
    "OrchestratorRunContext",
    "RunStrategyMetadata",
    "TeamIssue",
    "TeamSkillOptimizationHistoryItem",
    "TeamSkillOptimizationInvocation",
]
