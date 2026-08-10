# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Structured tool-call chain builder for skill experience optimization."""

from __future__ import annotations

import json
import re
from typing import List

_TOOL_CHAIN_FAILURE_RE = re.compile(
    r"error|exception|traceback|failed|failure|timeout|timed out"
    r"|errno|connectionerror|oserror|valueerror|typeerror"
    r"|错误|异常|失败|超时",
    re.IGNORECASE,
)
_TOOL_CHAIN_CORRECTION_RE = re.compile(
    r"不对|错了|应该|你搞错|纠正|我的意思是|that's wrong|should be|actually",
    re.IGNORECASE,
)
_TOOL_CHAIN_ARGS_MAX_CHARS = 120
_TOOL_CHAIN_RESULT_MAX_CHARS = 100


def _extract_message_text(message: dict) -> str:
    content = message.get("content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict):
                parts.append(str(block.get("text", "")))
            elif isinstance(block, str):
                parts.append(block)
        return "\n".join(parts)
    return str(content)


def _summarize_tool_result(content: str, language: str = "cn") -> tuple[str, str]:
    """Return (status_label, summary) for a tool result line."""
    text = content.strip()
    if not text:
        empty = "(空)" if language == "cn" else "(empty)"
        status = "空" if language == "cn" else "EMPTY"
        return status, empty
    if _TOOL_CHAIN_FAILURE_RE.search(text):
        status = "FAIL" if language == "en" else "失败"
    else:
        status = "OK"
    one_line = " ".join(text.split())
    if len(one_line) > _TOOL_CHAIN_RESULT_MAX_CHARS:
        one_line = one_line[:_TOOL_CHAIN_RESULT_MAX_CHARS] + "..."
    return status, one_line


def build_tool_call_chain(
    messages: List[dict],
    language: str = "cn",
    max_events: int = 40,
) -> str:
    """Build a structured tool-call chain from conversation messages."""
    if not messages:
        return "(无执行轨迹)" if language == "cn" else "(No execution trace)"

    lines: list[str] = []
    turn = 0
    for message in messages:
        role = message.get("role", "")
        if role == "assistant" and message.get("tool_calls"):
            for tool_call in message.get("tool_calls", []):
                if not isinstance(tool_call, dict):
                    continue
                turn += 1
                if turn > max_events:
                    break
                name = tool_call.get("name", "unknown")
                args = tool_call.get("arguments", "")
                if isinstance(args, dict):
                    args_str = json.dumps(args, ensure_ascii=False)
                else:
                    args_str = str(args)
                if len(args_str) > _TOOL_CHAIN_ARGS_MAX_CHARS:
                    args_str = args_str[:_TOOL_CHAIN_ARGS_MAX_CHARS] + "..."
                lines.append(f"[Turn {turn}] assistant → {name}({args_str})")
        elif role in ("tool", "function"):
            turn += 1
            if turn > max_events:
                break
            tool_name = message.get("name") or message.get("tool_name") or "tool"
            status, summary = _summarize_tool_result(
                _extract_message_text(message),
                language=language,
            )
            lines.append(f"[Turn {turn}] {tool_name} → {status}: {summary}")
        elif role == "user":
            text = _extract_message_text(message).strip()
            if text and _TOOL_CHAIN_CORRECTION_RE.search(text):
                turn += 1
                if turn > max_events:
                    break
                preview = text[:150] + ("..." if len(text) > 150 else "")
                tag = "用户纠正" if language == "cn" else "user_correction"
                lines.append(f"[Turn {turn}] user ({tag}): {preview}")
        if turn >= max_events:
            break

    if not lines:
        return (
            "(无工具调用轨迹；参见对话历史)"
            if language == "cn"
            else "(No tool calls; see conversation history)"
        )
    return "\n".join(lines)


__all__ = ["build_tool_call_chain"]
