# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Reliability monitoring signals.

A single unified signal stream feeds all detectors. The reliability rail
constructs one ``Signal`` per lifecycle hook and forwards it to every
detector; each detector filters by ``SignalKind`` and consumes only what it
cares about. This keeps the collection layer (rail / coordination) decoupled
from detection logic.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any


class SignalKind(str, Enum):
    """Lifecycle point a signal was captured at."""

    BEFORE_TOOL_CALL = "before_tool_call"
    AFTER_TOOL_CALL = "after_tool_call"
    TOOL_EXCEPTION = "tool_exception"
    MODEL_EXCEPTION = "model_exception"
    AFTER_MODEL_CALL = "after_model_call"
    BEFORE_MODEL_CALL = "before_model_call"
    # Team-level signal sourced from coordination, not from the rail.
    MESSAGE = "message"


@dataclass(slots=True)
class Signal:
    """One observation captured at a member-execution lifecycle point.

    Fields beyond ``kind`` / ``member_name`` are optional and only populated
    for the kinds that carry them. Detectors must tolerate ``None`` for any
    field they do not strictly require.

    Attributes:
        kind: Which lifecycle point produced this signal.
        member_name: The member the signal belongs to.
        tool_name: Tool identifier (tool-call kinds).
        tool_args: Tool arguments (BEFORE_TOOL_CALL); used for repeat detection.
        error: Error message (TOOL_EXCEPTION / MODEL_EXCEPTION).
        text_len: Response text length in characters (AFTER_MODEL_CALL).
        thinking_len: Reasoning/thinking length in characters (AFTER_MODEL_CALL).
        message_count: Context message count (BEFORE_MODEL_CALL); compaction hint.
        peer_member: The conversation peer (MESSAGE), for pingpong tracking.
    """

    kind: SignalKind
    member_name: str
    tool_name: str | None = None
    tool_args: dict[str, Any] | None = None
    error: str | None = None
    text_len: int | None = None
    thinking_len: int | None = None
    message_count: int | None = None
    peer_member: str | None = None
