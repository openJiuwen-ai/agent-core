# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Dataset profiling helpers for batch planning."""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any

UNKNOWN_VALUE = "unknown"


class DatasetProfiler:
    """Build deterministic metadata summaries for incoming dataset cases."""

    @staticmethod
    def profile(
        cases: list[dict[str, Any]],
        balance_keys: list[str],
    ) -> dict[str, Any]:
        """Return profile summary, warnings, and metadata quality."""
        summary: dict[str, Any] = {"total_cases": len(cases)}
        warnings: list[dict[str, Any]] = []
        for key in balance_keys:
            counter = Counter(case_value(case, key) for case in cases)
            counter.pop(UNKNOWN_VALUE, None)
            summary[key] = dict(sorted(counter.items()))
        for case in cases:
            missing_fields = [key for key in balance_keys if case_value(case, key) == UNKNOWN_VALUE]
            if missing_fields:
                warnings.append(
                    {
                        "case_id": case_id(case),
                        "missing_fields": missing_fields,
                    }
                )
        return {
            "summary": summary,
            "warnings": warnings,
            "metadata": {
                "quality": _profile_quality(len(cases), len(warnings)),
            },
        }


def case_value(case: dict[str, Any], key: str) -> str:
    """Read a balance value from top-level fields or nested metadata."""
    value = case.get(key)
    if value in (None, "") and isinstance(case.get("metadata"), dict):
        value = case["metadata"].get(key)
    if value in (None, ""):
        return UNKNOWN_VALUE
    return str(value)


def case_id(case: dict[str, Any]) -> str:
    """Return a stable case identifier for plans and warnings."""
    value = case.get("case_id") or case.get("id")
    if value in (None, ""):
        return f"{Path(str(case.get('case_path', 'case'))).stem}#{case.get('case_index', 0)}"
    return str(value)


def _profile_quality(total_cases: int, warning_count: int) -> str:
    if total_cases == 0:
        return "empty"
    if warning_count == 0:
        return "normal"
    if warning_count / total_cases >= 0.5:
        return "low_quality_fallback"
    return "partial_metadata"


__all__ = [
    "DatasetProfiler",
    "UNKNOWN_VALUE",
    "case_id",
    "case_value",
]
