# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Shared data contracts for standalone Harness improvement."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, TypeAlias

CaseMapping: TypeAlias = dict[str, Any]


@dataclass(frozen=True, slots=True)
class DatasetArtifact:
    """Filesystem-backed JSON dataset artifact consumed by the evaluator."""

    dataset_id: str
    dataset_dir: str
    dataset_files: list[str] = field(default_factory=list)
    cases: int | None = None


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


__all__ = [
    "ActionDefinition",
    "CaseMapping",
    "DatasetArtifact",
    "EvaluationCaseTraceRef",
    "EvaluationResultAnalysisArtifact",
    "EvaluationResultAnalysisInvocation",
    "TeamIssue",
]
