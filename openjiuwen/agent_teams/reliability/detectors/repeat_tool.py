# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Repeat / loop tool-call detector (stable hash + N=30 window + four tiers)."""

from __future__ import annotations

from collections import deque

from openjiuwen.agent_teams.reliability.anomaly import Anomaly, AnomalyKind, Severity
from openjiuwen.agent_teams.reliability.signals import Signal, SignalKind
from openjiuwen.agent_teams.reliability.window import stable_call_hash, stable_result_hash


class RepeatToolCallDetector:
    """Detect repeated or looping tool calls over a sliding history.

    Records ``(call_key, outcome_key)`` pairs for the last ``history_size``
    completed calls. ``call_key`` is a stable hash of the tool name plus its
    recursively key-sorted args (computed at BEFORE_TOOL_CALL and held until
    the call completes); ``outcome_key`` is a stable hash of the result on
    success (so identical results count as a loop and changing results do not)
    or the error text on failure.

    Four tiers, edge-triggered to avoid spam (only emits when severity rises):
      - generic repeat: same ``call_key`` appears >= ``repeat_warn`` times -> LOW
      - alternation (A-B-A-B with stable result outcomes) >= ``pingpong_warn`` -> MEDIUM.
        Capped at MEDIUM: result hashing cuts false positives, but
        non-deterministic content (timestamps, ids) can still skew it, so the
        leader makes the final call.
      - no-progress: trailing identical ``(call_key, outcome)`` >= ``loop_block`` -> HIGH
      - global circuit: trailing identical >= ``global_stop`` -> CRITICAL
    """

    def __init__(
        self,
        *,
        history_size: int = 30,
        repeat_warn: int = 10,
        pingpong_warn: int = 10,
        loop_block: int = 20,
        global_stop: int = 30,
    ) -> None:
        self._repeat_warn = repeat_warn
        self._pingpong_warn = pingpong_warn
        self._loop_block = loop_block
        self._global_stop = global_stop
        self._history: deque[tuple[str, str]] = deque(maxlen=history_size)
        self._pending_call_key: str | None = None
        self._fired_severity: Severity | None = None

    @property
    def name(self) -> str:
        """Return the detector's stable identifier."""
        return "repeat_tool_call"

    def observe(self, signal: Signal) -> Anomaly | None:
        """Hold the call key at BEFORE, record + classify at completion."""
        if signal.kind == SignalKind.BEFORE_TOOL_CALL:
            self._pending_call_key = stable_call_hash(signal.tool_name or "", signal.tool_args)
            return None
        if signal.kind == SignalKind.AFTER_TOOL_CALL:
            return self._record_and_classify(signal.member_name, stable_result_hash(signal.tool_result))
        if signal.kind == SignalKind.TOOL_EXCEPTION:
            return self._record_and_classify(signal.member_name, signal.error or "error")
        return None

    def reset(self) -> None:
        """Clear history and pending state."""
        self._history.clear()
        self._pending_call_key = None
        self._fired_severity = None

    def _record_and_classify(self, member: str, outcome: str) -> Anomaly | None:
        """Append the completed call and run the tiered classifier."""
        if self._pending_call_key is None:
            return None
        call_key = self._pending_call_key
        self._pending_call_key = None
        self._history.append((call_key, outcome))
        severity, kind, evidence = self._classify(call_key)
        if severity is None:
            return None
        if self._fired_severity is not None and severity.rank <= self._fired_severity.rank:
            return None
        self._fired_severity = severity
        return Anomaly(
            detector="repeat_tool_call",
            kind=kind,
            severity=severity,
            member_name=member,
            summary=f"{kind.value} detected: {evidence}",
            evidence=evidence,
        )

    def _classify(self, call_key: str) -> tuple[Severity | None, AnomalyKind, dict[str, int]]:
        """Run the four-tier check, returning the highest-severity hit."""
        identical = self._trailing_identical()
        if identical >= self._global_stop:
            return Severity.CRITICAL, AnomalyKind.TOOL_CALL_LOOP, {"trailing_identical": identical}
        if identical >= self._loop_block:
            return Severity.HIGH, AnomalyKind.TOOL_CALL_LOOP, {"trailing_identical": identical}
        alternation = self._trailing_alternation()
        if alternation >= self._pingpong_warn:
            return Severity.MEDIUM, AnomalyKind.TOOL_CALL_LOOP, {"trailing_alternation": alternation}
        repeats = sum(1 for ck, _ in self._history if ck == call_key)
        if repeats >= self._repeat_warn:
            return Severity.LOW, AnomalyKind.REPEAT_TOOL_CALL, {"call_repeats": repeats}
        return None, AnomalyKind.REPEAT_TOOL_CALL, {}

    def _trailing_identical(self) -> int:
        """Count how many trailing records exactly equal the most recent one."""
        if not self._history:
            return 0
        last = self._history[-1]
        count = 0
        for record in reversed(self._history):
            if record == last:
                count += 1
            else:
                break
        return count

    def _trailing_alternation(self) -> int:
        """Count the trailing A-B-A-B run length (outcomes must stay stable)."""
        if len(self._history) < 2:
            return 0
        sequence = list(reversed(self._history))
        first = sequence[0]
        second = sequence[1]
        if first == second or first[0] == second[0]:
            return 0
        count = 0
        for index, record in enumerate(sequence):
            expected = first if index % 2 == 0 else second
            if record == expected:
                count += 1
            else:
                break
        return count
