# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Attempt selector — picks the best attempt from N scored candidates."""

from __future__ import annotations

import logging
from typing import Sequence

from openjiuwen.auto_harness.infra.attempt_scorer import (
    ScoredAttempt,
)

logger = logging.getLogger(__name__)


class AttemptSelector:
    """Abstract base for attempt selection strategies."""

    def select(self, candidates: Sequence[ScoredAttempt]) -> ScoredAttempt:
        """Return the best candidate.  Must not mutate *candidates*."""
        raise NotImplementedError


class BestOfNSelector(AttemptSelector):
    """Select by: max tests passed → min diff lines → min lint errors.

    This is the standard ``best-of-N`` heuristic used by top SWE-Bench
    systems.
    """

    def select(self, candidates: Sequence[ScoredAttempt]) -> ScoredAttempt:
        if not candidates:
            raise ValueError("No candidates to select from")

        best = max(candidates, key=lambda c: c.score)
        logger.info(
            "[BestOfNSelector] Selected attempt %d "
            "(tests=%d/%d, diff=%d, lint=%d)",
            best.attempt_index,
            best.score.tests_passed,
            best.score.tests_total,
            best.score.diff_lines,
            best.score.lint_errors,
        )
        return best


class PassRateSelector(AttemptSelector):
    """Select by highest pass ratio (tests_passed / tests_total).

    Useful when tasks have highly variable test suite sizes.
    """

    def select(self, candidates: Sequence[ScoredAttempt]) -> ScoredAttempt:
        if not candidates:
            raise ValueError("No candidates to select from")

        def _ratio(c: ScoredAttempt) -> float:
            if c.score.tests_total == 0:
                return 0.0
            return c.score.tests_passed / c.score.tests_total

        best = max(candidates, key=lambda c: (_ratio(c), -c.score.diff_lines))
        logger.info(
            "[PassRateSelector] Selected attempt %d "
            "(pass_rate=%.2f, diff=%d)",
            best.attempt_index,
            _ratio(best),
            best.score.diff_lines,
        )
        return best
