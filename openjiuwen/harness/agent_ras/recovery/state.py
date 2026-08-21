# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Session-local deferred recovery and stream-buffer state.

``PendingRecovery`` carries recovery context derived from an anomaly's evidence.
``SuppressFlushState`` buffers suppressed stream text until automatic recovery
resolves (flush on normal/fail-open, clear on abnormal). Held by
``RecoveryExecutor.suppress_state``.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from openjiuwen.harness.agent_ras.models import Anomaly

RecoveryResolved = Literal["normal", "abnormal"]


@dataclass
class PendingRecovery:
    """Deferred recovery payload for a thinking-loop (or similar) anomaly."""

    source: str
    recovery_profile: str = ""
    chunk_type: str = "llm_output"
    mode: str = ""
    count: int = 0
    start_pos: int = 0
    scanned_text: str = ""
    thinking_excerpt: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_anomaly(cls, anomaly: Anomaly) -> PendingRecovery:
        """Build pending state from ``anomaly.evidence`` (profile / channel / excerpt)."""
        evidence = dict(anomaly.evidence or {})
        channel = str(evidence.pop("channel", "") or "")
        mode = str(evidence.get("mode") or "")
        profile = str(evidence.get("recovery_profile") or "")
        if not profile:
            if mode == "plan_execution_loop_lock" or channel == "plan_execution":
                profile = "thinking_loop_plan_exec"
            elif channel == "text_repetition":
                profile = "thinking_loop_text_rep"
        if mode == "plan_execution_loop_lock" or channel == "plan_execution":
            source = "plan_execution_loop_lock"
        elif channel == "text_repetition":
            source = "text_repetition"
        else:
            source = mode or channel or anomaly.kind.value
        return cls(
            source=source,
            recovery_profile=profile,
            chunk_type=str(evidence.get("chunk_type") or "llm_output"),
            mode=mode,
            count=int(evidence.get("count") or 0),
            start_pos=int(evidence.get("start_pos") or 0),
            scanned_text=str(evidence.get("scanned_text") or ""),
            thinking_excerpt=str(evidence.get("thinking_excerpt") or ""),
            extra=evidence,
        )

    @property
    def is_plan_execution(self) -> bool:
        return (
            self.source == "plan_execution_loop_lock"
            or self.recovery_profile == "thinking_loop_plan_exec"
            or self.mode == "plan_execution_loop_lock"
        )

    @property
    def is_text_repetition(self) -> bool:
        return (
            self.source == "text_repetition"
            or self.recovery_profile == "thinking_loop_text_rep"
        )


@dataclass
class SuppressFlushState:
    """Stream suppress buffer plus optional pending recovery for one session.

    Lifecycle fields:

    - ``review_awaiting``: L3 recovery background task is in flight
    - ``resolved``: ``\"normal\"`` / ``\"abnormal\"`` once recovery decided
    """

    pending: PendingRecovery | None = None
    cleared_normal: bool = False
    suppressed: dict[str, list[str]] = field(default_factory=dict)
    review_awaiting: bool = False
    resolved: RecoveryResolved | None = None

    def record_pending(self, pending: PendingRecovery) -> None:
        """Arm deferred recovery; subsequent matching chunks are suppressed."""
        self.pending = pending
        self.cleared_normal = False
        self.review_awaiting = False
        self.resolved = None

    def should_suppress(self, chunk_type: str) -> bool:
        """True while pending recovery is active for this chunk type."""
        return (
            self.pending is not None
            and not self.cleared_normal
            and chunk_type == self.pending.chunk_type
        )

    def record_suppressed(self, chunk_type: str, text: str) -> None:
        """Append suppressed stream text for a later flush."""
        if text:
            self.suppressed.setdefault(chunk_type, []).append(text)

    def flush_suppressed(self, chunk_type: str) -> str:
        """Join and clear buffered text for ``chunk_type`` (normal path)."""
        return "".join(self.suppressed.pop(chunk_type, []))

    def clear(self) -> None:
        """Drop pending and all buffers (abnormal / reset)."""
        self.pending = None
        self.cleared_normal = False
        self.suppressed.clear()
        self.review_awaiting = False
        self.resolved = None

    def mark_resolved_normal(self) -> None:
        """Normal / fail-open: stop suppressing but keep buffers for flush."""
        self.cleared_normal = True
        self.pending = None
        self.review_awaiting = False
        self.resolved = "normal"

    def mark_resolved_abnormal(self) -> None:
        """Abnormal: decision recorded; caller clears buffers."""
        self.review_awaiting = False
        self.resolved = "abnormal"
