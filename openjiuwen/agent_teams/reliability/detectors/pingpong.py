# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Team-level ping-pong detector (member to member back-and-forth)."""

from __future__ import annotations

from openjiuwen.agent_teams.reliability.anomaly import Anomaly, AnomalyKind, Severity
from openjiuwen.agent_teams.reliability.signals import Signal, SignalKind


class PingPongDetector:
    """Detect two members volleying messages back and forth without progress.

    Consumes MESSAGE signals (``member_name`` = sender, ``peer_member`` =
    recipient). A strict direction reversal between the same pair extends the
    streak; any third party or a non-reversing message restarts it. One volley
    is a single message, so ``min_volleys`` of 6 means roughly three
    round-trips. This is the only team-level detector — it is fed from
    coordination on the leader process, not from a per-member rail.
    """

    def __init__(self, *, min_volleys: int = 6) -> None:
        self._min_volleys = min_volleys
        self._last_from: str | None = None
        self._last_to: str | None = None
        self._count = 0
        self._fired_severity: Severity | None = None

    @property
    def name(self) -> str:
        """Return the detector's stable identifier."""
        return "ping_pong"

    def observe(self, signal: Signal) -> Anomaly | None:
        """Track back-and-forth volleys; emit when the streak crosses a tier."""
        if signal.kind != SignalKind.MESSAGE or signal.peer_member is None:
            return None
        sender = signal.member_name
        recipient = signal.peer_member
        is_reversal = sender == self._last_to and recipient == self._last_from
        if is_reversal:
            self._count += 1
        else:
            self._count = 1
            self._fired_severity = None
        self._last_from = sender
        self._last_to = recipient
        severity = self._classify()
        if severity is None:
            return None
        if self._fired_severity is not None and severity.rank <= self._fired_severity.rank:
            return None
        self._fired_severity = severity
        return Anomaly(
            detector="ping_pong",
            kind=AnomalyKind.PING_PONG,
            severity=severity,
            member_name=sender,
            summary=f"{self._count} consecutive volleys between {sender} and {recipient}",
            evidence={"volleys": self._count, "pair": sorted([sender, recipient])},
            peer_member=recipient,
        )

    def reset(self) -> None:
        """Reset the volley tracker."""
        self._last_from = None
        self._last_to = None
        self._count = 0
        self._fired_severity = None

    def _classify(self) -> Severity | None:
        """Map the volley streak onto a severity."""
        if self._count >= self._min_volleys * 2:
            return Severity.HIGH
        if self._count >= self._min_volleys:
            return Severity.MEDIUM
        return None
