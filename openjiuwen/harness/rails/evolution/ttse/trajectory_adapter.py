# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Render a jiuwen conversation into the flat trajectory text TTSE induces from.

This is the single adapter point that replaces the original TTSE
``trajectory.py`` (which was OpenClaw-specific). Everything else in TTSE is pure
logic; only "how do I read the agent's transcript" differs between hosts.
"""

from __future__ import annotations

import json
from typing import Any, List, Optional


def _content_to_str(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    try:
        return json.dumps(content, ensure_ascii=False)
    except (TypeError, ValueError):
        return str(content)


def _extract_tool_calls(msg: dict) -> List[str]:
    calls = msg.get("tool_calls") or []
    rendered: List[str] = []
    for call in calls:
        if isinstance(call, dict):
            name = call.get("name") or call.get("function", {}).get("name") or "tool"
            args = call.get("arguments")
            if isinstance(args, (dict, list)):
                args = json.dumps(args, ensure_ascii=False)
        else:
            name = getattr(call, "name", None) or "tool"
            args = getattr(call, "arguments", "")
        rendered.append(f"ACTION: {name}({args})")
    return rendered


def messages_to_trajectory_text(
    messages: List[Any],
    *,
    budget: Optional[int] = 9000,
) -> str:
    """Flatten jiuwen message dicts into USER/THOUGHT/ACTION/OBSERVATION lines.

    System messages are skipped (they are prompt scaffolding, not behavior).
    Output is head-truncated to ``budget`` characters, matching the reference's
    trajectory budgeting. Accepts dict messages or message objects with
    ``role``/``content`` attributes.
    """
    lines: List[str] = []
    for raw in messages or []:
        msg = (
            raw
            if isinstance(raw, dict)
            else {
                "role": getattr(raw, "role", ""),
                "content": getattr(raw, "content", ""),
                "tool_calls": getattr(raw, "tool_calls", None),
                "name": getattr(raw, "name", None),
            }
        )
        role = msg.get("role") or ""
        if role == "system":
            continue
        content = _content_to_str(msg.get("content")).strip()
        if role == "user":
            if content:
                lines.append(f"USER: {content}")
        elif role == "assistant":
            if content:
                lines.append(f"THOUGHT: {content}")
            lines.extend(_extract_tool_calls(msg))
        elif role == "tool":
            name = msg.get("name") or "tool"
            lines.append(f"OBSERVATION [{name}]: {content}")
        elif content:
            lines.append(f"{role.upper()}: {content}")
    text = "\n".join(lines)
    if budget is not None and len(text) > budget:
        text = text[:budget]
    return text


__all__ = ["messages_to_trajectory_text"]
