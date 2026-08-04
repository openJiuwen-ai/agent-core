# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Scoring invariants for LLM-as-judge outputs."""

import pytest

from openjiuwen.rsi.evaluator.judger.scoring import (
    aggregate_score,
)


def test_forbidden_inverse_does_not_double_penalize_low_behavior() -> None:
    score = aggregate_score(
        behavior_results=[
            {"id": "layout", "score": 1.0},
            {"id": "touch_targets", "score": 0.3},
            {"id": "tap_flow", "score": 0.75},
        ],
        forbidden_hits=[{"id": "sub_44px", "triggered": True, "penalty": 0.3}],
        behaviors=[
            {"id": "layout", "weight": 0.3},
            {"id": "touch_targets", "weight": 0.35},
            {"id": "tap_flow", "weight": 0.35},
        ],
    )

    assert score == pytest.approx(0.6675)


def test_forbidden_hit_caps_otherwise_high_behavior_score() -> None:
    score = aggregate_score(
        behavior_results=[{"id": "quality", "score": 0.95}],
        forbidden_hits=[{"id": "unsafe", "triggered": True, "penalty": 0.3}],
        behaviors=[{"id": "quality", "weight": 1.0}],
    )

    assert score == 0.7
