# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Consolidate: reflect -> bounded edit -> held-out gate."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import List, Tuple

from openjiuwen.agent_evolving.skill_train.sleep.backend import Backend
from openjiuwen.agent_evolving.skill_train.sleep.gate import evaluate_gate, select_gate_score
from openjiuwen.agent_evolving.skill_train.sleep.memory import apply_edits_detailed
from openjiuwen.agent_evolving.skill_train.sleep.replay import aggregate_scores, replay_batch
from openjiuwen.agent_evolving.skill_train.sleep.types import EditRecord, ReplayResult, TaskRecord


def _finite_score(value: float) -> float | None:
    return value if math.isfinite(value) else None


@dataclass
class ConsolidationResult:
    accepted: bool
    gate_action: str
    baseline_score: float
    candidate_score: float
    new_skill: str
    new_memory: str
    applied_edits: List[EditRecord]
    rejected_edits: List[EditRecord]
    holdout_baseline: float
    holdout_candidate: float
    unmatched_edits: List[EditRecord] = field(default_factory=list)
    holdout_leaked: bool = False
    gate_trials: List[dict] = field(default_factory=list)


def _split(tasks: List[TaskRecord]) -> Tuple[List[TaskRecord], List[TaskRecord], bool]:
    def _norm(split: str) -> str:
        return {"replay": "train", "holdout": "val"}.get(split, split)

    train = [task for task in tasks if _norm(task.split) == "train"]
    val = [task for task in tasks if _norm(task.split) == "val"]
    leaked = False
    if not val:
        val = train or [task for task in tasks if _norm(task.split) != "test"]
        leaked = bool(val)
    if not train:
        train = val
        leaked = leaked or bool(train)
    if not leaked and train and val:
        train_ids = {task.id for task in train}
        if any(task.id in train_ids for task in val):
            leaked = True
    return train, val, leaked


def _task_deltas(
    tasks: List[TaskRecord],
    baseline_pairs: List[Tuple[TaskRecord, ReplayResult]],
    candidate_pairs: List[Tuple[TaskRecord, ReplayResult]],
    metric: str,
    mixed_weight: float,
) -> List[dict]:
    baseline_by_id = {task.id: result for task, result in baseline_pairs}
    candidate_by_id = {task.id: result for task, result in candidate_pairs}
    out: List[dict] = []
    for task in tasks:
        baseline = baseline_by_id.get(task.id)
        candidate = candidate_by_id.get(task.id)
        baseline_score = (
            select_gate_score(baseline.hard, baseline.soft, metric, mixed_weight) if baseline is not None else None
        )
        candidate_score = (
            select_gate_score(candidate.hard, candidate.soft, metric, mixed_weight) if candidate is not None else None
        )
        if baseline_score is None or candidate_score is None:
            status = "regressed"
            scores_are_finite = False
        else:
            scores_are_finite = math.isfinite(baseline_score) and math.isfinite(candidate_score)
            if not scores_are_finite:
                status = "regressed"
            elif candidate_score > baseline_score:
                status = "improved"
            elif candidate_score < baseline_score:
                status = "regressed"
            else:
                status = "unchanged"
        out.append(
            {
                "task_id": task.id,
                "baseline_score": _finite_score(baseline_score) if baseline_score is not None else None,
                "candidate_score": _finite_score(candidate_score) if candidate_score is not None else None,
                "status": status,
                "scores_are_finite": scores_are_finite,
            }
        )
    return out


