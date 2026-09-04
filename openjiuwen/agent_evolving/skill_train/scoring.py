# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Scoring utilities for ReflACT rollout results."""

from __future__ import annotations

import hashlib


def compute_score(results: list) -> tuple[float, float]:
    """Compute hard and soft accuracy from rollout results."""
    if not results:
        return 0.0, 0.0

    def _hard(r: object) -> float:
        return float(r.hard if hasattr(r, "hard") else r.get("hard", 0))

    def _soft(r: object) -> float:
        return float(r.soft if hasattr(r, "soft") else r.get("soft", 0.0))

    hard = sum(_hard(r) for r in results) / len(results)
    soft = sum(_soft(r) for r in results) / len(results)
    return hard, soft


def skill_hash(content: str) -> str:
    """Return a short deterministic hash of skill content."""
    return hashlib.sha256(content.encode()).hexdigest()[:16]
