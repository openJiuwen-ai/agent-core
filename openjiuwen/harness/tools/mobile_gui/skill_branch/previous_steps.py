# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

import json
from typing import Any, List, Optional


def _content_to_text(content: Any) -> str:
    """Extract text from message content; omit image_url blocks."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return str(content)

    parts: List[str] = []
    for block in content:
        if isinstance(block, str):
            parts.append(block)
            continue
        if not isinstance(block, dict):
            continue
        if block.get("type") == "image_url":
            continue
        if block.get("type") == "text":
            text = block.get("text")
            if isinstance(text, str) and text.strip():
                parts.append(text)
    return "\n".join(parts)


def _format_tool_calls(msg: Any) -> str:
    tool_calls = getattr(msg, "tool_calls", None)
    if not tool_calls:
        return ""

    lines: List[str] = []
    for tc in tool_calls:
        name = getattr(tc, "name", None) or ""
        arguments = getattr(tc, "arguments", None) or ""
        if not name and isinstance(tc, dict):
            name = str(tc.get("name", ""))
            arguments = tc.get("arguments", "")
        if not name:
            continue
        if isinstance(arguments, dict):
            arg_text = json.dumps(arguments, ensure_ascii=False)
        else:
            arg_text = str(arguments)
        lines.append(f"Tool call: {name}({arg_text})")
    return "\n".join(lines)


def _format_assistant_turn_lines(msg: Any) -> List[str]:
    lines: List[str] = []
    text = _content_to_text(getattr(msg, "content", "")).strip()
    if text:
        lines.append(text)
    tool_lines = _format_tool_calls(msg)
    if tool_lines:
        lines.append(tool_lines)
    return lines


def _format_tool_line(msg: Any) -> str:
    tool_name = getattr(msg, "name", None) or "tool"
    body = _content_to_text(getattr(msg, "content", "")).strip()
    if not body:
        body = "(empty tool result)"
    return f"Tool result ({tool_name}): {body}"


def format_previous_steps_for_branch(
    messages: List[Any],
    *,
    skip_tool_call_id: Optional[str] = None,
    last_n_turns: int = 10,
) -> str:
    """Build text history from main ReAct context for the skill branch.

    Task text is **not** included here; branch prompts already pass it as
    ``User instruction`` (``pinned_user_goal`` from invoke).

    Includes only the last ``last_n_turns`` assistant steps (assistant text,
    tool calls, and tool results). All user turns are omitted (task query,
    VLM observations, skill images). Live UI is the latest screenshot only.
    """
    if not messages:
        return "(no previous steps)"

    turns: List[List[str]] = []
    current_turn: List[str] = []

    for msg in messages:
        role = getattr(msg, "role", None)
        if role == "assistant":
            if current_turn:
                turns.append(current_turn)
            assistant_lines = _format_assistant_turn_lines(msg)
            current_turn = list(assistant_lines) if assistant_lines else []
        elif role == "tool":
            tc_id = getattr(msg, "tool_call_id", None) or ""
            if skip_tool_call_id and tc_id == skip_tool_call_id:
                continue
            current_turn.append(_format_tool_line(msg))

    if current_turn:
        turns.append(current_turn)

    prefix_lines: List[str] = []
    if last_n_turns > 0 and len(turns) > last_n_turns:
        omitted = len(turns) - last_n_turns
        turns = turns[-last_n_turns:]
        prefix_lines.append(f"... ({omitted} earlier assistant turn(s) omitted)")

    step_lines: List[str] = list(prefix_lines)
    for step_num, turn_lines in enumerate(turns, start=1):
        if not turn_lines:
            continue
        step_lines.append(f"--- Step {step_num} (assistant) ---")
        step_lines.extend(turn_lines)

    if not step_lines:
        return "(no previous steps)"

    return "\n".join(step_lines)
