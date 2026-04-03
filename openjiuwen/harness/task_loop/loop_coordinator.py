# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""LoopCoordinator — controls the DeepAgent outer task loop.

Tracks round count, token usage, wall-clock time, and abort
flag.  ``should_continue()`` evaluates a chain of
``StopConditionEvaluator`` objects with OR semantics.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional

from openjiuwen.harness.schema.stop_condition import (
    CompletionPromiseEvaluator,
    StopConditionEvaluator,
    StopEvaluationContext,
)

logger = logging.getLogger(__name__)


class LoopCoordinator:
    """Coordinates the outer task-loop lifecycle.

    Attributes:
        current_iteration: Number of completed rounds
            (read-only).
        is_aborted: Whether abort was requested
            (read-only).
        stop_reason: Name of the evaluator that triggered
            the stop, or None if still running (read-only).
    """

    def __init__(
        self,
        evaluators: Optional[
            List[StopConditionEvaluator]
        ] = None,
    ) -> None:
        self._evaluators: List[StopConditionEvaluator] = (
            evaluators or []
        )
        self._iteration: int = 0
        self._token_usage: int = 0
        self._aborted: bool = False
        self._start_time: float = 0.0
        self._stop_reason: Optional[str] = None
        self._last_result: Optional[Dict[str, Any]] = None

    # -- read-only properties --

    @property
    def current_iteration(self) -> int:
        """Number of completed rounds."""
        return self._iteration

    @property
    def is_aborted(self) -> bool:
        """Whether abort has been requested."""
        return self._aborted

    @property
    def stop_reason(self) -> Optional[str]:
        """Name of the evaluator that stopped the loop."""
        return self._stop_reason

    # -- mutation --

    def reset(self) -> None:
        """Reset for a new invoke cycle."""
        self._iteration = 0
        self._token_usage = 0
        self._aborted = False
        self._start_time = time.monotonic()
        self._stop_reason = None
        self._last_result = None
        for ev in self._evaluators:
            ev.reset()

    def increment_iteration(self) -> None:
        """Record one completed round."""
        self._iteration += 1

    def add_token_usage(self, tokens: int) -> None:
        """Accumulate token consumption.

        Args:
            tokens: Number of tokens used in this round.
        """
        if tokens > 0:
            self._token_usage += tokens

    def set_last_result(
        self, result: Dict[str, Any],
    ) -> None:
        """Store the most recent round result.

        Args:
            result: Result dict from the last round.
        """
        self._last_result = result

    def request_abort(self) -> None:
        """Signal the loop to stop immediately."""
        self._aborted = True

    # -- stop evaluation --

    def should_continue(self) -> bool:
        """Return True if the loop may proceed.

        Evaluates all evaluators with OR semantics — the first
        evaluator that returns True from ``should_stop()``
        terminates the loop and records the stop reason.
        """
        if self._aborted:
            self._stop_reason = "Aborted"
            return False

        ctx = self._build_eval_context()
        for ev in self._evaluators:
            try:
                if ev.should_stop(ctx):
                    self._stop_reason = ev.name
                    logger.info(
                        "Stop condition met: %s",
                        ev.name,
                    )
                    return False
            except Exception:
                logger.warning(
                    "Evaluator %s raised an error",
                    ev.name,
                    exc_info=True,
                )
        return True

    # -- completion promise helper --

    def get_completion_promise_evaluator(
        self,
    ) -> Optional[CompletionPromiseEvaluator]:
        """Return the first CompletionPromiseEvaluator if any."""
        for ev in self._evaluators:
            if isinstance(ev, CompletionPromiseEvaluator):
                return ev
        return None

    # -- state persistence --

    def get_state(self) -> Dict[str, Any]:
        """Export a JSON-safe snapshot for checkpointing.

        Returns:
            Dict with iteration, token_usage, stop_reason,
            and per-evaluator state.
        """
        ev_states: Dict[str, Any] = {}
        for ev in self._evaluators:
            s = ev.get_state()
            if s is not None:
                ev_states[ev.name] = s
        return {
            "iteration": self._iteration,
            "token_usage": self._token_usage,
            "stop_reason": self._stop_reason,
            "evaluator_states": ev_states,
        }

    def load_state(
        self,
        data: Optional[Dict[str, Any]],
    ) -> None:
        """Restore state from a persisted snapshot.

        ``start_time`` is reset to now so that
        ``TimeoutEvaluator`` measures from the resume point.

        Args:
            data: Previously exported snapshot dict.
        """
        if not data:
            return
        self._iteration = int(
            data.get("iteration", 0) or 0
        )
        self._token_usage = int(
            data.get("token_usage", 0) or 0
        )
        self._stop_reason = data.get("stop_reason")
        self._start_time = time.monotonic()
        ev_states: Dict[str, Any] = data.get(
            "evaluator_states", {}
        )
        for ev in self._evaluators:
            if ev.name in ev_states:
                ev.load_state(ev_states[ev.name])

    # -- private helpers --

    def _build_eval_context(self) -> StopEvaluationContext:
        elapsed = time.monotonic() - self._start_time
        return StopEvaluationContext(
            iteration=self._iteration,
            token_usage=self._token_usage,
            elapsed_seconds=elapsed,
            last_result=self._last_result,
        )


__all__ = [
    "LoopCoordinator",
]
