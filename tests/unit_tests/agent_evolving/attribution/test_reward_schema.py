# coding: utf-8
"""Tests for the attribution.reward.v1 schema and signal conversion."""

from __future__ import annotations

import pytest

from openjiuwen.agent_evolving.attribution.model import DiagnosticSignal, SignalSource
from openjiuwen.agent_evolving.attribution.reward_stage.reward_schema import (
    RewardResult,
    RewardSchemaError,
    parse_reward_payload,
    reward_to_signals,
    validate_reward_payload,
)


def _valid_payload() -> dict[str, object]:
    return {
        "schema_version": "attribution.reward.v1",
        "overall": {"comment": "整体不错", "total_score": 87},
        "categories": [{"name": "正确性", "comment": "基本正确", "score": 90}],
        "dimensions": [
            {
                "category": "正确性",
                "requirement": "结果应正确",
                "comment": "有一处错误",
                "score": 70,
                "related_signal_ids": ["diag-a"],
            }
        ],
    }


def test_validate_accepts_valid_payload() -> None:
    assert validate_reward_payload(_valid_payload()) == []


def test_validate_rejects_missing_overall() -> None:
    payload = _valid_payload()
    del payload["overall"]
    assert any("overall" in error for error in validate_reward_payload(payload))


def test_validate_rejects_missing_dimensions() -> None:
    payload = _valid_payload()
    del payload["dimensions"]
    assert any("dimensions" in error for error in validate_reward_payload(payload))


def test_validate_rejects_out_of_range_scores() -> None:
    payload = _valid_payload()
    payload["dimensions"][0]["score"] = 150  # type: ignore[index]
    assert any("0, 100" in error for error in validate_reward_payload(payload))


def test_parse_reward_payload() -> None:
    overall, dimensions = parse_reward_payload(_valid_payload())
    assert overall.total_score == 87
    assert len(dimensions) == 1
    assert dimensions[0].score == 70.0


def test_parse_raises_on_invalid() -> None:
    payload = _valid_payload()
    del payload["dimensions"]
    with pytest.raises(RewardSchemaError):
        parse_reward_payload(payload)


def test_reward_result_roundtrip() -> None:
    result = RewardResult.from_payload(_valid_payload())
    restored = RewardResult.from_payload(result.to_payload())
    assert restored.overall == result.overall
    assert restored.dimensions == result.dimensions


def test_reward_to_signals_collects_related_nodes() -> None:
    related = [
        DiagnosticSignal.create(
            "故障信号",
            source=SignalSource.KEYWORD_TOOL,
            node_ids=["main:5", "main:6"],
            ts_ns=500,
            message_id="m9",
        )
    ]
    payload = _valid_payload()
    payload["dimensions"][0]["related_signal_ids"] = [related[0].signal_id]  # type: ignore[index]
    result = RewardResult.from_payload(payload)
    signals = reward_to_signals(result, related)
    assert len(signals) == 1
    signal = signals[0]
    assert signal.source == SignalSource.REWARD
    assert signal.tier == "weak"
    assert signal.score == 70.0
    assert signal.node_ids == ["main:5", "main:6"]
    assert signal.ts_ns == 500
    assert signal.message_id == "m9"


def test_reward_to_signals_ignores_unknown_related() -> None:
    result = RewardResult.from_payload(_valid_payload())
    signals = reward_to_signals(result, [])
    assert signals[0].node_ids == []
