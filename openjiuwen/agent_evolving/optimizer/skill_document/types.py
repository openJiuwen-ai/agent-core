# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Type definitions for skill document optimizer."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from openjiuwen.agent_evolving.dataset import Case, EvaluatedCase
    from openjiuwen.agent_evolving.trajectory.types import Trajectory

EditOp = Literal["append", "insert_after", "replace", "delete"]


@dataclass(frozen=True)
class Edit:
    """Single edit operation on a skill document."""

    op: EditOp
    content: str
    target: str = ""
    support_count: int = 0
    source_type: str = "failure"


@dataclass(frozen=True)
class Patch:
    """Collection of edits with reasoning."""

    edits: list[Edit]
    reasoning: str = ""


@dataclass(frozen=True)
class RawPatch:
    """Raw patch from reflect phase, before aggregation."""

    patch: Patch
    source_type: str
    batch_size: int = 0
    failure_summary: str = ""
    operator_id: str = ""


@dataclass(frozen=True)
class AttributedBatch:
    """Failure/success batch attributed to a single operator."""

    operator_id: str
    failures: list[tuple[Trajectory, EvaluatedCase, Case]]
    successes: list[tuple[Trajectory, EvaluatedCase, Case]]


@dataclass(frozen=True)
class SlowUpdateResult:
    """Result from epoch-level slow update guidance."""

    reasoning: str
    slow_update_content: str
    action: str


__all__ = [
    "AttributedBatch",
    "Edit",
    "EditOp",
    "Patch",
    "RawPatch",
    "SlowUpdateResult",
]
