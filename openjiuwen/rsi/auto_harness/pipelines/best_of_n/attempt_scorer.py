# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Attempt scorer — scores a solution workspace by test passes, diff size, lint."""

from __future__ import annotations

import logging
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger(__name__)


@dataclass(frozen=True, order=False)
class AttemptScore:
    """Scoring dimensions for a single attempt."""

    tests_passed: int
    tests_total: int
    diff_lines: int
    lint_errors: int

    def __lt__(self, other: "AttemptScore") -> bool:
        """Ranking: higher tests_passed wins, then smaller diff, then fewer lint errors."""
        if self.tests_passed != other.tests_passed:
            return self.tests_passed < other.tests_passed
        if self.diff_lines != other.diff_lines:
            return self.diff_lines > other.diff_lines  # smaller is better
        return self.lint_errors > other.lint_errors  # fewer is better

    def __le__(self, other: "AttemptScore") -> bool:
        return self == other or self < other

    def __gt__(self, other: "AttemptScore") -> bool:
        return not self <= other

    def __ge__(self, other: "AttemptScore") -> bool:
        return not self < other


@dataclass
class ScoredAttempt:
    """An attempt paired with its score."""

    score: AttemptScore
    workspace: Path
    attempt_index: int


class AttemptScorer:
    """Score a workspace by running tests and computing patch metrics."""

    def __init__(
        self,
        ci_runner_factory: Callable[[str], Any] | None = None,
        baseline_ref: str = "HEAD",
    ) -> None:
        """Args:
            ci_runner_factory: callable that takes a workspace path and returns
                a CIGateRunner-like object with ``run(action)``.
            baseline_ref: git ref to diff against for patch size.
        """
        self._ci_runner_factory = ci_runner_factory
        self._baseline_ref = baseline_ref

    async def score(
        self,
        workspace: Path | str,
        ci_runner: Any | None = None,
    ) -> AttemptScore:
        """Score a workspace.

        Args:
            workspace: Path to the workspace to score.
            ci_runner: Optional pre-built CI runner.  If not provided,
                ``ci_runner_factory`` is used.

        Returns:
            AttemptScore with test results and patch metrics.
        """
        ws = Path(workspace)
        if ci_runner is None and self._ci_runner_factory is not None:
            ci_runner = self._ci_runner_factory(str(ws))

        # Run tests
        tests_passed, tests_total = 0, 0
        if ci_runner is not None:
            try:
                result = await ci_runner.run("test")
                gates = result.get("gates", [])
                for gate in gates:
                    if gate.get("name") == "test":
                        tests_passed = gate.get("tests_passed", 0)
                        tests_total = gate.get("tests_total", 0)
                        break
                else:
                    # Fallback: derive from gate passed status only
                    tests_passed = 1 if result.get("passed", False) else 0
                    tests_total = 1
            except Exception as exc:
                logger.warning(
                    "[AttemptScorer] CI test run failed for %s: %s", ws, exc,
                )

        # Diff lines
        diff_lines = await self._count_diff_lines(ws)

        # Lint errors (quick check, don't fail if unavailable)
        lint_errors = await self._count_lint_errors(ws)

        return AttemptScore(
            tests_passed=tests_passed,
            tests_total=tests_total,
            diff_lines=diff_lines,
            lint_errors=lint_errors,
        )

    async def _count_diff_lines(self, workspace: Path) -> int:
        try:
            proc = await subprocess.create_subprocess_exec(
                "git",
                "diff",
                self._baseline_ref,
                "--stat",
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=str(workspace),
            )
            stdout, _ = await proc.communicate()
            text = stdout.decode("utf-8", errors="replace")
            # "1 file changed, 10 insertions(+), 3 deletions(-)"
            total = 0
            for line in text.splitlines():
                parts = line.split(",")
                for part in parts[1:]:
                    part = part.strip()
                    if "insertion" in part or "deletion" in part:
                        num = part.split()[0]
                        try:
                            total += int(num)
                        except ValueError:
                            pass
            return total
        except Exception as exc:
            logger.debug(
                "[AttemptScorer] Diff counting failed: %s", exc,
            )
            return 0

    async def _count_lint_errors(self, workspace: Path) -> int:
        try:
            proc = await subprocess.create_subprocess_exec(
                sys.executable,
                "-m",
                "ruff",
                "check",
                "--output-format",
                "json",
                ".",
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=str(workspace),
            )
            stdout, _ = await proc.communicate()
            import json
            violations = json.loads(stdout.decode("utf-8", errors="replace"))
            return len(violations)
        except Exception as exc:
            logger.debug(
                "[AttemptScorer] Lint counting failed: %s", exc,
            )
            return 0
