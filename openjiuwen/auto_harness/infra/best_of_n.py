# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Best-of-N controller — run N fix attempts, score, and promote the best.

This controller plugs into the auto-harness verify stage as an alternative
to the classic two-phase fix loop.  When CI fails after the initial
implementation, instead of incrementally fixing errors, it generates *N*
independent fix attempts (each with different randomness), scores the
resulting workspaces, and promotes the best one.
"""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Coroutine

from openjiuwen.auto_harness.infra.attempt_scorer import (
    AttemptScorer,
    AttemptScore,
    ScoredAttempt,
)
from openjiuwen.auto_harness.infra.attempt_selector import (
    AttemptSelector,
    BestOfNSelector,
)
from openjiuwen.auto_harness.infra.workspace_cloner import (
    ClonedWorkspace,
    WorkspaceCloner,
)

logger = logging.getLogger(__name__)


@dataclass
class BestOfNResult:
    """Outcome of a best-of-N run."""

    success: bool = False
    best: ScoredAttempt | None = None
    all_attempts: list[ScoredAttempt] = field(default_factory=list)
    error_log: list[str] = field(default_factory=list)


class BestOfNController:
    """Run N independent fix attempts and return the best scored result.

    Typical flow:

    1. Clone the current workspace *N* times.
    2. For each clone, run *attempt_factory(clone_path, seed)*.
    3. Run the CI suite on each clone and score it.
    4. Select the best candidate via *selector*.
    5. Promote the best clone back to the original workspace.
    6. Clean up the remaining clones.
    """

    def __init__(
        self,
        n_attempts: int = 3,
        timeout_per_attempt: float = 600.0,
        cloner: WorkspaceCloner | None = None,
        scorer: AttemptScorer | None = None,
        selector: AttemptSelector | None = None,
    ) -> None:
        self._n = max(1, n_attempts)
        self._timeout = timeout_per_attempt
        self._cloner = cloner or WorkspaceCloner()
        self._scorer = scorer or AttemptScorer()
        self._selector = selector or BestOfNSelector()

    async def run(
        self,
        workspace: Path | str,
        attempt_factory: Callable[[Path, int], Coroutine[Any, Any, Any]],
        ci_runner: Callable[[], Coroutine[Any, Any, Any]],
    ) -> BestOfNResult:
        """Execute the best-of-N pipeline.

        Args:
            workspace: Base workspace path (read-only reference).
            attempt_factory: ``async def attempt_factory(path: Path, seed: int)``.
                Called once per clone.  Should run the fix agent inside *path*.
            ci_runner: Callable that runs CI on the **current** workspace.
        The controller temporarily ``chdir`` into each clone before
        calling it.

        Returns:
            BestOfNResult with success flag and scored attempts.
        """
        original = Path(workspace).resolve()
        result = BestOfNResult()
        original_cwd = Path.cwd()

        # 1. Clone
        try:
            clones = await self._cloner.clone_n_async(original, self._n)
        except Exception as exc:
            msg = f"[BestOfN] Workspace cloning failed: {exc}"
            logger.error(msg)
            result.error_log.append(msg)
            return result

        try:
            for cloned in clones:
                # Each attempt runs sequentially so cwd switching is safe.
                os.chdir(str(cloned.path))
                idx = cloned.index

                logger.info(
                    "[BestOfN] Attempt %d/%d in %s",
                    idx + 1,
                    self._n,
                    cloned.path,
                )

                try:
                    # 2. Run attempt
                    await asyncio.wait_for(
                        attempt_factory(cloned.path, idx),
                        timeout=self._timeout,
                    )

                    # 3. Score
                    score = await asyncio.wait_for(
                        self._scorer.score(cloned.path),
                        timeout=self._timeout,
                    )
                except asyncio.TimeoutError:
                    msg = f"Attempt {idx}: timeout"
                    logger.warning(msg)
                    result.error_log.append(msg)
                    score = AttemptScore(0, 0, 0, 0)
                except Exception as exc:
                    msg = f"Attempt {idx}: {exc}"
                    logger.warning(msg)
                    result.error_log.append(msg)
                    score = AttemptScore(0, 0, 0, 0)

                scored = ScoredAttempt(
                    score=score,
                    workspace=cloned.path,
                    attempt_index=idx,
                )
                result.all_attempts.append(scored)
                logger.info(
                    "[BestOfN] Attempt %d scored: %s",
                    idx,
                    score,
                )

            # 4. Select best
            if not result.all_attempts:
                result.error_log.append("No attempts were scored")
                return result

            result.best = self._selector.select(result.all_attempts)
            result.success = result.best.score.tests_passed > 0

            if result.best:
                logger.info(
                    "[BestOfN] Best attempt: %d (workspace=%s)",
                    result.best.attempt_index,
                    result.best.workspace,
                )
                # 5. Promote best
                self._cloner.promote(
                    ClonedWorkspace(
                        path=result.best.workspace,
                        original=original,
                        index=result.best.attempt_index,
                    ),
                )

        finally:
            os.chdir(str(original_cwd))
            # 6. Clean up losers
            best_path = (
                result.best.workspace.resolve()
                if result.best else None
            )
            for cloned in clones:
                if best_path and cloned.path.resolve() == best_path:
                    continue
                self._cloner.remove(cloned)

        return result
