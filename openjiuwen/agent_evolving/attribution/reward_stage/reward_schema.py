# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""``attribution.reward.v1`` value objects and validation.

The Reward deep agent submits its result through ``submit_reward``; the
payload is validated here before being recorded.  Dimensions convert into
diagnostic signals for the attribution stage.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from openjiuwen.agent_evolving.attribution.model import (
    DiagnosticSignal,
    RewardDimension,
    RewardOverall,
    SignalSource,
    reward_tier,
)

SCHEMA_VERSION = "attribution.reward.v1"


class RewardSchemaError(ValueError):
    """A submitted reward payload failed format validation."""


def validate_reward_payload(payload: Any) -> list[str]:
    """Return a list of format errors; an empty list means valid."""
    errors: list[str] = []
    if not isinstance(payload, Mapping):
        return ["payload must be an object"]
    overall = payload.get("overall")
    if not isinstance(overall, Mapping):
        errors.append("overall is required and must be an object")
    else:
        if not str(overall.get("comment") or "").strip():
            errors.append("overall.comment is required")
        total_score = overall.get("total_score")
        if isinstance(total_score, bool) or not isinstance(total_score, (int, float)):
            errors.append("overall.total_score is required and must be a number")
        elif not 0 <= float(total_score) <= 100:
            errors.append("overall.total_score must be within [0, 100]")
    categories = payload.get("categories")
    if categories is not None:
        if not isinstance(categories, list):
            errors.append("categories must be a list")
        else:
            for index, category in enumerate(categories):
                if not isinstance(category, Mapping) or not str(category.get("name") or "").strip():
                    errors.append(f"categories[{index}].name is required")
                    continue
                score = category.get("score")
                if score is not None and not isinstance(score, (int, float)):
                    errors.append(f"categories[{index}].score must be a number")
    dimensions = payload.get("dimensions")
    if not isinstance(dimensions, list) or not dimensions:
        errors.append("dimensions is required and must be a non-empty list")
    else:
        for index, dimension in enumerate(dimensions):
            if not isinstance(dimension, Mapping):
                errors.append(f"dimensions[{index}] must be an object")
                continue
            for key in ("category", "requirement", "comment"):
                if not str(dimension.get(key) or "").strip():
                    errors.append(f"dimensions[{index}].{key} is required")
            score = dimension.get("score")
            if isinstance(score, bool) or not isinstance(score, (int, float)):
                errors.append(f"dimensions[{index}].score is required and must be a number")
            elif not 0 <= float(score) <= 100:
                errors.append(f"dimensions[{index}].score must be within [0, 100]")
            related = dimension.get("related_signal_ids")
            if related is not None and not isinstance(related, list):
                errors.append(f"dimensions[{index}].related_signal_ids must be a list")
    return errors


def parse_reward_payload(payload: Any) -> tuple[RewardOverall, list[RewardDimension]]:
    """Parse and validate a submitted payload, raising :class:`RewardSchemaError`."""
    errors = validate_reward_payload(payload)
    if errors:
        raise RewardSchemaError("; ".join(errors))
    overall = RewardOverall(
        comment=str(payload["overall"]["comment"]),
        total_score=int(float(payload["overall"]["total_score"])),
    )
    dimensions = [
        RewardDimension(
            category=str(dimension["category"]),
            requirement=str(dimension["requirement"]),
            comment=str(dimension["comment"]),
            score=float(dimension["score"]),
            related_signal_ids=[str(item) for item in dimension.get("related_signal_ids") or ()],
        )
        for dimension in payload["dimensions"]
    ]
    return overall, dimensions


@dataclass(frozen=True)
class RewardResult:
    """A validated reward submission."""

    overall: RewardOverall
    dimensions: list[RewardDimension]
    categories: list[dict[str, Any]] = field(default_factory=list)

    @classmethod
    def from_payload(cls, payload: Any) -> "RewardResult":
        overall, dimensions = parse_reward_payload(payload)
        categories = (
            [dict(category) for category in payload["categories"] or ()]
            if isinstance(payload, Mapping) and payload.get("categories")
            else []
        )
        return cls(overall=overall, dimensions=dimensions, categories=categories)

    def to_payload(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "overall": self.overall.to_dict(),
            "categories": self.categories,
            "dimensions": [
                {
                    "category": dimension.category,
                    "requirement": dimension.requirement,
                    "comment": dimension.comment,
                    "score": dimension.score,
                    "related_signal_ids": list(dimension.related_signal_ids),
                }
                for dimension in self.dimensions
            ],
        }


def reward_to_signals(
    reward: RewardResult,
    related_signals: list[DiagnosticSignal],
) -> list[DiagnosticSignal]:
    """Convert reward dimensions into diagnostic signals for attribution.

    ``node_ids`` are collected from the related process signals each
    dimension references (lead anchors, not located causes).
    """
    by_id = {signal.signal_id: signal for signal in related_signals}
    signals: list[DiagnosticSignal] = []
    for dimension in reward.dimensions:
        node_ids: list[str] = []
        ts_ns: int | None = None
        message_id: str | None = None
        for signal_id in dimension.related_signal_ids:
            signal = by_id.get(signal_id)
            if signal is None:
                continue
            for node_id in signal.node_ids:
                if node_id not in node_ids:
                    node_ids.append(node_id)
            if ts_ns is None and signal.ts_ns is not None:
                ts_ns = signal.ts_ns
            if message_id is None and signal.message_id is not None:
                message_id = signal.message_id
        signals.append(
            DiagnosticSignal.create(
                f"{dimension.requirement}\n{dimension.comment}",
                source=SignalSource.REWARD,
                tier=reward_tier(dimension.score),
                node_ids=node_ids,
                score=dimension.score,
                ts_ns=ts_ns,
                message_id=message_id,
                metadata={"category": dimension.category, "related_signal_ids": list(dimension.related_signal_ids)},
            )
        )
    return signals


__all__ = [
    "SCHEMA_VERSION",
    "RewardResult",
    "RewardSchemaError",
    "parse_reward_payload",
    "reward_to_signals",
    "validate_reward_payload",
]
