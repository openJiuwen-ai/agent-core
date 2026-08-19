# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Success detection for the TTSE fail path (blame gating).

The reference TTSE pipeline runs on a benchmark with an external grader that
hands back a task ``score``. jiuwen has no such grader by default, so success
detection is made pluggable: a :class:`SuccessDetector` decides whether a task
succeeded (``success`` / ``partial`` / ``fail``) so :class:`TTSERail` can run
the blame -> retire -> synthesize pass only on real failures.

The default :class:`TrajectoryErrorSuccessDetector` honors an explicit score
when one is provided (snapshot ``ttse_score`` or a ``ctx`` attribute) and
otherwise treats a recorded error on any trajectory step as failure. With
neither signal the task is treated as successful, so blame never fires on a
clean run. Wire a real grader/score source for production use.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, List, Optional

from openjiuwen.agent_evolving.signal.from_conv import (
    ConversationSignalDetector,
    detect_tool_error_signals,
)
from openjiuwen.core.common.logging import logger

from .config import TTSEConfig


@dataclass(frozen=True)
class SuccessOutcome:
    """The outcome of a task: ``success`` / ``partial`` / ``fail`` + a score."""

    outcome: str
    score: float
    reason: str = ""


class SuccessDetector(ABC):
    """Decide task success to gate the TTSE blame/synthesize pass."""

    @abstractmethod
    async def detect(
        self,
        trajectory: Any,
        messages: Any,
        *,
        ctx: Any = None,
        snapshot: Optional[dict] = None,
    ) -> SuccessOutcome:
        raise NotImplementedError


def _explicit_score(ctx: Any, snapshot: Optional[dict]) -> Optional[float]:
    """Look for an externally-provided score (grader/harness)."""
    value: Any = None
    if snapshot:
        value = snapshot.get("ttse_score")
    if value is None and ctx is not None:
        value = getattr(ctx, "ttse_score", None)
        if value is None:
            inputs = getattr(ctx, "inputs", None)
            value = getattr(inputs, "ttse_score", None) if inputs is not None else None
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _trajectory_has_error(trajectory: Any) -> bool:
    """True if any recorded trajectory step carries an error."""
    if trajectory is None:
        return False
    steps = getattr(trajectory, "steps", None) or []
    for step in steps:
        if getattr(step, "error", None):
            return True
        detail = getattr(step, "detail", None)
        if detail is not None and getattr(detail, "error", None):
            return True
    return False


class TrajectoryErrorSuccessDetector(SuccessDetector):
    """Default detector: explicit-score-if-present, else trajectory-error scan.

    * An explicit score (>= ``success_threshold`` -> success, > 0 -> partial,
      else fail) wins when provided.
    * Otherwise a recorded error on any trajectory step -> fail.
    * Otherwise success.
    """

    def __init__(self, success_threshold: float = TTSEConfig().success_threshold) -> None:
        self.success_threshold = success_threshold

    async def detect(
        self,
        trajectory: Any,
        messages: Any,
        *,
        ctx: Any = None,
        snapshot: Optional[dict] = None,
    ) -> SuccessOutcome:
        score = _explicit_score(ctx, snapshot)
        if score is not None:
            if score >= self.success_threshold:
                return SuccessOutcome("success", score, "explicit-score")
            if score > 0:
                return SuccessOutcome("partial", score, "explicit-score")
            return SuccessOutcome("fail", score, "explicit-score")
        if _trajectory_has_error(trajectory):
            return SuccessOutcome("fail", 0.0, "trajectory-error")
        return SuccessOutcome("success", 1.0, "no-error-default")


class SignalBasedSuccessDetector(SuccessDetector):
    """Outcome detector backed by jiuwen's tool-error signal rules.

    When no external grader score is wired, this reads the task's REAL execution
    signals via :func:`detect_tool_error_signals` (deterministic regex rules
    over tool output / trajectory — no extra LLM cost):

    * an ``execution_failure`` signal (some tool output matched the failure
      keywords) -> ``fail`` (drives blame/retire/synthesize);
    * otherwise -> ``success`` (no negative evidence).

    An explicit score (snapshot ``ttse_score`` / ``ctx.ttse_score``) still wins,
    so wiring a real grader remains the most accurate source. This detector is
    richer than :class:`TrajectoryErrorSuccessDetector`: it inspects tool OUTPUT
    text (catching a ``Error: file not found`` returned in a result) rather than
    only raised step errors.
    """

    def __init__(
        self,
        success_threshold: float = TTSEConfig().success_threshold,
        *,
        signal_detector: Optional[ConversationSignalDetector] = None,
    ) -> None:
        self.success_threshold = success_threshold
        self._detector = signal_detector

    async def detect(
        self,
        trajectory: Any,
        messages: Any,
        *,
        ctx: Any = None,
        snapshot: Optional[dict] = None,
    ) -> SuccessOutcome:
        score = _explicit_score(ctx, snapshot)
        if score is not None:
            if score >= self.success_threshold:
                return SuccessOutcome("success", score, "explicit-score")
            if score > 0:
                return SuccessOutcome("partial", score, "explicit-score")
            return SuccessOutcome("fail", score, "explicit-score")
        types = self._signal_types(trajectory, messages)
        if "execution_failure" in types:
            return SuccessOutcome("fail", 0.0, "signal:execution_failure")
        return SuccessOutcome("success", 1.0, "no-failure-default")

    def _signal_types(self, trajectory: Any, messages: Any) -> set:
        """Run the deterministic tool-error rules; never raise into the caller."""
        try:
            if self._detector is not None:
                signals: List = self._detector.detect_trajectory_signals(
                    trajectory,
                    messages=messages,
                    signal_types={"execution_failure", "script_artifact"},
                )
            else:
                if messages is None and trajectory is not None:
                    messages = ConversationSignalDetector.convert_trajectory_to_messages(trajectory)
                signals = detect_tool_error_signals(messages or [])
        except Exception as exc:  # noqa: BLE001 - never raise into the caller
            logger.warning("[TTSERail] signal detection failed: %s", exc)
            return set()
        types = {getattr(s, "signal_type", "") for s in signals or []}
        types.discard("")
        if types:
            logger.info("[TTSERail] signals detected: %s", sorted(types))
        else:
            logger.info("[TTSERail] no signals detected")
        return types


__all__ = [
    "SuccessDetector",
    "SuccessOutcome",
    "TrajectoryErrorSuccessDetector",
    "SignalBasedSuccessDetector",
]
