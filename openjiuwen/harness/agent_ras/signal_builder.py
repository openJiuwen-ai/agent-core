# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Shared Signal construction helpers for Agent RAS rails."""
from __future__ import annotations

import json
from typing import Any

from openjiuwen.core.single_agent.interrupt.exception import ToolInterruptException
from openjiuwen.harness.agent_ras.models import Signal, SignalInterruptKind, SignalKind


def tool_args_as_dict(args: Any) -> dict[str, Any] | None:
    if isinstance(args, dict):
        return args
    dump = getattr(args, "model_dump", None)
    if callable(dump):
        try:
            value = dump()
            return value if isinstance(value, dict) else {"value": value}
        except (TypeError, ValueError):
            return {"raw": str(args)}
    if isinstance(args, str):
        try:
            value = json.loads(args)
        except (TypeError, ValueError):
            return {"raw": args}
        return value if isinstance(value, dict) else {"value": value}
    if args is None:
        return None
    return {"raw": str(args)}


def tool_msg_content_from_inputs(inputs: Any) -> str | None:
    tool_msg = getattr(inputs, "tool_msg", None)
    if tool_msg is None:
        return None
    content = getattr(tool_msg, "content", None)
    if isinstance(content, str):
        return content
    if isinstance(tool_msg, str):
        return tool_msg
    return str(tool_msg)


def measure_text(response: Any) -> tuple[int | None, int | None]:
    if response is None:
        return None, None
    content = getattr(response, "content", None)
    text_len = len(content) if isinstance(content, str) else None
    reasoning = (
        getattr(response, "reasoning_content", None)
        or getattr(response, "thinking", None)
    )
    thinking_len = len(reasoning) if isinstance(reasoning, str) else None
    return text_len, thinking_len


def error_text(exc: Exception | None) -> str:
    return str(exc) if exc is not None else "error"


def _resolve_interrupt_kind(exc: Any) -> SignalInterruptKind | None:
    """Classify whether an exception originates from a permission interrupt.

    Walks the ``cause``/``__cause__`` chain looking for
    :class:`ToolInterruptException`, the type raised by both
    ``BaseSecurityRail._raise_tool_interrupt`` and
    ``InterruptBaseRail._raise_interrupt``. Cycle-safe via ``id()``.
    Returns the kind enum to attach to the Signal, or ``None`` for normal
    exceptions.
    """
    seen: set[int] = set()
    cur: Any = exc
    while cur is not None and id(cur) not in seen:
        if isinstance(cur, ToolInterruptException):
            return SignalInterruptKind.PERMISSION_ASK_SIGNAL
        seen.add(id(cur))
        cur = getattr(cur, "cause", None) or getattr(cur, "__cause__", None)
    return None


def build_before_tool_call_signal(member_name: str, inputs: Any) -> Signal:
    return Signal(
        kind=SignalKind.BEFORE_TOOL_CALL,
        member_name=member_name,
        tool_name=getattr(inputs, "tool_name", "") or "",
        tool_args=tool_args_as_dict(getattr(inputs, "tool_args", None)),
    )


def build_after_tool_call_signal(
    member_name: str, inputs: Any, ctx: Any | None = None
) -> Signal:
    """Build AFTER_TOOL_CALL signal.

    ``ctx`` (optional) carries the rail context; its ``exception`` attribute
    is inspected to flag permission-interrupt pseudo-results produced by
    ``@rail``'s finally cleanup after an ask was raised.
    """
    interrupt_kind = (
        _resolve_interrupt_kind(getattr(ctx, "exception", None))
        if ctx is not None
        else None
    )
    return Signal(
        kind=SignalKind.AFTER_TOOL_CALL,
        member_name=member_name,
        tool_name=getattr(inputs, "tool_name", "") or "",
        tool_args=tool_args_as_dict(getattr(inputs, "tool_args", None)),
        tool_result=getattr(inputs, "tool_result", None),
        tool_msg_content=tool_msg_content_from_inputs(inputs),
        interrupt_kind=interrupt_kind,
    )


def build_tool_exception_signal(
    member_name: str, inputs: Any, exc: Exception | None
) -> Signal:
    return Signal(
        kind=SignalKind.TOOL_EXCEPTION,
        member_name=member_name,
        tool_name=getattr(inputs, "tool_name", "") or "",
        error=error_text(exc),
        tool_msg_content=tool_msg_content_from_inputs(inputs),
        interrupt_kind=_resolve_interrupt_kind(exc),
    )


def build_model_exception_signal(member_name: str, exc: Exception | None) -> Signal:
    return Signal(
        kind=SignalKind.MODEL_EXCEPTION,
        member_name=member_name,
        error=error_text(exc),
    )


def build_before_model_call_signal(member_name: str, inputs: Any) -> Signal:
    messages = getattr(inputs, "messages", None)
    count = len(messages) if isinstance(messages, list) else None
    return Signal(
        kind=SignalKind.BEFORE_MODEL_CALL,
        member_name=member_name,
        message_count=count,
    )


def build_after_model_call_signal(member_name: str, inputs: Any) -> Signal:
    text_len, thinking_len = measure_text(getattr(inputs, "response", None))
    return Signal(
        kind=SignalKind.AFTER_MODEL_CALL,
        member_name=member_name,
        text_len=text_len,
        thinking_len=thinking_len,
    )


def build_stream_chunk_signal(member_name: str, inputs: Any) -> Signal:
    return Signal(
        kind=SignalKind.STREAM_CHUNK,
        member_name=member_name,
        chunk_type=getattr(inputs, "chunk_type", None) or "llm_output",
        chunk_text=getattr(inputs, "chunk_text", None) or "",
    )