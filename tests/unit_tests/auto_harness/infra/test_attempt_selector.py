# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""test_attempt_selector — AttemptSelector 单元测试。"""

from __future__ import annotations

from pathlib import Path
from unittest import IsolatedAsyncioTestCase

from openjiuwen.auto_harness.infra.attempt_scorer import (
    AttemptScore,
    ScoredAttempt,
)
from openjiuwen.auto_harness.infra.attempt_selector import (
    BestOfNSelector,
    PassRateSelector,
)


class TestBestOfNSelector(IsolatedAsyncioTestCase):
    def test_single_candidate(self):
        selector = BestOfNSelector()
        candidates = [
            ScoredAttempt(
                score=AttemptScore(5, 10, 20, 0),
                workspace=Path("/tmp/ws"),
                attempt_index=0,
            ),
        ]
        best = selector.select(candidates)
        assert best.attempt_index == 0

    def test_max_tests_passed_wins(self):
        selector = BestOfNSelector()
        candidates = [
            ScoredAttempt(
                score=AttemptScore(3, 10, 10, 0),
                workspace=Path("/tmp/a"),
                attempt_index=0,
            ),
            ScoredAttempt(
                score=AttemptScore(7, 10, 50, 0),
                workspace=Path("/tmp/b"),
                attempt_index=1,
            ),
            ScoredAttempt(
                score=AttemptScore(5, 10, 5, 0),
                workspace=Path("/tmp/c"),
                attempt_index=2,
            ),
        ]
        best = selector.select(candidates)
        assert best.attempt_index == 1
        assert best.score.tests_passed == 7

    def test_tiebreak_smaller_diff(self):
        selector = BestOfNSelector()
        candidates = [
            ScoredAttempt(
                score=AttemptScore(5, 10, 100, 0),
                workspace=Path("/tmp/a"),
                attempt_index=0,
            ),
            ScoredAttempt(
                score=AttemptScore(5, 10, 20, 0),
                workspace=Path("/tmp/b"),
                attempt_index=1,
            ),
        ]
        best = selector.select(candidates)
        assert best.attempt_index == 1
        assert best.score.diff_lines == 20

    def test_tiebreak_fewer_lint(self):
        selector = BestOfNSelector()
        candidates = [
            ScoredAttempt(
                score=AttemptScore(5, 10, 20, 5),
                workspace=Path("/tmp/a"),
                attempt_index=0,
            ),
            ScoredAttempt(
                score=AttemptScore(5, 10, 20, 1),
                workspace=Path("/tmp/b"),
                attempt_index=1,
            ),
        ]
        best = selector.select(candidates)
        assert best.attempt_index == 1
        assert best.score.lint_errors == 1

    def test_empty_raises(self):
        selector = BestOfNSelector()
        with self.assertRaises(ValueError):
            selector.select([])


class TestPassRateSelector(IsolatedAsyncioTestCase):
    def test_higher_ratio_wins(self):
        selector = PassRateSelector()
        candidates = [
            ScoredAttempt(
                score=AttemptScore(5, 10, 10, 0),  # 0.5
                workspace=Path("/tmp/a"),
                attempt_index=0,
            ),
            ScoredAttempt(
                score=AttemptScore(8, 10, 50, 0),  # 0.8
                workspace=Path("/tmp/b"),
                attempt_index=1,
            ),
        ]
        best = selector.select(candidates)
        assert best.attempt_index == 1

    def test_ratio_tiebreak_diff(self):
        selector = PassRateSelector()
        candidates = [
            ScoredAttempt(
                score=AttemptScore(5, 10, 100, 0),  # 0.5
                workspace=Path("/tmp/a"),
                attempt_index=0,
            ),
            ScoredAttempt(
                score=AttemptScore(5, 10, 20, 0),  # 0.5
                workspace=Path("/tmp/b"),
                attempt_index=1,
            ),
        ]
        best = selector.select(candidates)
        assert best.attempt_index == 1
        assert best.score.diff_lines == 20
