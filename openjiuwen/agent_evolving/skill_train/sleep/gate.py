# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Held-out gate helpers for sleep consolidation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

GateAction = Literal["accept_new_best", "accept", "reject"]


@dataclass(frozen=True)
class GateResult:
    """Outcome of one sleep gate decision."""

    action: str
    skill_doc: str
    score: float
    champion_skill: str
    champion_score: float
    champion_step: int

    # Back-compat aliases used by consolidate / night_pass / reports.
    @property
    def current_skill(self) -> str:
        return self.skill_doc

    @property
    def current_score(self) -> float:
        return self.score

    @property
    def best_skill(self) -> str:
        return self.champion_skill

    @property
    def best_score(self) -> float:
        return self.champion_score

    @property
    def best_step(self) -> int:
        return self.champion_step


@dataclass(frozen=True)
class SleepGateSnapshot:
    """Bundles the live / best skill scores used by ``evaluate_gate``."""

    current_skill: str
    current_score: float
    best_skill: str
    best_score: float
    best_step: int

    def as_reject(self) -> GateResult:
        return GateResult(
            action="reject",
            skill_doc=self.current_skill,
            score=self.current_score,
            champion_skill=self.best_skill,
            champion_score=self.best_score,
            champion_step=self.best_step,
        )


def select_gate_score(
    hard: float,
    soft: float,
    metric: str = "hard",
    mixed_weight: float = 0.5,
) -> float:
    kind = (metric or "hard").strip().lower()
    if kind == "hard":
        return float(hard)
    if kind == "soft":
        return float(soft)
    if kind == "mixed":
        weight = max(0.0, min(1.0, float(mixed_weight)))
        return (1.0 - weight) * float(hard) + weight * float(soft)
    raise ValueError(f"unknown gate metric {metric!r}; expected hard/soft/mixed")


def evaluate_gate(
    candidate_skill: str,
    cand_hard: float,
    snapshot: SleepGateSnapshot,
    global_step: int,
    *,
    cand_soft: float = 0.0,
    metric: str = "hard",
    mixed_weight: float = 0.5,
) -> GateResult:
    cand_score = select_gate_score(cand_hard, cand_soft, metric, mixed_weight)
    if cand_score <= snapshot.current_score:
        return snapshot.as_reject()
    beats_champion = cand_score > snapshot.best_score
    if beats_champion:
        return GateResult(
            action="accept_new_best",
            skill_doc=candidate_skill,
            score=cand_score,
            champion_skill=candidate_skill,
            champion_score=cand_score,
            champion_step=global_step,
        )
    return GateResult(
        action="accept",
        skill_doc=candidate_skill,
        score=cand_score,
        champion_skill=snapshot.best_skill,
        champion_score=snapshot.best_score,
        champion_step=snapshot.best_step,
    )
