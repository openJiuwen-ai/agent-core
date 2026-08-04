# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Data contracts for optimization experience learning."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class OptimizationExperienceStageInput:
    """Stage-level input for extracting reusable optimization experience."""

    stage: str
    source_artifact_paths: list[str] = field(default_factory=list)
    summary: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class OptimizationExperienceInput:
    """Accepted optimization transition used by the experience learner."""

    optimization_type: str
    before_ref_path: str
    after_ref_path: str
    eval_ref_path: str
    output_dir: str
    role: str = ""
    stages: list[OptimizationExperienceStageInput] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class OptimizationExperienceArtifact:
    """Filesystem-backed reusable experience record."""

    experience_id: str
    optimization_type: str
    output_dir: str
    experience_ref_path: str
    stage_experience_paths: list[str] = field(default_factory=list)
    role: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class OptimizationExperienceRetrievalQuery:
    """Query for retrieving reusable optimization experience."""

    optimization_type: str
    stage: str
    eval_ref_path: str = ""
    analysis_result_path: str = ""
    harness_refs_path: str = ""
    target_members: list[str] = field(default_factory=list)
    candidate_modules: list[str] = field(default_factory=list)
    limit: int = 5
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class OptimizationExperienceRetrievalResult:
    """Retrieved reusable experience for one optimization stage."""

    query: OptimizationExperienceRetrievalQuery
    matches: list[dict[str, Any]] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


__all__ = [
    "OptimizationExperienceArtifact",
    "OptimizationExperienceInput",
    "OptimizationExperienceRetrievalQuery",
    "OptimizationExperienceRetrievalResult",
    "OptimizationExperienceStageInput",
]
