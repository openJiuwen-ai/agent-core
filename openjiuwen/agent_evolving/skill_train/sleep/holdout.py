# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Train/val partitioning and per-task score deltas for sleep nights."""

from __future__ import annotations

import math
from typing import List, Mapping, Sequence, Tuple

from openjiuwen.agent_evolving.skill_train.sleep.gate import select_gate_score
from openjiuwen.agent_evolving.skill_train.sleep.types import ReplayResult, TaskRecord

_ALIAS = {"replay": "train", "holdout": "val"}


def _canonical_split(label: str) -> str:
    return _ALIAS.get(label, label)


def partition_tasks(
    tasks: Sequence[TaskRecord],
) -> Tuple[List[TaskRecord], List[TaskRecord], bool]:
    """Return ``(train, val, holdout_leaked)``.

    ``holdout_leaked`` is True when val is empty and falls back to train, or
    when the two slices share task ids.
    """
    train = [task for task in tasks if _canonical_split(task.split) == "train"]
    val = [task for task in tasks if _canonical_split(task.split) == "val"]
    leaked = False
    if not val:
        fallback = train or [task for task in tasks if _canonical_split(task.split) != "test"]
        val = list(fallback)
        leaked = bool(val)
    if not train:
        train = list(val)
        leaked = leaked or bool(train)
    if not leaked and train and val:
        train_ids = {task.id for task in train}
        leaked = any(task.id in train_ids for task in val)
    return train, val, leaked


def _finite(value: float | None) -> float | None:
    if value is None:
        return None
    return value if math.isfinite(value) else None


def _score_of(
    result: ReplayResult | None,
    metric: str,
    mixed_weight: float,
) -> float | None:
    if result is None:
        return None
    return select_gate_score(result.hard, result.soft, metric, mixed_weight)


def compare_task_scores(
    tasks: Sequence[TaskRecord],
    baseline_pairs: Sequence[Tuple[TaskRecord, ReplayResult]],
    candidate_pairs: Sequence[Tuple[TaskRecord, ReplayResult]],
    metric: str,
    mixed_weight: float,
) -> List[dict]:
    baseline_map: Mapping[str, ReplayResult] = {task.id: result for task, result in baseline_pairs}
    candidate_map: Mapping[str, ReplayResult] = {task.id: result for task, result in candidate_pairs}
    rows: List[dict] = []
    for task in tasks:
        base = _score_of(baseline_map.get(task.id), metric, mixed_weight)
        cand = _score_of(candidate_map.get(task.id), metric, mixed_weight)
        if base is None or cand is None:
            status = "regressed"
            finite = False
        else:
            finite = math.isfinite(base) and math.isfinite(cand)
            if not finite:
                status = "regressed"
            elif cand > base:
                status = "improved"
            elif cand < base:
                status = "regressed"
            else:
                status = "unchanged"
        rows.append(
            {
                "task_id": task.id,
                "baseline_score": _finite(base),
                "candidate_score": _finite(cand),
                "status": status,
                "scores_are_finite": finite,
            }
        )
    return rows


def any_regression(rows: Sequence[Mapping[str, object]]) -> bool:
    return any(row.get("status") == "regressed" for row in rows)
