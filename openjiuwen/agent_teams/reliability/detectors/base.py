# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Detector protocol and the shared error-burst detector.

A ``Detector`` consumes the unified ``Signal`` stream and emits an ``Anomaly``
when a threshold trips — pure logic, no I/O, trivially unit-testable.
``ErrorBurstDetector`` is the shared implementation for tool-call and
model-call error detection (sliding-window rate + consecutive-failure streak).
"""

from __future__ import annotations

import time
from typing import Callable, Protocol

from openjiuwen.agent_teams.reliability.anomaly import Anomaly, AnomalyKind, Severity
from openjiuwen.agent_teams.reliability.signals import Signal, SignalKind
from openjiuwen.agent_teams.reliability.window import SlidingWindowCounter


class Detector(Protocol):
    """Consumes signals and emits anomalies when a threshold trips."""

    @property
    def name(self) -> str:
        """Return the detector's stable identifier."""
        ...

    def observe(self, signal: Signal) -> Anomaly | None:
        """Consume one signal; return an Anomaly when a threshold trips."""
        ...

    def reset(self) -> None:
        """Reset per-round detection state."""
        ...


class ErrorBurstDetector:
    """Detect error bursts: sliding-window rate plus a consecutive-failure run.

    Shared by tool-call and model-call error detection. Counts errors of
    ``error_kind`` in a trailing window and tracks the consecutive-failure
    streak; a ``reset_kind`` (success) signal clears the streak. The emission
    is edge-triggered: an anomaly fires only when the severity rises above the
    last reported level, so a sustained burst does not spam identical alerts.
    """

    def __init__(
        self,
        *,
        name: str,
        error_kind: SignalKind,
        reset_kind: SignalKind,
        anomaly_kind: AnomalyKind,
        window_seconds: float,
        rate_threshold: int,
        consecutive_threshold: int,
        now: Callable[[], float] = time.time,
    ) -> None:
        self._name = name
        self._error_kind = error_kind
        self._reset_kind = reset_kind
        self._anomaly_kind = anomaly_kind
        self._window_seconds = window_seconds
        self._rate_threshold = rate_threshold
        self._consecutive_threshold = consecutive_threshold
        self._now = now
        self._window = SlidingWindowCounter(window_seconds)
        self._consecutive = 0
        self._fired_severity: Severity | None = None

    @property
    def name(self) -> str:
        """Return the detector's stable identifier."""
        return self._name

    def observe(self, signal: Signal) -> Anomaly | None:
        """Count errors, reset on success, emit on severity rise."""
        if signal.kind == self._reset_kind:
            self._consecutive = 0
            self._fired_severity = None
            return None
        if signal.kind != self._error_kind:
            return None
        now = self._now()
        self._consecutive += 1
        window_count = self._window.add(now)
        severity = self._classify(window_count)
        if severity is None:
            return None
        if self._fired_severity is not None and severity.rank <= self._fired_severity.rank:
            return None
        self._fired_severity = severity
        return Anomaly(
            detector=self._name,
            kind=self._anomaly_kind,
            severity=severity,
            member_name=signal.member_name,
            summary=f"{self._consecutive} consecutive failures ({window_count} within {self._window_seconds:.0f}s)",
            evidence={
                "consecutive": self._consecutive,
                "window_count": window_count,
                "window_seconds": self._window_seconds,
                "last_error": signal.error,
            },
        )

    def reset(self) -> None:
        """Reset window, streak and fired level."""
        self._window.reset()
        self._consecutive = 0
        self._fired_severity = None

    def _classify(self, window_count: int) -> Severity | None:
        """Map the current streak / window count onto a severity."""
        if self._consecutive >= self._consecutive_threshold * 2 or window_count >= self._rate_threshold * 2:
            return Severity.HIGH
        if self._consecutive >= self._consecutive_threshold or window_count >= self._rate_threshold:
            return Severity.MEDIUM
        return None
