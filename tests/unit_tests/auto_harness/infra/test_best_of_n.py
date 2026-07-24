# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""test_best_of_n — BestOfNController 单元测试。"""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest import IsolatedAsyncioTestCase

from openjiuwen.auto_harness.infra.attempt_scorer import (
    AttemptScore,
    AttemptScorer,
    ScoredAttempt,
)
from openjiuwen.auto_harness.infra.attempt_selector import BestOfNSelector
from openjiuwen.auto_harness.infra.best_of_n import (
    BestOfNController,
    BestOfNResult,
)
from openjiuwen.auto_harness.infra.workspace_cloner import WorkspaceCloner


class _FakeCIRunner:
    def __init__(self, passed: bool = False):
        self._passed = passed

    async def run(self, action: str) -> dict:
        return {
            "passed": self._passed,
            "gates": [],
            "errors": "",
        }


class TestBestOfNController(IsolatedAsyncioTestCase):
    async def test_all_attempts_fail(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp) / "ws"
            workspace.mkdir()

            ctrl = BestOfNController(
                n_attempts=2,
                timeout_per_attempt=5.0,
            )

            async def attempt_factory(path: Path, seed: int):
                pass

            async def ci_runner():
                return _FakeCIRunner(passed=False)

            result = await ctrl.run(
                workspace=workspace,
                attempt_factory=attempt_factory,
                ci_runner=ci_runner,
            )

            assert isinstance(result, BestOfNResult)
            assert result.success is False
            assert len(result.all_attempts) == 2
            assert result.best is not None

    async def test_best_attempt_selected(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp) / "ws"
            workspace.mkdir()

            # Create a scorer that returns increasing scores per attempt
            class _SeedScorer(AttemptScorer):
                async def score(self, workspace, ci_runner=None):
                    # Derive score from path name (attempt-N)
                    idx = int(Path(workspace).name.split("-")[-1])
                    return AttemptScore(
                        tests_passed=idx + 1,
                        tests_total=5,
                        diff_lines=10,
                        lint_errors=0,
                    )

            ctrl = BestOfNController(
                n_attempts=3,
                timeout_per_attempt=5.0,
                scorer=_SeedScorer(),
                selector=BestOfNSelector(),
            )

            async def attempt_factory(path: Path, seed: int):
                pass

            async def ci_runner():
                return _FakeCIRunner(passed=False)

            result = await ctrl.run(
                workspace=workspace,
                attempt_factory=attempt_factory,
                ci_runner=ci_runner,
            )

            assert result.best is not None
            # Best should be attempt 2 (highest seed => highest score)
            assert result.best.attempt_index == 2
            assert result.best.score.tests_passed == 3

    async def test_promotes_best_workspace(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp) / "ws"
            workspace.mkdir()
            (workspace / "original.txt").write_text("orig")

            ctrl = BestOfNController(
                n_attempts=2,
                timeout_per_attempt=5.0,
            )

            async def attempt_factory(path: Path, seed: int):
                marker = path / "marker.txt"
                marker.write_text(f"attempt-{seed}")

            async def ci_runner():
                return _FakeCIRunner(passed=False)

            result = await ctrl.run(
                workspace=workspace,
                attempt_factory=attempt_factory,
                ci_runner=ci_runner,
            )

            # Best workspace should be promoted back
            assert result.best is not None
            best_marker = workspace / "marker.txt"
            assert best_marker.exists()
            expected = f"attempt-{result.best.attempt_index}"
            assert best_marker.read_text() == expected

    async def test_cleans_up_losers(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp) / "ws"
            workspace.mkdir()

            ctrl = BestOfNController(
                n_attempts=3,
                timeout_per_attempt=5.0,
            )

            async def attempt_factory(path: Path, seed: int):
                pass

            async def ci_runner():
                return _FakeCIRunner(passed=False)

            result = await ctrl.run(
                workspace=workspace,
                attempt_factory=attempt_factory,
                ci_runner=ci_runner,
            )

            # Only the winning workspace should exist in parent dir
            # (original ws is replaced by promotion, losers deleted)
            parent_items = list(workspace.parent.iterdir())
            loser_dirs = [
                p for p in parent_items
                if p.is_dir() and p.name.startswith("ws-attempt-")
            ]
            assert len(loser_dirs) == 0
