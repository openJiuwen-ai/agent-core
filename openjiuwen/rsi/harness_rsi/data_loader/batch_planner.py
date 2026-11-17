# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Curriculum-balanced batch planning."""

from __future__ import annotations

from collections import defaultdict, deque
from typing import Any

from openjiuwen.rsi.harness_rsi.data_loader.profiler import (
    UNKNOWN_VALUE,
    case_id,
    case_value,
)

_DIFFICULTY_ORDER = {
    "easy": 0,
    "medium": 1,
    "hard": 2,
}


class BatchPlanner:
    """Plan batches with difficulty progression and dimension round-robin."""

    @staticmethod
    def plan(
        cases: list[dict[str, Any]],
        batch_size: int,
    ) -> list[list[dict[str, Any]]]:
        """Return ordered batches for one epoch."""
        ordered_cases = _curriculum_balanced_cases(cases)
        batches: list[list[dict[str, Any]]] = []
        batch: list[dict[str, Any]] = []
        for case in ordered_cases:
            batch.append(case)
            if len(batch) >= batch_size:
                batches.append(batch)
                batch = []
        if batch:
            batches.append(batch)
        return batches


def batch_plan_item(batch: list[dict[str, Any]], batch_index: int) -> dict[str, Any]:
    """Return the serializable batch plan entry for one batch."""
    difficulties = [case_value(case, "difficulty") for case in batch]
    return {
        "batch_id": f"batch_{batch_index:03d}",
        "cases": [
            {
                "case_id": case_id(case),
                "difficulty": case_value(case, "difficulty"),
                "dimension": case_value(case, "dimension"),
                "source": case_value(case, "source"),
                "task_type": case_value(case, "task_type"),
                "case_path": str(case.get("case_path", "")),
                "case_index": case.get("case_index"),
            }
            for case in batch
        ],
        "metadata": {
            "difficulty_stage": _dominant_difficulty(difficulties),
            "dimensions": sorted({case_value(case, "dimension") for case in batch}),
        },
    }


def _curriculum_balanced_cases(cases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_difficulty: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for case in cases:
        by_difficulty[case_value(case, "difficulty")].append(case)

    ordered: list[dict[str, Any]] = []
    for difficulty in sorted(by_difficulty, key=_difficulty_sort_key):
        ordered.extend(_round_robin_by_dimension(by_difficulty[difficulty]))
    return ordered


def _round_robin_by_dimension(cases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, deque[dict[str, Any]]] = defaultdict(deque)
    for case in sorted(cases, key=_stable_case_sort_key):
        grouped[case_value(case, "dimension")].append(case)

    ordered: list[dict[str, Any]] = []
    dimensions = sorted(grouped)
    while any(grouped[dimension] for dimension in dimensions):
        for dimension in dimensions:
            if grouped[dimension]:
                ordered.append(grouped[dimension].popleft())
    return ordered


def _difficulty_sort_key(difficulty: str) -> tuple[int, str]:
    return (_DIFFICULTY_ORDER.get(difficulty, len(_DIFFICULTY_ORDER)), difficulty)


def _stable_case_sort_key(case: dict[str, Any]) -> tuple[str, str, str, int]:
    return (
        case_value(case, "source"),
        case_value(case, "task_type"),
        case_id(case),
        int(case.get("case_index") or 0),
    )


def _dominant_difficulty(difficulties: list[str]) -> str:
    known = [difficulty for difficulty in difficulties if difficulty != UNKNOWN_VALUE]
    if not known:
        return UNKNOWN_VALUE
    return min(known, key=_difficulty_sort_key)


__all__ = [
    "BatchPlanner",
    "batch_plan_item",
]
