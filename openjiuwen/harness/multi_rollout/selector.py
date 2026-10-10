# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Result selectors — pick the best result from N rollouts."""

from __future__ import annotations

import logging
from typing import Any, Protocol

logger = logging.getLogger(__name__)


class RolloutResult:
    """Wrapper for a single rollout result with metadata."""

    def __init__(
        self,
        result: Any,
        attempt_index: int,
        exception: Exception | None = None,
    ):
        self.result = result
        self.attempt_index = attempt_index
        self.exception = exception

    @property
    def is_success(self) -> bool:
        return self.exception is None and self.result is not None

    @property
    def output_text(self) -> str:
        """Extract human-readable output for comparison."""
        if self.exception is not None:
            return ""
        if isinstance(self.result, dict):
            # Common result shapes
            for key in ("output", "content", "text", "answer"):
                if key in self.result:
                    val = self.result[key]
                    return str(val) if val is not None else ""
        return str(self.result) if self.result is not None else ""


class ResultSelector(Protocol):
    """Protocol for selecting the best rollout result."""

    def select(self, results: list[RolloutResult]) -> RolloutResult:
        """Return the best result.  Must not mutate *results*."""
        ...


class FirstSuccessfulSelector:
    """Return the first successful (non-exception) result.

    This is the fastest selector and a safe default.
    """

    @staticmethod
    def select(results: list[RolloutResult]) -> RolloutResult:
        if not results:
            raise ValueError("No rollout results to select from")

        for r in results:
            if r.is_success:
                logger.info(
                    "[FirstSuccessfulSelector] Selected attempt %d",
                    r.attempt_index,
                )
                return r

        # All failed — return first so caller sees the exception
        logger.warning(
            "[FirstSuccessfulSelector] All %d attempts failed; "
            "returning first for diagnostics",
            len(results),
        )
        return results[0]


class LongestOutputSelector:
    """Return the successful result with the longest output text.

    Useful when longer outputs indicate more complete solutions.
    """

    @staticmethod
    def select(results: list[RolloutResult]) -> RolloutResult:
        if not results:
            raise ValueError("No rollout results to select from")

        successes = [r for r in results if r.is_success]
        if not successes:
            return results[0]

        best = max(successes, key=lambda r: len(r.output_text))
        logger.info(
            "[LongestOutputSelector] Selected attempt %d "
            "(output_len=%d)",
            best.attempt_index,
            len(best.output_text),
        )
        return best


class ShortestOutputSelector:
    """Return the successful result with the shortest output text.

    Useful when brevity indicates precision (e.g., minimal diffs).
    """

    @staticmethod
    def select(results: list[RolloutResult]) -> RolloutResult:
        if not results:
            raise ValueError("No rollout results to select from")

        successes = [r for r in results if r.is_success]
        if not successes:
            return results[0]

        best = min(successes, key=lambda r: len(r.output_text))
        logger.info(
            "[ShortestOutputSelector] Selected attempt %d "
            "(output_len=%d)",
            best.attempt_index,
            len(best.output_text),
        )
        return best


_SELECTORS: dict[str, type[ResultSelector]] = {
    "first_successful": FirstSuccessfulSelector,
    "longest_output": LongestOutputSelector,
    "shortest_output": ShortestOutputSelector,
}


def get_selector(kind: str) -> ResultSelector:
    """Factory: instantiate a selector by kind name."""
    cls = _SELECTORS.get(kind)
    if cls is None:
        raise ValueError(
            f"Unknown selector kind: {kind!r}. "
            f"Available: {list(_SELECTORS.keys())}"
        )
    return cls()
