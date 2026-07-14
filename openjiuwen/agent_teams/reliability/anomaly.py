# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Reliability anomaly model.

An ``Anomaly`` is what a detector emits when a threshold trips. It is a
Pydantic model so it can be carried across process boundaries inside an
``AnomalyDetectedEvent`` (see ``schema/events.py``) and rendered into a
leader-facing alert.
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class Severity(str, Enum):
    """Anomaly severity, ordered low to high.

    The remediation policy maps each severity onto one or more actions.
    """

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"

    @property
    def rank(self) -> int:
        """Return a comparable rank (LOW=0 .. CRITICAL=3)."""
        return _SEVERITY_ORDER.index(self)


_SEVERITY_ORDER: tuple[Severity, ...] = (
    Severity.LOW,
    Severity.MEDIUM,
    Severity.HIGH,
    Severity.CRITICAL,
)


class AnomalyKind(str, Enum):
    """The kind of unhealthy behavior a detector identified."""

    TOOL_ERROR_RATE = "tool_error_rate"
    REPEAT_TOOL_CALL = "repeat_tool_call"
    TOOL_CALL_LOOP = "tool_call_loop"
    MODEL_ERROR = "model_error"
    OUTPUT_TOO_LONG = "output_too_long"
    THINKING_TOO_LONG = "thinking_too_long"
    FREQUENT_COMPACTION = "frequent_compaction"
    PING_PONG = "ping_pong"


class Anomaly(BaseModel):
    """A detected unhealthy condition awaiting remediation.

    Attributes:
        detector: Stable identifier of the detector that produced this.
        kind: The category of anomaly.
        severity: How urgent the condition is.
        member_name: The member exhibiting the condition.
        summary: One-line human- and LLM-readable description.
        evidence: Structured snapshot backing the judgement (counts, hashes,
            drop magnitude, conversation pair, ...).
        peer_member: The other member, for team-level anomalies (pingpong).
    """

    detector: str = Field(..., description="Detector identifier")
    kind: AnomalyKind = Field(..., description="Anomaly category")
    severity: Severity = Field(..., description="Severity level")
    member_name: str = Field(..., description="Affected member")
    summary: str = Field(..., description="One-line description for human/LLM")
    evidence: dict[str, Any] = Field(default_factory=dict, description="Supporting evidence snapshot")
    peer_member: str | None = Field(default=None, description="Peer member for team-level anomalies")
