# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Detector protocol and async-recovery capability.

A ``Detector`` consumes the unified ``Signal`` stream and emits an ``Anomaly``
when a threshold trips — pure logic, no I/O, trivially unit-testable.
``AsyncRecoveryDetector`` is an optional capability for detectors that finish
stream recovery after ``observe`` returns.
"""
from __future__ import annotations

from typing import Any, Awaitable, Callable, Protocol, runtime_checkable

from openjiuwen.harness.agent_ras.models import (
    Anomaly,
    Signal,
)

AsyncRecoveryHandler = Callable[[Any, str], Awaitable[None]]


class Detector(Protocol):
    """Consumes signals and emits anomalies when a threshold trips.

    Every detector must implement:
      - ``name`` property: stable identifier used for logging / evidence.
      - ``observe(signal)``: sync or async; called by ``AgentRASMonitor.detection``.
        Returning ``None`` means "no anomaly".
      - ``reset()``: per-round cleanup; called by ``AgentRASMonitor.reset``.
    """

    @property
    def name(self) -> str:
        """Return the detector's stable identifier."""
        ...

    def observe(
        self, signal: Signal,
    ) -> Anomaly | None | Awaitable[Anomaly | None]:
        """Consume one signal; return an Anomaly when a threshold trips."""
        ...

    def reset(self) -> None:
        """Reset per-round detection state."""
        ...


@runtime_checkable
class AsyncRecoveryDetector(Protocol):
    """Optional Detector capability: finish stream recovery after ``observe`` returns.

    Main implementation: ``LlmThinkingLoopDetector`` (async L3 skill evaluation).
    ``AgentRASMonitor`` wires the completion handler at start and awaits on stop.
    """

    def has_async_recovery_in_flight(self) -> bool:
        """Return True while an async evaluation / recovery task is pending."""
        ...

    async def await_async_recovery(self, timeout: float) -> None:
        """Wait up to ``timeout`` seconds for in-flight async recovery."""
        ...

    def release_async_recovery(self) -> None:
        """Force-release in-flight state (timeout / cancel path)."""
        ...

    def set_async_recovery_handler(
        self,
        handler: AsyncRecoveryHandler | None,
    ) -> None:
        """Install the Monitor callback invoked when async evaluation finishes."""
        ...


def is_async_recovery_detector(detector: Any) -> bool:
    """Return True if ``detector`` structurally matches ``AsyncRecoveryDetector``."""
    return isinstance(detector, AsyncRecoveryDetector)
