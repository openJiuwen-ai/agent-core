# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Data contracts for member harness optimization.

Schema extends the spec's Section 3 and 5 (rule.md + design.md v1.10).
All dataclasses use frozen=True, slots=True.
All required (non-default) fields come before optional (default) fields.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from openjiuwen.rsi.harness_rsi.schema import ActionDefinition, TeamIssue  # noqa: F401

# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class MechanismType(str, Enum):
    """Role-internal failure mechanism type taxonomy."""

    PROMPT = "prompt"
    INSTRUCTION = "instruction"
    WORKFLOW = "workflow"
    SKILL = "skill"
    TOOL = "tool"
    MCP = "mcp"
    MEMORY = "memory"
    CONTEXT = "context"
    CONFIG = "config"
    RAIL = "rail"
    KNOWLEDGE = "knowledge"
    INSUFFICIENT_ROLE_EVIDENCE = "insufficient_role_evidence"


class OptimizationSurface(str, Enum):
    """Concrete ExpertHarness surface selected for the optimization action."""

    NONE = "none"
    PROMPT_SECTION = "prompt_section"
    IDENTITY = "identity"
    SOUL = "soul"
    SKILL = "skill"
    TOOL = "tool"
    RAIL = "rail"


class FailureSignature(str, Enum):
    """Failure signature taxonomy for role-internal mechanism attribution."""

    REPEATED_FAILURE_LOOP = "repeated_failure_loop"
    TOOL_CALL_FAILURE = "tool_call_failure"
    STALLED_OBJECTIVE = "stalled_objective"
    MISSED_EXPLORATION_OR_CAPABILITY = "missed_exploration_or_capability"
    STALE_MEMORY_OR_CONTEXT = "stale_memory_or_context"
    SKILL_CODE_FAILURE = "skill_code_failure"
    SUBAGENT_OR_WORKFLOW_HANDOFF_FAILURE = "subagent_or_workflow_handoff_failure"
    PROMPT_INSTRUCTION_MISMATCH = "prompt_instruction_mismatch"
    CONFIG_OR_RAIL_MISMATCH = "config_or_rail_mismatch"
    INSUFFICIENT_ROLE_EVIDENCE = "insufficient_role_evidence"


# ---------------------------------------------------------------------------
# Candidate Roles
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class MemberRoleCandidate:
    """A candidate role for member optimization, resolved from harness refs."""

    role: str
    harness_ref_path: str
    member_name: str = ""
    description: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Role Attribution (Step 1)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RoleIssueAttribution:
    """Attribution of a TeamIssue to a specific role."""

    issue_id: str
    role: str
    harness_ref_path: str
    confidence: float
    evidence: list[dict[str, Any]] = field(default_factory=list)
    trace_refs: list[dict[str, str]] = field(default_factory=list)
    rationale: str = ""
    member_name: str = ""
    role_output_refs: list[dict[str, str]] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class UnassignedMemberIssue:
    """A TeamIssue that could not be attributed to a specific role."""

    issue_id: str
    reason: str
    evidence: list[dict[str, Any]] = field(default_factory=list)
    trace_refs: list[dict[str, str]] = field(default_factory=list)
    rationale: str = ""


@dataclass(frozen=True, slots=True)
class RoleAttributionReport:
    """Step 1 role attribution report."""

    attribution_id: str = ""
    source_eval_ref_path: str = ""
    source_analysis_result_path: str = ""
    harness_refs_path: str = ""
    candidate_roles: list[MemberRoleCandidate] = field(default_factory=list)
    assigned_role_issues: list[RoleIssueAttribution] = field(default_factory=list)
    unassigned_issues: list[UnassignedMemberIssue] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Mechanism Attribution (Step 2)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RoleMechanismAttribution:
    """Role-internal mechanism attribution for one issue."""

    issue_id: str
    role: str
    mechanism_type: str
    failure_signature: str
    confidence: float
    optimization_surface: str = ""
    evidence: list[dict[str, Any]] = field(default_factory=list)
    evidence_refs: list[dict[str, str]] = field(default_factory=list)
    rationale: str = ""


@dataclass(frozen=True, slots=True)
class MechanismAttributionReport:
    """Step 2 mechanism attribution report."""

    mechanism_attribution_id: str = ""
    source_role_attribution_path: str = ""
    role_mechanisms: dict[str, list[RoleMechanismAttribution]] = field(default_factory=dict)
    skipped_roles: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Member Selection
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class MemberOptimizationTarget:
    """A role selected for harness optimization this run."""

    role: str
    harness_ref_path: str
    attributed_issue_ids: list[str] = field(default_factory=list)
    confidence: float = 0.0
    reason: str = ""
    evidence_refs: list[dict[str, str]] = field(default_factory=list)
    mechanism_types: list[str] = field(default_factory=list)
    optimization_surfaces: list[str] = field(default_factory=list)
    member_name: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class UnselectedAttribution:
    """An assigned-but-not-selected role issue attribution."""

    issue_id: str
    role: str
    reason: str = ""
    confidence: float = 0.0


