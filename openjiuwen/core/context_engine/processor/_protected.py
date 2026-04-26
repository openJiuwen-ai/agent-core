# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Helper for processors to skip messages tied to active skill bodies.

Active pins are always protected. Original ``skill_tool`` ToolMessages are
treated as protected only on the degraded fallback path (when the active
session-state copy could not be written). Once they are stubbed, they
behave like normal short tool results.
"""
from __future__ import annotations

from typing import Any, Iterable, List, Optional, Set

from openjiuwen.core.context_engine.context.context_utils import ContextUtils
from openjiuwen.core.foundation.llm import BaseMessage


def is_protected(msg: BaseMessage, *, in_active_window: bool = True) -> bool:
    metadata = getattr(msg, "metadata", None) or {}
    if metadata.get("active_skill_pin"):
        return True
    if metadata.get("skill_body_stub"):
        return False
    if metadata.get("is_skill_body"):
        return in_active_window
    return False


def _msg_key(msg: BaseMessage) -> Any:
    """Identity key used to test 'this message is in the active window'."""
    cmid = getattr(msg, "context_message_id", None)
    if cmid is not None:
        return ("cmid", cmid)
    meta = getattr(msg, "metadata", None) or {}
    cmid = meta.get("context_message_id") if isinstance(meta, dict) else None
    if cmid is not None:
        return ("cmid", cmid)
    return ("id", id(msg))


def resolve_active_window_message_ids(
    context: Any,
    messages: List[BaseMessage],
    *,
    window_size: Optional[int] = None,
    dialogue_round: Optional[int] = None,
) -> Set[Any]:
    """Compute identity keys for messages that would land in context_messages
    if get_context_window were called right now.

    This mirrors ``SessionModelContext._get_window_messages`` so that processors
    can decide ``in_active_window`` consistently with what the model will see.
    Falls back to "all messages are in window" when the calculation cannot be
    performed safely (no context, no buffer, etc.).
    """
    if not messages:
        return set()

    default_round = (
        dialogue_round
        if dialogue_round is not None
        else getattr(context, "_default_dialogue_round", None)
    )
    default_size = (
        window_size
        if window_size is not None
        else getattr(context, "_default_window_size", None)
    )

    candidates: List[BaseMessage] = list(messages)
    if isinstance(default_round, int) and default_round > 0:
        try:
            round_index = ContextUtils.find_last_n_dialogue_round(candidates, default_round)
            candidates = candidates[round_index:]
        except Exception:
            pass

    if isinstance(default_size, int):
        if default_size <= 0:
            return set()
        candidates = candidates[-default_size:]

    return {_msg_key(m) for m in candidates}


def msg_in_window(msg: BaseMessage, in_window_ids: Set[Any]) -> bool:
    if not in_window_ids:
        # Empty set means "no info" -> conservatively treat as in window.
        return True
    return _msg_key(msg) in in_window_ids


__all__ = [
    "is_protected",
    "resolve_active_window_message_ids",
    "msg_in_window",
]
