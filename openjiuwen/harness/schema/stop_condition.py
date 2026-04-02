# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Stop condition definitions for DeepAgent task loop."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import (
    Any,
    Callable,
    Dict,
    Optional,
)


# ================================================================
# Evaluation context (decoupled from AgentCallbackContext)
# ================================================================

@dataclass
class StopEvaluationContext:
    """Runtime context passed to each StopConditionEvaluator.

    Decoupled from AgentCallbackContext so evaluators do not
    depend on the agent callback system.

    Attributes:
        iteration: Number of completed outer-loop rounds.
        token_usage: Cumulative token usage across all rounds.
        elapsed_seconds: Wall-clock seconds since loop start.
        last_result: Result dict from the most recent round.
        extra: Arbitrary extra data for custom evaluators.
    """

    iteration: int = 0
    token_usage: int = 0
    elapsed_seconds: float = 0.0
    last_result: Optional[Dict[str, Any]] = None
    extra: Dict[str, Any] = field(default_factory=dict)


# ================================================================
# Evaluator interface
# ================================================================

class StopConditionEvaluator(ABC):
    """Strategy interface for a single stop condition.

    Implement ``should_stop()`` to return ``True`` when the
    outer task loop should terminate.
    """

    @property
    def name(self) -> str:
        """Evaluator name used as stop_reason."""
        return self.__class__.__name__

    @abstractmethod
    def should_stop(
        self, ctx: StopEvaluationContext,
    ) -> bool:
        """Return True if the loop should stop.

        Args:
            ctx: Current evaluation context.

        Returns:
            True to stop the loop, False to continue.
        """

    def reset(self) -> None:
        """Reset internal state for a new invoke cycle."""

    def get_state(self) -> Optional[Dict[str, Any]]:
        """Export serialisable state snapshot.

        Returns:
            A JSON-safe dict, or None if no state to save.
        """
        return None

    def load_state(self, _data: Dict[str, Any]) -> None:
        """Restore state from a persisted snapshot.

        Args:
            _data: Previously exported state dict.
        """


# ================================================================
# Built-in evaluators
# ================================================================

class MaxRoundsEvaluator(StopConditionEvaluator):
    """Stop after a fixed number of outer-loop rounds.

    Attributes:
        max_rounds: Maximum number of completed rounds.
    """

    def __init__(self, max_rounds: int) -> None:
        self._max_rounds = max_rounds

    def should_stop(self, ctx: StopEvaluationContext) -> bool:
        """Return True when completed rounds >= max_rounds."""
        return ctx.iteration >= self._max_rounds


class TokenBudgetEvaluator(StopConditionEvaluator):
    """Stop when cumulative token usage exceeds a budget.

    Attributes:
        max_tokens: Token budget across all rounds.
    """

    def __init__(self, max_tokens: int) -> None:
        self._max_tokens = max_tokens

    def should_stop(self, ctx: StopEvaluationContext) -> bool:
        """Return True when token usage >= max_tokens."""
        return ctx.token_usage >= self._max_tokens


class TimeoutEvaluator(StopConditionEvaluator):
    """Stop when wall-clock elapsed time exceeds a limit.

    Attributes:
        timeout_seconds: Elapsed-time limit in seconds.
    """

    def __init__(self, timeout_seconds: float) -> None:
        self._timeout_seconds = timeout_seconds

    def should_stop(self, ctx: StopEvaluationContext) -> bool:
        """Return True when elapsed_seconds >= timeout_seconds."""
        return ctx.elapsed_seconds >= self._timeout_seconds


class CompletionPromiseEvaluator(StopConditionEvaluator):
    """Stop when a completion promise has been fulfilled.

    The evaluator does NOT parse LLM output directly.
    ``TaskCompletionRail`` detects the promise tag and calls
    ``notify_fulfilled()`` to set the internal flag.

    Attributes:
        promise: Expected promise string.
    """

    def __init__(self, promise: str) -> None:
        self._promise = promise
        self._fulfilled: bool = False
        self._matched_text: str = ""

    def notify_fulfilled(self, matched_text: str) -> None:
        """Mark the promise as fulfilled.

        Args:
            matched_text: The normalised text matched from output.
        """
        self._fulfilled = True
        self._matched_text = matched_text

    def should_stop(self, ctx: StopEvaluationContext) -> bool:  # noqa: ARG002
        """Return True when the promise flag is set."""
        _ = ctx
        return self._fulfilled

    def reset(self) -> None:
        self._fulfilled = False
        self._matched_text = ""

    def get_state(self) -> Optional[Dict[str, Any]]:
        return {
            "fulfilled": self._fulfilled,
            "matched_text": self._matched_text,
        }

    def load_state(self, data: Dict[str, Any]) -> None:
        self._fulfilled = bool(data.get("fulfilled", False))
        self._matched_text = str(data.get("matched_text", ""))


class CustomPredicateEvaluator(StopConditionEvaluator):
    """Stop based on a user-supplied predicate.

    The predicate receives the full ``StopEvaluationContext``
    so it can inspect iteration count, token usage, and any
    custom data stored in ``ctx.extra``.

    Attributes:
        predicate: Callable that returns True to stop.
    """

    def __init__(
        self,
        predicate: Callable[[StopEvaluationContext], bool],
    ) -> None:
        self._predicate = predicate

    def should_stop(self, ctx: StopEvaluationContext) -> bool:
        """Delegate to user predicate."""
        return self._predicate(ctx)


__all__ = [
    "StopEvaluationContext",
    "StopConditionEvaluator",
    "MaxRoundsEvaluator",
    "TokenBudgetEvaluator",
    "TimeoutEvaluator",
    "CompletionPromiseEvaluator",
    "CustomPredicateEvaluator",
]
