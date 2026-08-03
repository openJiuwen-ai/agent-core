# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Progress event model for streaming ``OptimizationOrchestrator.run`` state."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable


@dataclass(frozen=True, slots=True)
class OptimizationProgressEvent:
    """A single progress event emitted by the optimization orchestrator."""

    phase: str
    epoch: int = 0
    batch_index: int = 0
    stage: str = ""
    score: float | None = None
    improved: bool | None = None
    message: str = ""
    metrics: dict[str, Any] = field(default_factory=dict)
    artifacts: dict[str, Any] = field(default_factory=dict)


ProgressCallback = Callable[[OptimizationProgressEvent], None]
SeedOptimizationDecisionCallback = Callable[[dict[str, Any]], bool | Awaitable[bool]]


__all__ = [
    "OptimizationProgressEvent",
    "ProgressCallback",
    "SeedOptimizationDecisionCallback",
]
