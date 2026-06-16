# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Frequent context-compaction detector (inferred from message-count drops)."""

from __future__ import annotations

import time
from typing import Callable

from openjiuwen.agent_teams.reliability.anomaly import Anomaly, AnomalyKind, Severity
from openjiuwen.agent_teams.reliability.signals import Signal, SignalKind
from openjiuwen.agent_teams.reliability.window import SlidingWindowCounter


class FrequentCompactionDetector:
    """Infer context compaction from a sharp drop in the message count.

    There is no dedicated compaction hook, so this detector watches the
    context message count reported on consecutive BEFORE_MODEL_CALL signals.
    A drop of at least ``drop_ratio`` is treated as a compaction event; when
    compaction recurs ``frequency_threshold`` times within the window a MEDIUM
    anomaly is raised. Severity is capped at MEDIUM because the inference is
    fuzzy — normal multi-turn trimming can look like compaction — so the
    evidence carries the drop magnitude for the leader to judge, and this
    detector never feeds automated remediation.
    """

    def __init__(
        self,
        *,
        window_seconds: float = 300.0,
        frequency_threshold: int = 3,
        drop_ratio: float = 0.3,
        now: Callable[[], float] = time.time,
    ) -> None:
        self._frequency_threshold = frequency_threshold
        self._drop_ratio = drop_ratio
        self._now = now
        self._window = SlidingWindowCounter(window_seconds)
        self._last_count: int | None = None
        self._fired = False

    @property
    def name(self) -> str:
        """Return the detector's stable identifier."""
        return "frequent_compaction"

    def observe(self, signal: Signal) -> Anomaly | None:
        """Detect message-count drops and flag frequent compaction."""
        if signal.kind != SignalKind.BEFORE_MODEL_CALL or signal.message_count is None:
            return None
        current = signal.message_count
        previous = self._last_count
        self._last_count = current
        if previous is None or previous <= 0:
            return None
        if current >= previous * (1.0 - self._drop_ratio):
            return None
        # A sharp drop in message count: infer a compaction event.
        compactions = self._window.add(self._now())
        if compactions < self._frequency_threshold or self._fired:
            return None
        self._fired = True
        return Anomaly(
            detector="frequent_compaction",
            kind=AnomalyKind.FREQUENT_COMPACTION,
            severity=Severity.MEDIUM,
            member_name=signal.member_name,
            summary=f"{compactions} inferred compactions; last drop {previous}->{current}",
            evidence={
                "compactions": compactions,
                "prev_count": previous,
                "cur_count": current,
                "drop": previous - current,
            },
        )

    def reset(self) -> None:
        """Reset window, baseline and fired flag."""
        self._window.reset()
        self._last_count = None
        self._fired = False