def consolidate(
    backend: Backend,
    tasks: List[TaskRecord],
    skill: str,
    memory: str,
    *,
    edit_budget: int = 4,
    gate_metric: str = "mixed",
    gate_mixed_weight: float = 0.5,
    gate_no_regression: bool = False,
    gate_mode: str = "on",
    evolve_skill: bool = True,
    evolve_memory: bool = False,
    night: int = 1,
) -> ConsolidationResult:
    train_tasks, val_tasks, holdout_leaked = _split(tasks)
    gate_off = str(gate_mode).strip().lower() in {"off", "none", "false", "greedy"}

    if gate_off:
        base_hard, base_soft = 0.0, 0.0
        base_pairs: List[Tuple[TaskRecord, ReplayResult]] = []
    else:
        base_pairs = replay_batch(backend, val_tasks, skill, memory)
        base_hard, base_soft = aggregate_scores(base_pairs)
    base_score = select_gate_score(base_hard, base_soft, gate_metric, gate_mixed_weight)

    train_pairs = replay_batch(backend, train_tasks, skill, memory)
    failures = [(task, result) for task, result in train_pairs if result.hard < 1.0]
    successes = [(task, result) for task, result in train_pairs if result.hard >= 1.0]

    cand_skill, cand_memory = skill, memory
    all_applied: List[EditRecord] = []
    all_rejected: List[EditRecord] = []
    all_unmatched: List[EditRecord] = []
    gate_trials: List[dict] = []
    current_pairs = base_pairs

    def _gate_apply(doc: str, edits: List[EditRecord], which: str) -> str:
        nonlocal cand_skill, cand_memory, base_score, current_pairs
        if not edits:
            return doc
        new_doc, applied, unmatched = apply_edits_detailed(doc, edits)
        if unmatched:
            all_unmatched.extend(unmatched)
        if not applied:
            return doc
        if gate_off:
            all_applied.extend(applied)
            return new_doc
        trial_skill = new_doc if which == "skill" else cand_skill
        trial_memory = new_doc if which == "memory" else cand_memory
        pairs = replay_batch(backend, val_tasks, trial_skill, trial_memory)
        hard, soft = aggregate_scores(pairs)
        cand_score = select_gate_score(hard, soft, gate_metric, gate_mixed_weight)
        task_deltas = _task_deltas(val_tasks, current_pairs, pairs, gate_metric, gate_mixed_weight)
        blocked = bool(gate_no_regression and any(row["status"] == "regressed" for row in task_deltas))
        improved = cand_score > base_score and not blocked
        gate_trials.append(
            {
                "target": which,
                "baseline_score": _finite_score(base_score),
                "candidate_score": _finite_score(cand_score),
                "accepted": improved,
                "blocked_by_regression": blocked,
                "task_deltas": task_deltas,
            }
        )
        if improved:
            base_score = cand_score
            current_pairs = pairs
            all_applied.extend(applied)
            return new_doc
        all_rejected.extend(applied)
        return doc

    if evolve_skill:
        edits = backend.reflect(
            failures,
            successes,
            cand_skill,
            cand_memory,
            edit_budget=edit_budget,
            evolve_skill=True,
            evolve_memory=False,
        )
        cand_skill = _gate_apply(cand_skill, edits, "skill")

    if evolve_memory:
        train_pairs2 = replay_batch(backend, train_tasks, cand_skill, cand_memory)
        failures2 = [(task, result) for task, result in train_pairs2 if result.hard < 1.0]
        successes2 = [(task, result) for task, result in train_pairs2 if result.hard >= 1.0]
        edits_m = backend.reflect(
            failures2,
            successes2,
            cand_skill,
            cand_memory,
            edit_budget=edit_budget,
            evolve_skill=False,
            evolve_memory=True,
        )
        cand_memory = _gate_apply(cand_memory, edits_m, "memory")

    if gate_off:
        accepted = bool(all_applied)
        action = "greedy_applied" if all_applied else "greedy_noop"
        final_score = 0.0
        base_gate_score = 0.0
    else:
        final_pairs = replay_batch(backend, val_tasks, cand_skill, cand_memory)
        final_hard, final_soft = aggregate_scores(final_pairs)
        final_score = select_gate_score(final_hard, final_soft, gate_metric, gate_mixed_weight)
        base_gate_score = select_gate_score(base_hard, base_soft, gate_metric, gate_mixed_weight)
        final_deltas = _task_deltas(val_tasks, base_pairs, final_pairs, gate_metric, gate_mixed_weight)
        blocked_final = bool(gate_no_regression and any(row["status"] == "regressed" for row in final_deltas))
        gate = evaluate_gate(
            candidate_skill=cand_skill,
            cand_hard=final_hard,
            current_skill=skill,
            current_score=base_gate_score,
            best_skill=skill,
            best_score=base_gate_score,
            best_step=night - 1,
            global_step=night,
            cand_soft=final_soft,
            metric=gate_metric,
            mixed_weight=gate_mixed_weight,
        )
        action = gate.action
        accepted = bool(all_applied) and final_score > base_gate_score and not blocked_final
        if holdout_leaked:
            # Do not certify when val overlaps train.
            accepted = False
            action = "reject_holdout_leaked"

    if not accepted:
        cand_skill, cand_memory = skill, memory

    return ConsolidationResult(
        accepted=accepted,
        gate_action=action,
        baseline_score=base_gate_score if not gate_off else 0.0,
        candidate_score=final_score if not gate_off else 0.0,
        new_skill=cand_skill,
        new_memory=cand_memory,
        applied_edits=all_applied,
        rejected_edits=all_rejected,
        holdout_baseline=base_gate_score if not gate_off else 0.0,
        holdout_candidate=final_score if not gate_off else 0.0,
        unmatched_edits=all_unmatched,
        holdout_leaked=holdout_leaked,
        gate_trials=gate_trials,
    )
