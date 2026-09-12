# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Skill-train selection gate for candidate skill documents.

Compares a candidate's rollout score against the active skill and the
session best, then returns a structured accept or reject outcome.

Modes: ``hard``, ``soft``, ``mixed`` (convex blend via ``mixed_weight``).
"""

from __future__ import annotations

import re
from typing import Callable, Literal, NamedTuple

GateAction = Literal["accept_new_best", "accept", "reject"]
GateMetric = Literal["hard", "soft", "mixed"]

_MARKER_PAIRS: tuple[tuple[str, str], ...] = (
    ("<!-- SLOW_UPDATE_START -->", "<!-- SLOW_UPDATE_END -->"),
    ("<!-- APPENDIX_START -->", "<!-- APPENDIX_END -->"),
)

_DEFAULT_PRIORITY_TERMS = frozenset(
    {
        "must",
        "always",
        "never",
        "only",
        "critical",
        "important",
        "resolve",
        "prefer",
        "ensure",
        "strict",
        "verify",
    }
)

_TOKEN_RE = re.compile(r"[a-zA-Z0-9]+")


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
            action="reject",
            current_skill=self.current_skill,
            current_score=self.current_score,
            best_skill=self.best_skill,
            best_score=self.best_score,
            best_step=self.best_step,
        )

    def accept(self, skill: str, score: float) -> GateResult:
        return GateResult(
            action="accept",
            current_skill=skill,
            current_score=score,
            best_skill=self.best_skill,
            best_score=self.best_score,
            best_step=self.best_step,
        )

    def accept_new_best(self, skill: str, score: float, step: int) -> GateResult:
        return GateResult(
            action="accept_new_best",
            current_skill=skill,
            current_score=score,
            best_skill=skill,
            best_score=score,
            best_step=step,
        )


class GateTuning(NamedTuple):
    """Optional knobs for score projection and density bonus."""

    soft: float = 0.0
    metric: GateMetric = "hard"
    mixed_weight: float = 0.5
    use_semantic_density: bool = False
    semantic_density_weight: float = 0.05
    leading_words: list[str] | None = None


def _drop_marked_regions(text: str) -> str:
    body = text
    for open_tag, close_tag in _MARKER_PAIRS:
        while True:
            start = body.find(open_tag)
            if start < 0:
                break
            end = body.find(close_tag, start)
            if end < 0:
                after_open = start + len(open_tag)
                body = body[:start] + body[after_open:]
                break
            after_close = end + len(close_tag)
            body = body[:start] + body[after_close:]
    return body


def compute_semantic_density(
    skill_content: str,
    leading_words: list[str] | None = None,
) -> float:
    """Fraction of tokens that belong to the priority lexicon."""
    if skill_content is None or not str(skill_content).strip():
        return 0.0
    lexicon = (
        {term.lower() for term in leading_words}
        if leading_words is not None
        else _DEFAULT_PRIORITY_TERMS
    )
    tokens = _TOKEN_RE.findall(_drop_marked_regions(str(skill_content)).lower())
    if not tokens:
        return 0.0
    hits = 0
    for token in tokens:
        if token in lexicon:
            hits += 1
    return hits / len(tokens)


def _clamp01(value: float) -> float:
    return 0.0 if value < 0.0 else 1.0 if value > 1.0 else value


def _score_hard(hard: float, soft: float, weight: float) -> float:
    del soft, weight
    return float(hard)


def _score_soft(hard: float, soft: float, weight: float) -> float:
    del hard, weight
    return float(soft)


def _score_mixed(hard: float, soft: float, weight: float) -> float:
    blend = _clamp01(float(weight))
    return (1.0 - blend) * float(hard) + blend * float(soft)


_METRIC_PROJECTORS: dict[str, Callable[[float, float, float], float]] = {
    "hard": _score_hard,
    "soft": _score_soft,
    "mixed": _score_mixed,
}


def select_gate_score(
    hard: float,
    tuning: GateTuning | None = None,
    skill_content: str = "",
) -> float:
    """Project hard/soft rollout aggregates onto one comparable scalar."""
    opts = tuning if tuning is not None else GateTuning()
    projector = _METRIC_PROJECTORS.get(str(opts.metric))
    if projector is None:
        allowed = ", ".join(repr(name) for name in ("hard", "soft", "mixed"))
        raise ValueError(f"unsupported gate metric {opts.metric!r}; choose one of {allowed}")
    projected = projector(hard, opts.soft, opts.mixed_weight)
    if opts.use_semantic_density:
        projected = projected + float(opts.semantic_density_weight) * compute_semantic_density(
            skill_content, opts.leading_words
        )
    return projected


def evaluate_gate(
    candidate_skill: str,
    cand_hard: float,
    state: GateState,
    global_step: int,
    tuning: GateTuning | None = None,
) -> GateResult:
    """Accept or reject ``candidate_skill`` relative to ``state``."""
    opts = tuning if tuning is not None else GateTuning()
    projected = select_gate_score(cand_hard, opts, skill_content=candidate_skill)
    if projected <= state.current_score:
        return state.rejected()
    if projected <= state.best_score:
        return state.accept(candidate_skill, projected)
    return state.accept_new_best(candidate_skill, projected, global_step)
