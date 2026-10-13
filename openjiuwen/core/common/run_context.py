# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Request-local runtime context shared by single-Agent and Team execution."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, TypeAlias


class HeartbeatReason(Enum):
    """Describe why a heartbeat execution started."""

    INTERVAL = "interval"
    MANUAL = "manual"


@dataclass
class RunContext:
    """Carry request-local runtime data across one logical invocation."""

    reason: HeartbeatReason | None = None
    session_id: str | None = None
    context_mode: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


RunContextInput: TypeAlias = RunContext | Mapping[str, Any]


def normalize_run_context(value: RunContextInput | None) -> RunContext | None:
    """Normalize a public mapping input into the shared runtime model.

    Unknown top-level keys are preserved in ``extra`` for compatibility with
    callers that historically supplied an extensible ``run.context`` mapping.

    Args:
        value: Typed context or mapping received at a public execution boundary.

    Returns:
        The original typed context, a normalized copy of the mapping, or None.
    """
    if value is None or isinstance(value, RunContext):
        return value

    context_data = dict(value)
    raw_extra = context_data.pop("extra", {}) or {}
    extra = dict(raw_extra)
    raw_reason = context_data.pop("reason", None)
    reason = HeartbeatReason(raw_reason) if isinstance(raw_reason, str) else raw_reason
    session_id = context_data.pop("session_id", None)
    context_mode = context_data.pop("context_mode", None)
    extra.update(context_data)
    return RunContext(
        reason=reason,
        session_id=session_id,
        context_mode=context_mode,
        extra=extra,
    )


__all__ = [
    "HeartbeatReason",
    "RunContext",
    "RunContextInput",
    "normalize_run_context",
]
