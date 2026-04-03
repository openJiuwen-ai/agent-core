# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""HandoffSignal -- handoff directive emitted by agents and consumed by HandoffOrchestrator."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

# Keys written by HandoffTool.invoke() and read by extract_handoff_signal.
HANDOFF_TARGET_KEY = "__handoff_to__"
HANDOFF_MESSAGE_KEY = "__handoff_message__"
HANDOFF_REASON_KEY = "__handoff_reason__"


@dataclass(frozen=True)
class HandoffSignal:
    """Immutable handoff directive produced by :func:`extract_handoff_signal`.

    Attributes:
        target:  ID of the target agent.
        message: Optional context message forwarded to the target agent.
        reason:  Optional human-readable reason for the handoff.
    """
    target: str
    message: Optional[str] = None
    reason: Optional[str] = None


def _find_handoff_payload(result: Any) -> Optional[dict]:
    """Search *result* for a dict that contains HANDOFF_TARGET_KEY."""
    if isinstance(result, dict):
        if HANDOFF_TARGET_KEY in result:
            return result
        for key in ("output", "result", "content"):
            sub = result.get(key)
            if isinstance(sub, dict) and HANDOFF_TARGET_KEY in sub:
                return sub
    return None


def extract_handoff_signal(result: Any) -> Optional[HandoffSignal]:
    """Return :class:`HandoffSignal` if *result* contains a handoff directive, else ``None``.

    Args:
        result: Agent return value to inspect.

    Returns:
        :class:`HandoffSignal` when ``__handoff_to__`` is found in *result*; ``None`` otherwise.
    """
    payload = _find_handoff_payload(result)
    if payload is None:
        return None
    target = payload.get(HANDOFF_TARGET_KEY)
    if not target or not isinstance(target, str):
        return None
    return HandoffSignal(
        target=target,
        message=payload.get(HANDOFF_MESSAGE_KEY) or None,
        reason=payload.get(HANDOFF_REASON_KEY) or None,
    )
