# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Render canonical OTLP trajectories into text for Metis reflection."""

from __future__ import annotations

import json
from typing import Any, List, Mapping, Optional

from openjiuwen.agent_evolving.trajectory.model import Trajectory
from openjiuwen.agent_evolving.trajectory.spans import (
    iter_spans,
    read_llm_exchange,
    read_span_error,
    read_tool_call,
    span_sort_key,
)
from openjiuwen.agent_evolving.trajectory.team import span_category

_STEP_TEXT_MAX = 1500
_TRAJECTORY_TEXT_MAX = 20000


def _clip(text: str, limit: int = _STEP_TEXT_MAX) -> str:
    text = text.strip()
    return text if len(text) <= limit else text[: limit - 3] + "..."


def _as_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return str(value)


def _tool_call_name(call: Any) -> str:
    if not isinstance(call, Mapping):
        return ""
    function = call.get("function")
    if isinstance(function, Mapping):
        return str(call.get("name") or function.get("name") or "")
    return str(call.get("name") or "")


def _llm_span_text(span: Mapping[str, Any]) -> str:
    _, completions = read_llm_exchange(span)
    contents: List[str] = []
    tool_calls: List[str] = []
    for message in completions:
        content = _as_text(message.get("content"))
        if content:
            contents.append(_clip(content))
        for call in message.get("tool_calls") or []:
            name = _tool_call_name(call)
            if name:
                tool_calls.append(name)
    parts = list(contents)
    if tool_calls:
        parts.append("calls: " + ", ".join(tool_calls))
    return "\n".join(parts) if parts else "(no content)"


def _tool_span_text(span: Mapping[str, Any]) -> str:
    call = read_tool_call(span)
    lines = [f"tool: {call.get('name') or span.get('name') or ''}"]
    args = _clip(_as_text(call.get("input")), 400)
    result = _clip(_as_text(call.get("output")))
    if args:
        lines.append(f"args: {args}")
    if result:
        lines.append(f"result: {result}")
    return "\n".join(lines)


def render_trajectory_text(trajectory: Optional[Trajectory]) -> str:
    """Return a compact, numbered transcript from canonical span accessors."""
    if trajectory is None:
        return ""
    blocks: List[str] = []
    for span in sorted(iter_spans(trajectory), key=span_sort_key):
        kind = span_category(span)
        if kind == "llm":
            body = _llm_span_text(span)
            label = "assistant"
        elif kind == "tool":
            body = _tool_span_text(span)
            label = "tool"
        else:
            continue
        error = read_span_error(span)
        if error:
            body += f"\nerror: {_clip(_as_text(error), 300)}"
        blocks.append(f"[step {len(blocks) + 1} | {label}]\n{body}")
    text = "\n\n".join(blocks)
    return text if len(text) <= _TRAJECTORY_TEXT_MAX else text[: _TRAJECTORY_TEXT_MAX - 3] + "..."


__all__ = ["render_trajectory_text"]
