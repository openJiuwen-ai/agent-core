# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Per-document gated edit trials and night finalization."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import List, Tuple

from openjiuwen.agent_evolving.skill_train.sleep.backend import Backend
from openjiuwen.agent_evolving.skill_train.sleep.gate import SleepGateSnapshot, evaluate_gate, select_gate_score
from openjiuwen.agent_evolving.skill_train.sleep.holdout import any_regression, compare_task_scores
from openjiuwen.agent_evolving.skill_train.sleep.memory import apply_edits_detailed
from openjiuwen.agent_evolving.skill_train.sleep.replay import aggregate_scores, replay_batch
from openjiuwen.agent_evolving.skill_train.sleep.types import EditRecord, ReplayResult, TaskRecord

PairList = List[Tuple[TaskRecord, ReplayResult]]


@dataclass(frozen=True)
class NightKnobs:
    edit_budget: int = 4
    gate_metric: str = "mixed"
    gate_mixed_weight: float = 0.5
    gate_no_regression: bool = False
    gate_mode: str = "on"
    evolve_skill: bool = True
    evolve_memory: bool = False
    night: int = 1

    @property
    def gate_disabled(self) -> bool:
        return str(self.gate_mode).strip().lower() in {"off", "none", "false", "greedy"}


@dataclass
class NightOutcome:
    """Public consolidation result (alias: ConsolidationResult)."""

    accepted: bool
    gate_action: str
    new_skill: str
    new_memory: str
    applied_edits: List[EditRecord]
    rejected_edits: List[EditRecord]
    baseline_score: float = 0.0
    candidate_score: float = 0.0
    holdout_baseline: float = 0.0
    holdout_candidate: float = 0.0
    unmatched_edits: List[EditRecord] = field(default_factory=list)
    holdout_leaked: bool = False
    gate_trials: List[dict] = field(default_factory=list)


@dataclass
class NightBoard:
    skill: str
    memory: str
    baseline_score: float
    holdout_pairs: PairList
    applied: List[EditRecord] = field(default_factory=list)
    rejected: List[EditRecord] = field(default_factory=list)
    unmatched: List[EditRecord] = field(default_factory=list)
    trials: List[dict] = field(default_factory=list)


def _json_safe(value: float) -> float | None:
    return value if math.isfinite(value) else None


def split_fail_win(pairs: PairList) -> Tuple[PairList, PairList]:
    fails = [(task, result) for task, result in pairs if result.hard < 1.0]
    wins = [(task, result) for task, result in pairs if result.hard >= 1.0]
    return fails, wins


def trial_document(
    backend: Backend,
    board: NightBoard,
    *,
    document: str,
    edits: List[EditRecord],
    target: str,
    val_tasks: List[TaskRecord],
    knobs: NightKnobs,
) -> str:
    if not edits:
        return document
    revised, matched, leftover = apply_edits_detailed(document, edits)
    if leftover:
        board.unmatched.extend(leftover)
    if not matched:
        return document
    if knobs.gate_disabled:
        board.applied.extend(matched)
        return revised

    trial_skill = revised if target == "skill" else board.skill
    trial_memory = revised if target == "memory" else board.memory
    pairs = replay_batch(backend, val_tasks, trial_skill, trial_memory)
    hard, soft = aggregate_scores(pairs)
    cand = select_gate_score(hard, soft, knobs.gate_metric, knobs.gate_mixed_weight)
    deltas = compare_task_scores(
        val_tasks,
        board.holdout_pairs,
        pairs,
        knobs.gate_metric,
        knobs.gate_mixed_weight,
    )
    blocked = bool(knobs.gate_no_regression and any_regression(deltas))
    improved = cand > board.baseline_score and not blocked
    board.trials.append(
        {
            "target": target,
            "baseline_score": _json_safe(board.baseline_score),
            "candidate_score": _json_safe(cand),
            "accepted": improved,
            "blocked_by_regression": blocked,
            "task_deltas": deltas,
        }
    )
    if not improved:
        board.rejected.extend(matched)
        return document
    board.baseline_score = cand
    board.holdout_pairs = pairs
    board.applied.extend(matched)
    return revised


