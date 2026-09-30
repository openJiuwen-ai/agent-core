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
            fn = call.get("function") if isinstance(call.get("function"), dict) else {}
            name = call.get("name") or fn.get("name") or "tool"
            args = call.get("arguments")
            if args is None:
                args = fn.get("arguments")
            if isinstance(args, (dict, list)):
                args = json.dumps(args, ensure_ascii=False)
        else:
            name = getattr(call, "name", None) or "tool"
            args = getattr(call, "arguments", "")
        rendered.append(f"ACTION: {name}({args})")
    return rendered


_WRITE_TOOL_NAMES = frozenset({"write_file", "edit_file"})
_PATH_ARG_KEYS = (
    "output_path",
    "file_path",
    "path",
    "filename",
    "save_path",
    "outfile",
    "output_file",
)


def _as_msg_dict(raw: Any) -> dict:
    if isinstance(raw, dict):
        return raw
    return {
        "role": getattr(raw, "role", ""),
        "content": getattr(raw, "content", ""),
        "tool_calls": getattr(raw, "tool_calls", None),
        "name": getattr(raw, "name", None),
    }


def _tool_call_name(call: Any) -> str:
    if isinstance(call, dict):
        return str(call.get("name") or call.get("function", {}).get("name") or "")
    return str(getattr(call, "name", None) or "")


def _tool_call_arguments(call: Any) -> Any:
    if isinstance(call, dict):
        args = call.get("arguments")
        if args is None and isinstance(call.get("function"), dict):
            args = call["function"].get("arguments")
        return args
    return getattr(call, "arguments", None)


def _coerce_args_dict(args: Any) -> dict:
    if isinstance(args, dict):
        return args
    if isinstance(args, str) and args.strip():
        try:
            parsed = json.loads(args)
        except (TypeError, ValueError):
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _path_from_args(args: dict) -> Optional[str]:
    for key in _PATH_ARG_KEYS:
        value = args.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def count_tool_calls(messages: List[Any]) -> int:
    """Count assistant tool_calls across messages (each call counts once)."""
    total = 0
    for raw in messages or []:
        msg = _as_msg_dict(raw)
        if (msg.get("role") or "") != "assistant":
            continue
        calls = msg.get("tool_calls") or []
        total += len(calls)
    return total


def extract_output_paths(
    messages: List[Any],
    *,
    max_paths: int = 20,
) -> List[str]:
    """Collect write/edit tool path args; de-duplicated, order-preserving."""
    seen: set[str] = set()
    paths: List[str] = []
    for raw in messages or []:
        msg = _as_msg_dict(raw)
        if (msg.get("role") or "") != "assistant":
            continue
        for call in msg.get("tool_calls") or []:
            name = _tool_call_name(call)
            if name not in _WRITE_TOOL_NAMES:
                continue
            path = _path_from_args(_coerce_args_dict(_tool_call_arguments(call)))
            if not path or path in seen:
                continue
            seen.add(path)
            paths.append(path)
            if len(paths) >= max_paths:
                return paths
    return paths


def extract_final_reply(
    messages: List[Any],
    *,
    max_chars: int = 1500,
) -> str:
    """Last assistant message with no tool_calls; truncated to ``max_chars``."""
    for raw in reversed(messages or []):
        msg = _as_msg_dict(raw)
        if (msg.get("role") or "") != "assistant":
            continue
        if msg.get("tool_calls"):
            continue
        content = _content_to_str(msg.get("content")).strip()
        if not content:
            continue
        if max_chars is not None and len(content) > max_chars:
            return content[:max_chars]
        return content
    return ""


def messages_to_trajectory_text(
    messages: List[Any],
    *,
    budget: Optional[int] = None,
) -> str:
    """Flatten jiuwen message dicts into USER/THOUGHT/ACTION/OBSERVATION lines.

    System messages are skipped (they are prompt scaffolding, not behavior).
    When ``budget`` is a positive int and the flatten exceeds it, keep the
    **tail** (ACTION/OBSERVATION) rather than the USER head. ``None`` / ``<= 0``
    keeps the full flatten. Accepts dict messages or message objects with
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
    if budget is not None and budget > 0 and len(text) > budget:
        text = text[-budget:]
    return text


__all__ = [
    "count_tool_calls",
    "extract_final_reply",
    "extract_output_paths",
    "messages_to_trajectory_text",
]
