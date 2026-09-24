# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Compact conversation snippet builder for evolution LLM prompts."""

from __future__ import annotations

from typing import List

from openjiuwen.agent_evolving.trajectory.messages import tool_call_name


def build_conversation_snippet(
    messages: List[dict],
    max_messages: int = 30,
    content_preview_chars: int = 300,
    language: str = "cn",
) -> str:
    """Build compact dialogue snippet for LLM prompt context."""
    if not messages:
        return ""

    def _extract_text(message: dict) -> str:
        content = message.get("content", "")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts = []
            for block in content:
                if isinstance(block, dict):
                    parts.append(block.get("text", ""))
                elif isinstance(block, str):
                    parts.append(block)
            return "\n".join(parts)
        return str(content)

    lines: List[str] = []
    recent = messages[-max_messages:]
    for i, message in enumerate(recent):
        role = message.get("role", "unknown")
        text = _extract_text(message).strip() or ("(无文本)" if language == "cn" else "(No text)")
        budget = content_preview_chars * 2 if i >= len(recent) - 5 else content_preview_chars
        if len(text) > budget:
            orig_len = len(text)
            text = text[:budget] + (
                f"\n... [已截断，原始长度 {orig_len} 字符]"
                if language == "cn"
                else f"\n... [truncated, original {orig_len} chars]"
            )
        tool_calls = message.get("tool_calls")
        if role == "assistant" and tool_calls:
            names = [str(tool_call_name(tool_call) or "") for tool_call in tool_calls]
            prefix = f"[assistant] (tool_calls: {', '.join(names)})\n  "
        else:
            prefix = f"[{role}] "
        lines.append(prefix + text)
    return "\n".join(lines)


__all__ = ["build_conversation_snippet"]