def evolve_one_target(
    backend: Backend,
    board: NightBoard,
    *,
    train_tasks: List[TaskRecord],
    val_tasks: List[TaskRecord],
    knobs: NightKnobs,
    target: str,
) -> None:
    pairs = replay_batch(backend, train_tasks, board.skill, board.memory)
    fails, wins = split_fail_win(pairs)
    evolve_skill = target == "skill"
    edits = backend.reflect(
        fails,
        wins,
        board.skill,
        board.memory,
        edit_budget=knobs.edit_budget,
        evolve_skill=evolve_skill,
        evolve_memory=not evolve_skill,
    )
    if evolve_skill:
        board.skill = trial_document(
            backend,
            board,
            document=board.skill,
            edits=edits,
            target="skill",
            val_tasks=val_tasks,
            knobs=knobs,
        )
        return
    board.memory = trial_document(
        backend,
        board,
        document=board.memory,
        edits=edits,
        target="memory",
        val_tasks=val_tasks,
        knobs=knobs,
    )


def finalize_night(
    board: NightBoard,
    *,
    original_skill: str,
    original_memory: str,
    base_hard: float,
    base_soft: float,
    initial_holdout: PairList,
    val_tasks: List[TaskRecord],
    backend: Backend,
    knobs: NightKnobs,
    holdout_leaked: bool,
) -> NightOutcome:
    if knobs.gate_disabled:
        accepted = bool(board.applied)
        action = "greedy_applied" if board.applied else "greedy_noop"
        return NightOutcome(
            accepted=accepted,
            gate_action=action,
            baseline_score=0.0,
            candidate_score=0.0,
            new_skill=board.skill if accepted else original_skill,
            new_memory=board.memory if accepted else original_memory,
            applied_edits=board.applied,
            rejected_edits=board.rejected,
            holdout_baseline=0.0,
            holdout_candidate=0.0,
            unmatched_edits=board.unmatched,
            holdout_leaked=holdout_leaked,
            gate_trials=board.trials,
        )

    final_pairs = replay_batch(backend, val_tasks, board.skill, board.memory)
    final_hard, final_soft = aggregate_scores(final_pairs)
    final_score = select_gate_score(final_hard, final_soft, knobs.gate_metric, knobs.gate_mixed_weight)
    base_gate = select_gate_score(base_hard, base_soft, knobs.gate_metric, knobs.gate_mixed_weight)
    final_deltas = compare_task_scores(
        val_tasks,
        initial_holdout,
        final_pairs,
        knobs.gate_metric,
        knobs.gate_mixed_weight,
    )
    blocked_final = bool(knobs.gate_no_regression and any_regression(final_deltas))
    snapshot = SleepGateSnapshot(
        current_skill=original_skill,
        current_score=base_gate,
        best_skill=original_skill,
        best_score=base_gate,
        best_step=knobs.night - 1,
    )
    gate = evaluate_gate(
        board.skill,
        final_hard,
        snapshot,
        knobs.night,
        cand_soft=final_soft,
        metric=knobs.gate_metric,
        mixed_weight=knobs.gate_mixed_weight,
    )
    action = gate.action
    accepted = bool(board.applied) and final_score > base_gate and not blocked_final
    if holdout_leaked:
        accepted = False
        action = "reject_holdout_leaked"
    if not accepted:
        board.skill = original_skill
        board.memory = original_memory
    return NightOutcome(
        accepted=accepted,
        gate_action=action,
        baseline_score=base_gate,
        candidate_score=final_score,
        new_skill=board.skill,
        new_memory=board.memory,
        applied_edits=board.applied,
        rejected_edits=board.rejected,
        holdout_baseline=base_gate,
        holdout_candidate=final_score,
        unmatched_edits=board.unmatched,
        holdout_leaked=holdout_leaked,
        gate_trials=board.trials,
    )
