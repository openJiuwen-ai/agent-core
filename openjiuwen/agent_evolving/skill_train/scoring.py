# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Aggregate rollout metrics and fingerprint skill text."""

from __future__ import annotations

import hashlib
from typing import Any

_HASH_PREFIX_LEN = 16


def _metric_value(record: Any, attr: str, default: float) -> float:
    if hasattr(record, attr):
        return float(getattr(record, attr))
    if isinstance(record, dict):
        return float(record.get(attr, default))
    return default


def compute_score(results: list) -> tuple[float, float]:
    """Return mean hard and soft scores across rollout records."""
    count = len(results)
    if count == 0:
        return 0.0, 0.0

    hard_total = sum(_metric_value(r, "hard", 0.0) for r in results)
    soft_total = sum(_metric_value(r, "soft", 0.0) for r in results)
    return hard_total / count, soft_total / count


def skill_hash(content: str) -> str:
    """Fingerprint skill body text with a truncated SHA-256 digest."""
    digest = hashlib.sha256(content.encode()).hexdigest()
    return digest[:_HASH_PREFIX_LEN]