@dataclass(frozen=True, slots=True)
class MemberSelectionReport:
    """Output of MemberSelector — selected targets and unselected attributions."""

    selection_id: str = ""
    source_role_attribution_path: str = ""
    source_mechanism_attribution_path: str = ""
    targets: list[MemberOptimizationTarget] = field(default_factory=list)
    unselected_attributions: list[UnselectedAttribution] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Action Planning
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class MemberOptimizationAction:
    """One constrained optimization action for a selected role."""

    action_id: str
    role: str
    action_group: str
    operation: str
    action_type: str
    target_path: str
    description: str = ""
    rationale: str = ""
    attributed_issue_ids: list[str] = field(default_factory=list)
    depends_on: list[str] = field(default_factory=list)
    run_if: str = "dependency_succeeded"
    allowed_skills: list[str] = field(default_factory=list)
    allowed_tools: list[str] = field(default_factory=list)
    candidate_query: str = ""
    install_ref: str = ""
    expected_effect: str = ""
    risk_notes: list[str] = field(default_factory=list)
    declared_write_paths: list[str] = field(default_factory=list)
    constraints: dict[str, Any] = field(default_factory=dict)
    member_name: str = ""


@dataclass(frozen=True, slots=True)
class MemberOptimizationPlan:
    """A dependency-aware member optimization plan."""

    plan_id: str = ""
    targets: list[MemberOptimizationTarget] = field(default_factory=list)
    actions: list[MemberOptimizationAction] = field(default_factory=list)
    action_waves: list[list[str]] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Execution
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class MemberActionExecutionResult:
    """Execution result for one optimization action."""

    action_id: str = ""
    role: str = ""
    status: str = ""
    worktree_path: str = ""
    artifact_path: str = ""
    changed_files: list[str] = field(default_factory=list)
    declared_write_paths: list[str] = field(default_factory=list)
    merge_status: str = ""
    merge_artifact_path: str = ""
    error: str = ""
    member_name: str = ""


@dataclass(frozen=True, slots=True)
class MemberActionMergeResult:
    """Result of merging a role-local subwave into the integration worktree."""

    role: str = ""
    wave_index: int = 0
    subwave_index: int = 0
    action_ids: list[str] = field(default_factory=list)
    status: str = ""
    merged_files: list[str] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)
    artifact_path: str = ""
    error: str = ""


# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class VerificationCheck:
    """Result of one verification check."""

    name: str = ""
    status: str = ""
    notes: str = ""
    error: str = ""


@dataclass(frozen=True, slots=True)
class RoleVerificationResult:
    """Verification result for a single role."""

    role: str = ""
    status: str = ""
    checks: list[VerificationCheck] = field(default_factory=list)
    repairable: bool = False
    error: str = ""


@dataclass(frozen=True, slots=True)
class MemberVerificationResult:
    """Verification result for all roles."""

    status: str = ""
    checked_roles: list[str] = field(default_factory=list)
    checks: list[VerificationCheck] = field(default_factory=list)
    role_results: dict[str, RoleVerificationResult] = field(default_factory=dict)
    repairable: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class RepairItem:
    """One repair applied to a role integration worktree."""

    role: str = ""
    action: str = ""
    description: str = ""
    status: str = ""
    error: str = ""


@dataclass(frozen=True, slots=True)
class MemberFixResult:
    """Result of repair attempts after verification failure."""

    status: str = ""
    repairs: list[RepairItem] = field(default_factory=list)
    verification_path: str = ""
    final_verification_status: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Role Result & Artifact
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RoleResult:
    """Per-role optimization result for artifact and orchestrator consumption."""

    status: str = ""
    before_harness_ref_path: str = ""
    after_harness_ref_path: str = ""
    action_ids: list[str] = field(default_factory=list)
    verification_status: str = ""
    error: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class MemberOptimizationArtifact:
    """Filesystem-backed artifact for one member optimization run.

    This replaces the single-role mock artifact with full multi-role support
    per feat_009 Section 5.2 design decision.
    """

    optimization_id: str = ""
    output_dir: str = ""
    status: str = ""
    optimized_harness_refs_path: str = ""
    roles: list[str] = field(default_factory=list)
    candidate_ready_roles: list[str] = field(default_factory=list)
    published_roles: list[str] = field(default_factory=list)
    staged_roles: list[str] = field(default_factory=list)
    verified_roles: list[str] = field(default_factory=list)
    promoted_roles: list[str] = field(default_factory=list)
    promotion_status: str = "not_evaluated"
    failed_roles: list[str] = field(default_factory=list)
    skipped_roles: list[str] = field(default_factory=list)
    role_attribution_path: str = ""
    mechanism_attribution_path: str = ""
    selection_path: str = ""
    plan_path: str = ""
    execution_result_path: str = ""
    verification_path: str = ""
    fix_result_path: str = ""
    role_results: dict[str, RoleResult] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    # Legacy compatibility field — prefer roles/published_roles/failed_roles
    role: str = ""


__all__ = [
    "ActionDefinition",
    "FailureSignature",
    "MechanismType",
    "MemberActionExecutionResult",
    "MemberActionMergeResult",
    "MemberFixResult",
    "MemberOptimizationAction",
    "MemberOptimizationArtifact",
    "MemberOptimizationPlan",
    "MemberOptimizationTarget",
    "MemberRoleCandidate",
    "MemberSelectionReport",
    "MemberVerificationResult",
    "OptimizationSurface",
    "RepairItem",
    "RoleAttributionReport",
    "RoleIssueAttribution",
    "RoleMechanismAttribution",
    "RoleResult",
    "RoleVerificationResult",
    "UnassignedMemberIssue",
    "UnselectedAttribution",
    "VerificationCheck",
]
