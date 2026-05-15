# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Training checkpoint state types."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional


@dataclass
class EvolveCheckpoint:
    """Training checkpoint (for resume)."""

    version: str
    run_id: str
    step: Dict[str, int]
    best: Dict[str, Any]
    seed: Optional[int]
    operators_state: Dict[str, Dict[str, Any]]
    updater_state: Dict[str, Any]
    searcher_state: Dict[str, Any]
    last_metrics: Dict[str, Any]


__all__ = ["EvolveCheckpoint"]
