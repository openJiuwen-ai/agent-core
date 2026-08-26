# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Dataset-neutral continuous signals used only to rank Harness experiments."""

from __future__ import annotations

import math
from typing import Any, Mapping

OPTIMIZATION_SIGNALS_SCHEMA_VERSION = 1


def optimization_signals_contract(
    *,
    continuous_score: Any = None,
    dimensions: Mapping[str, Any] | None = None,
    source: str = "",
) -> dict[str, Any]:
    """Build a versioned, dataset-neutral search-signal contract.

    These signals may rank equally passing candidates or select a repair parent.
    They never replace the evaluator's strict promotion score.
    """
    score = _finite_float(continuous_score)
    normalized_dimensions: dict[str, dict[str, Any]] = {}
    for raw_name, raw_value in (dimensions or {}).items():
        name = str(raw_name).strip()
        value = _dimension_value(raw_value)
        if not name or value is None:
            continue
        dimension_source = str(raw_value.get("source") or source) if isinstance(raw_value, Mapping) else str(source)
        normalized_dimensions[name] = {
            "availability": "available",
            "value": value,
            "source": dimension_source,
        }
    return {
        "schema_version": OPTIMIZATION_SIGNALS_SCHEMA_VERSION,
        "continuous_score": {
            "availability": "available" if score is not None else "not_available",
            "value": score,
            "source": str(source),
        },
        "dimensions": dict(sorted(normalized_dimensions.items())),
        "promotion_authority": "eval_ref_case_score",
    }


def evaluation_optimization_signals(metadata: Mapping[str, Any]) -> dict[str, Any]:
    """Normalize the explicit optimization contract from evaluator metadata."""
    raw = metadata.get("optimization_signals")
    if not isinstance(raw, Mapping):
        return optimization_signals_contract()
    continuous = raw.get("continuous_score")
    continuous = continuous if isinstance(continuous, Mapping) else {}
    dimensions = raw.get("dimensions")
    dimensions = dimensions if isinstance(dimensions, Mapping) else {}
    return optimization_signals_contract(
        continuous_score=(
            continuous.get("value") if str(continuous.get("availability") or "") == "available" else None
        ),
        dimensions=dimensions,
        source=str(continuous.get("source") or ""),
    )


def _dimension_value(value: Any) -> float | None:
    if isinstance(value, Mapping):
        if str(value.get("availability") or "available") != "available":
            return None
        value = value.get("value")
    return _finite_float(value)


def _finite_float(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


__all__ = [
    "OPTIMIZATION_SIGNALS_SCHEMA_VERSION",
    "evaluation_optimization_signals",
    "optimization_signals_contract",
]
