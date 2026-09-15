# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Nightly consolidation entry: reflect -> gated edit -> held-out verdict."""

from __future__ import annotations

from typing import List

from openjiuwen.agent_evolving.skill_train.sleep.backend import Backend
from openjiuwen.agent_evolving.skill_train.sleep.gate import select_gate_score
from openjiuwen.agent_evolving.skill_train.sleep.holdout import partition_tasks
from openjiuwen.agent_evolving.skill_train.sleep.night_pass import (
    NightBoard,
    NightKnobs,
    NightOutcome,
    evolve_one_target,
    finalize_night,
)
from openjiuwen.agent_evolving.skill_train.sleep.replay import aggregate_scores, replay_batch
from openjiuwen.agent_evolving.skill_train.sleep.types import TaskRecord

# Public alias kept for callers/tests that import ConsolidationResult.
ConsolidationResult = NightOutcome
ConsolidateKnobs = NightKnobs


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
) -> NightOutcome:
    knobs = NightKnobs(
        edit_budget=edit_budget,
        gate_metric=gate_metric,
        gate_mixed_weight=gate_mixed_weight,
        gate_no_regression=gate_no_regression,
        gate_mode=gate_mode,
        evolve_skill=evolve_skill,
        evolve_memory=evolve_memory,
        night=night,
    )
    train_tasks, val_tasks, holdout_leaked = partition_tasks(tasks)

    base_pairs: list = []
    base_hard = 0.0
    base_soft = 0.0
    if not knobs.gate_disabled:
        base_pairs = replay_batch(backend, val_tasks, skill, memory)
        hard_soft = aggregate_scores(base_pairs)
        base_hard, base_soft = hard_soft[0], hard_soft[1]
    base_score = select_gate_score(
        base_hard,
        base_soft,
        knobs.gate_metric,
        knobs.gate_mixed_weight,
    )

    board = NightBoard(
        skill=skill,
        memory=memory,
        baseline_score=base_score,
        holdout_pairs=list(base_pairs),
    )

    if knobs.evolve_skill:
        evolve_one_target(
            backend,
            board,
            train_tasks=train_tasks,
            val_tasks=val_tasks,
            knobs=knobs,
            target="skill",
        )
    if knobs.evolve_memory:
        evolve_one_target(
            backend,
            board,
            train_tasks=train_tasks,
            val_tasks=val_tasks,
            knobs=knobs,
            target="memory",
        )

    return finalize_night(
        board,
        original_skill=skill,
        original_memory=memory,
        base_hard=base_hard,
        base_soft=base_soft,
        initial_holdout=base_pairs,
        val_tasks=val_tasks,
        backend=backend,
        knobs=knobs,
        holdout_leaked=holdout_leaked,
    )
