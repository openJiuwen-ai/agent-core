# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Skill-train selection gate for candidate skill documents.

Compares a candidate's rollout score against the active skill and the
session best, then returns a structured accept or reject outcome.

Modes: ``hard``, ``soft``, ``mixed`` (convex blend via ``mixed_weight``).
"""

from __future__ import annotations

import re
from typing import Literal, NamedTuple

GateAction = Literal["accept_new_best", "accept", "reject"]
GateMetric = Literal["hard", "soft", "mixed"]

_SPAN_PAIRS = (
    ("<!-- SLOW_UPDATE_START -->", "<!-- SLOW_UPDATE_END -->"),
    ("<!-- APPENDIX_START -->", "<!-- APPENDIX_END -->"),
)

_DEFAULT_LEXICON = frozenset(
    "must always never only critical important resolve prefer ensure strict verify".split()
)


class GateResult(NamedTuple):
    """Outcome of a single gate evaluation."""

    action: GateAction
    current_skill: str
    current_score: float
    best_skill: str
    best_score: float
    best_step: int


class GateState(NamedTuple):
    """Current and best skill snapshots used by the selection gate."""

    current_skill: str
    current_score: float
    best_skill: str
    best_score: float
    best_step: int

    def rejected(self) -> GateResult:
        return GateResult(
            "reject",
            self.current_skill,
            self.current_score,
            self.best_skill,
            self.best_score,
            self.best_step,
        )


def _without_spans(text: str) -> str:
    body = text
    for left, right in _SPAN_PAIRS:
        while True:
            i = body.find(left)
            if i < 0:
                break
            j = body.find(right, i)
            if j < 0:
                cut = i + len(left)
                body = body[:i] + body[cut:]
                break
            cut = j + len(right)
            body = body[:i] + body[cut:]
    return body


def compute_semantic_density(
    skill_content: str,
    leading_words: list[str] | None = None,
) -> float:
    if not (skill_content and skill_content.strip()):
        return 0.0
    lexicon = {w.lower() for w in leading_words} if leading_words is not None else _DEFAULT_LEXICON
    tokens = re.findall(r"[a-zA-Z0-9]+", _without_spans(skill_content).lower())
    return 0.0 if not tokens else sum(t in lexicon for t in tokens) / len(tokens)


def select_gate_score(
    hard: float,
    soft: float,
    metric: GateMetric = "hard",
    mixed_weight: float = 0.5,
    *,
    skill_content: str = "",
    use_semantic_density: bool = False,
    semantic_density_weight: float = 0.05,
    leading_words: list[str] | None = None,
) -> float:
    if metric == "hard":
        score = float(hard)
    elif metric == "soft":
        score = float(soft)
    elif metric == "mixed":
        w = max(0.0, min(1.0, float(mixed_weight)))
        score = (1.0 - w) * float(hard) + w * float(soft)
    else:
        raise ValueError(f"unknown gate metric {metric!r}; expected 'hard', 'soft', or 'mixed'")
    if use_semantic_density:
        score += float(semantic_density_weight) * compute_semantic_density(skill_content, leading_words)
    return score


def evaluate_gate(
    candidate_skill: str,
    cand_hard: float,
    state: GateState,
    global_step: int,
    **opts: object,
) -> GateResult:
    soft = float(opts.get("cand_soft", 0.0))  # type: ignore[arg-type]
    metric = opts.get("metric", "hard")  # type: ignore[assignment]
    mixed_weight = float(opts.get("mixed_weight", 0.5))  # type: ignore[arg-type]
    use_density = bool(opts.get("use_semantic_density", False))
    density_weight = float(opts.get("semantic_density_weight", 0.05))  # type: ignore[arg-type]
    leading = opts.get("leading_words")  # type: ignore[assignment]
    score = select_gate_score(
        cand_hard,
        soft,
        metric,  # type: ignore[arg-type]
        mixed_weight,
        skill_content=candidate_skill,
        use_semantic_density=use_density,
        semantic_density_weight=density_weight,
        leading_words=leading,  # type: ignore[arg-type]
    )
    if score <= state.current_score:
        return state.rejected()
    if score <= state.best_score:
        return GateResult(
            "accept",
            candidate_skill,
            score,
            state.best_skill,
            state.best_score,
            state.best_step,
        )
    return GateResult(
        "accept_new_best",
        candidate_skill,
        score,
        candidate_skill,
        score,
        global_step,
    )
