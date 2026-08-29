# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

from openjiuwen.rsi.evaluator.optimization_signals import (
    evaluation_optimization_signals,
    optimization_signals_contract,
)


def test_contract_normalizes_continuous_score_and_dimensions() -> None:
    contract = optimization_signals_contract(
        continuous_score=0.75,
        dimensions={"completion": 0.8, "invalid": float("nan")},
        source="official_evaluator",
    )

    assert contract["continuous_score"] == {
        "availability": "available",
        "value": 0.75,
        "source": "official_evaluator",
    }
    assert contract["dimensions"] == {
        "completion": {
            "availability": "available",
            "value": 0.8,
            "source": "official_evaluator",
        }
    }
    assert contract["promotion_authority"] == "eval_ref_case_score"


def test_reader_does_not_infer_from_dataset_specific_metadata() -> None:
    signals = evaluation_optimization_signals(
        {
            "trial_scores": [0.9, 0.7],
            "aggregate_mean_score": 0.8,
            "avg_behavior_score": 0.6,
        }
    )

    assert signals["continuous_score"]["availability"] == "not_available"
    assert signals["dimensions"] == {}


def test_reader_preserves_only_explicit_available_values() -> None:
    signals = evaluation_optimization_signals(
        {
            "optimization_signals": {
                "schema_version": 1,
                "continuous_score": {
                    "availability": "available",
                    "value": 0.42,
                    "source": "adapter",
                },
                "dimensions": {
                    "quality": {"availability": "available", "value": 0.5, "source": "judge"},
                    "missing": {"availability": "not_available", "value": None},
                },
            }
        }
    )

    assert signals["continuous_score"]["value"] == 0.42
    assert signals["dimensions"] == {"quality": {"availability": "available", "value": 0.5, "source": "judge"}}
