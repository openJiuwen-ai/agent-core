# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Type definitions for skill document optimizer."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

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


@dataclass(frozen=True)
class SlowUpdateResult:
    """Result from epoch-level slow update guidance."""

    reasoning: str
    slow_update_content: str
    action: str


__all__ = [
    "Edit",
    "EditOp",
    "Patch",
    "RawPatch",
    "SlowUpdateResult",
]
