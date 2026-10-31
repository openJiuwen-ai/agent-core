# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Multi-rollout configuration."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class MultiRolloutConfig:
    """Configuration for parallel multi-rollout task execution.

    When enabled, the agent spawns *n_rollouts* isolated subagents,
    each with a different strategy, runs them in parallel, and returns
    the best result.
    """

    enabled: bool = False
    n_rollouts: int = 3
    max_parallel: int = 0  # 0 means unlimited (bounded by asyncio)
    timeout_per_rollout: float = 600.0
    strategy_variants: list[str] = field(
        default_factory=lambda: [
            (
                "Approach: focus on correctness and thoroughness. "
                "Explore deeply, consider all implications, and produce "
                "a robust solution."
            ),
            (
                "Approach: focus on minimal changes. Change as few lines "
                "as possible while still fixing the issue. Preserve existing "
                "structure and style."
            ),
            (
                "Approach: focus on edge cases and defensive programming. "
                "Consider boundary conditions, error handling, and "
                "unexpected inputs."
            ),
        ]
    )
    selector_kind: str = "first_successful"
    # Optional: extra kwargs passed to the selector
    selector_kwargs: dict[str, Any] = field(default_factory=dict)
