# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Shared data contracts for RSI harness and artifact optimization."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, TypeAlias

ArtifactType: TypeAlias = Literal["program", "paper"]
RsiScenario: TypeAlias = Literal["harness", "artifact"]
RsiStatus: TypeAlias = Literal[
    "created",
    "queued",
    "running",
    "completed",
    "failed",
    "paused",
    "terminated",
]


@dataclass(frozen=True, slots=True)
class RsiTaskCreateRequest:
    """Public task creation request shared by Harness and artifact scenes."""

    scenario: RsiScenario
    artifact_type: ArtifactType | None
    name: str
    artifact_path: str | None
    optimization_instruction: str | None
    dataset_file: str | None
    search_width: int | None
    model_refs: dict[str, str]
    max_iterations: int


@dataclass(frozen=True, slots=True)
class RsiTaskEnvelope:
    """AgentServer-owned identity and configuration for one RSI task."""

    task_id: str
    run_dir: str
    artifact_type: ArtifactType | None
    config: RsiTaskCreateRequest


@dataclass(frozen=True, slots=True)
class ArtifactValidationResult:
    """Result of provider-specific input validation."""

    valid: bool
    errors: list[dict[str, str]]


@dataclass(frozen=True, slots=True)
class RsiUsageTokens:
    """Cumulative token counters for one optimization task."""

    input: int
    output: int
    cache_hit: int


@dataclass(frozen=True, slots=True)
class RsiUsage:
    """Cumulative model and optimization-engine usage."""

    tokens: RsiUsageTokens
    cost_estimate: float
    call_count: int


@dataclass(frozen=True, slots=True)
class RsiChange:
    """Intent-level summary of changes represented by one tree node."""

    group: str
    operation: str
    function: str | None
    target: str | None
    summary: str


@dataclass(frozen=True, slots=True)
class RsiTreeNode:
    """Common node projection used by tree queries and node events."""

    node_id: str
    iteration: int
    parent_id: str | None
    type: str
    adopted: bool
    score: float | None
    summary: str | None
    snapshot_artifact_id: str | None
    reason: str | None
    failure_class: str | None
    changes: list[RsiChange]
    extra: dict[str, object]


@dataclass(frozen=True, slots=True)
class TreeResponse:
    """Complete persisted RSI tree, including the root and all branches."""

    nodes: list[RsiTreeNode]
    depth: int
    iteration: int


@dataclass(frozen=True, slots=True)
class EngineResult:
    """Result of an execution-control operation."""

    task_id: str
    status: RsiStatus
    final_node_id: str | None
    error_code: str | None
    error_message: str | None


@dataclass(frozen=True, slots=True)
class EngineState:
    """Persisted current state for one RSI engine task."""

    task_id: str
    status: RsiStatus
    iteration: int
    total_iterations: int
    score: float | None
    baseline: float | None
    usage: RsiUsage | None
    updated_at: str
    error_code: str | None
    error_message: str | None


@dataclass(frozen=True, slots=True)
class ArtifactRef:
    """Provider-side reference to a downloadable task or node artifact."""

    artifact_id: str
    node_id: str | None
    name: str
    kind: str
    path: str
    sha256: str | None
    download_url: str | None


@dataclass(frozen=True, slots=True)
class EngineReport:
    """Current or final report and the task's complete artifact index."""

    task_id: str
    status: RsiStatus
    best_node_id: str | None
    usage: RsiUsage | None
    artifact_index: list[ArtifactRef]
    summary: str | None


__all__ = [
    "ArtifactRef",
    "ArtifactType",
    "ArtifactValidationResult",
    "EngineReport",
    "EngineResult",
    "EngineState",
    "RsiChange",
    "RsiScenario",
    "RsiStatus",
    "RsiTaskCreateRequest",
    "RsiTaskEnvelope",
    "RsiTreeNode",
    "RsiUsage",
    "RsiUsageTokens",
    "TreeResponse",
]
