# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Artifact-specific request contract and compatibility exports.

Shared RSI task, tree, usage, and result types live in
:mod:`openjiuwen.rsi.schema`.  They are re-exported here for compatibility
with the original artifact contract import path.
"""

from __future__ import annotations

from dataclasses import dataclass

from openjiuwen.rsi.schema import (
    ArtifactRef,
    ArtifactType,
    ArtifactValidationResult,
    EngineReport,
    EngineResult,
    EngineState,
    RsiChange,
    RsiScenario,
    RsiStatus,
    RsiTaskCreateRequest,
    RsiTaskEnvelope,
    RsiTreeNode,
    RsiUsage,
    RsiUsageTokens,
    TreeResponse,
)


@dataclass(frozen=True, slots=True)
class ArtifactEngineRequest:
    """Provider-facing request shared by program and paper optimizers.

    ``artifact_type`` intentionally does not appear here.  Routing has already
    happened before the AgentServer creates this request.
    """

    task_id: str
    run_dir: str
    artifact_path: str | None
    model_config: str
    max_iterations: int
    optimization_instruction: str | None


__all__ = [
    "ArtifactEngineRequest",
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
