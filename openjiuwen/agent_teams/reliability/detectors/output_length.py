# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Output / thinking length detector."""

from __future__ import annotations

from openjiuwen.agent_teams.reliability.anomaly import Anomaly, AnomalyKind, Severity
from openjiuwen.agent_teams.reliability.signals import Signal, SignalKind


class OutputLengthDetector:
    """Flag over-long model output text or thinking content.

    A direct-threshold detector (no window): each AFTER_MODEL_CALL signal is
    judged on its own. Severity stays LOW — over-long output is usually a
    quality hint, not a failure — and each kind fires once until ``reset()``
    to avoid per-iteration spam. When both exceed their thresholds in one
    call, the thinking anomaly is reported first and the text one on the next.
    """

    def __init__(
        self,
        *,
        text_threshold: int = 32000,
        thinking_threshold: int = 16000,
    ) -> None:
        self._text_threshold = text_threshold
        self._thinking_threshold = thinking_threshold
        self._fired: set[AnomalyKind] = set()

    @property
    def name(self) -> str:
        """Return the detector's stable identifier."""
        return "output_length"

    def observe(self, signal: Signal) -> Anomaly | None:
        """Emit a LOW anomaly when text or thinking exceeds its threshold."""
        if signal.kind != SignalKind.AFTER_MODEL_CALL:
            return None
        thinking_len = signal.thinking_len or 0
        if thinking_len > self._thinking_threshold and AnomalyKind.THINKING_TOO_LONG not in self._fired:
            self._fired.add(AnomalyKind.THINKING_TOO_LONG)
            return self._make(signal.member_name, AnomalyKind.THINKING_TOO_LONG, thinking_len, self._thinking_threshold)
        text_len = signal.text_len or 0
        if text_len > self._text_threshold and AnomalyKind.OUTPUT_TOO_LONG not in self._fired:
            self._fired.add(AnomalyKind.OUTPUT_TOO_LONG)
            return self._make(signal.member_name, AnomalyKind.OUTPUT_TOO_LONG, text_len, self._text_threshold)
        return None

    def reset(self) -> None:
        """Clear the fired set for a new round."""
        self._fired.clear()

    @staticmethod
    def _make(member: str, kind: AnomalyKind, length: int, threshold: int) -> Anomaly:
        """Build a length anomaly with the measured length as evidence."""
        return Anomaly(
            detector="output_length",
            kind=kind,
            severity=Severity.LOW,
            member_name=member,
            summary=f"{kind.value}: {length} chars exceeds {threshold}",
            evidence={"length": length, "threshold": threshold},
        )
