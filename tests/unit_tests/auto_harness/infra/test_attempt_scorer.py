# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""test_attempt_scorer — AttemptScorer 单元测试。"""

from __future__ import annotations

from pathlib import Path
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock

from openjiuwen.auto_harness.infra.attempt_scorer import (
    AttemptScore,
    AttemptScorer,
)


class _FakeCIRunner:
    """Fake CI runner that returns deterministic results."""

    def __init__(self, passed: bool, tests_passed: int, tests_total: int):
        self._passed = passed
        self._tests_passed = tests_passed
        self._tests_total = tests_total

    async def run(self, action: str) -> dict:
        return {
            "passed": self._passed,
            "gates": [
                {
                    "name": "test",
                    "passed": self._passed,
                    "tests_passed": self._tests_passed,
                    "tests_total": self._tests_total,
                },
            ],
            "errors": "",
        }


class TestAttemptScore(IsolatedAsyncioTestCase):
    def test_ordering(self):
        a = AttemptScore(5, 10, 20, 0)
        b = AttemptScore(3, 10, 10, 0)
        c = AttemptScore(5, 10, 15, 0)
        d = AttemptScore(5, 10, 20, 1)

        assert a > b  # more tests passed
        assert c > a  # same tests, smaller diff
        assert a > d  # same tests/diff, fewer lint

    def test_equality(self):
        a = AttemptScore(5, 10, 20, 0)
        b = AttemptScore(5, 10, 20, 0)
        assert a == b


class TestAttemptScorer(IsolatedAsyncioTestCase):
    async def test_score_with_ci_runner(self):
        ci = _FakeCIRunner(passed=True, tests_passed=8, tests_total=10)

        scorer = AttemptScorer()
        score = await scorer.score("/tmp/fake", ci_runner=ci)

        assert score.tests_passed == 8
        assert score.tests_total == 10

    async def test_score_ci_runner_factory(self):
        ci = _FakeCIRunner(passed=False, tests_passed=2, tests_total=10)
        factory = lambda path: ci  # noqa: E731

        scorer = AttemptScorer(ci_runner_factory=factory)
        score = await scorer.score("/tmp/fake")

        assert score.tests_passed == 2
        assert score.tests_total == 10

    async def test_score_ci_failure_graceful(self):
        ci = AsyncMock()
        ci.run.side_effect = RuntimeError("CI broken")

        scorer = AttemptScorer()
        score = await scorer.score("/tmp/fake", ci_runner=ci)

        assert score.tests_passed == 0
        assert score.tests_total == 0

    async def test_score_without_ci_runner(self):
        scorer = AttemptScorer()
        score = await scorer.score("/tmp/fake")

        assert score.tests_passed == 0
        assert score.tests_total == 0
