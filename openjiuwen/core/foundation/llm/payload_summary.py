# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Bounded log summaries for LLM request payloads.

Error paths used to serialize full ``messages``/``tools`` into structured
logs — user queries, assistant replies and tool results all landing on disk,
and full-payload serialization being able to blow up at the memory edge.
These helpers replace the full payload with counts plus a short, masked
preview of the current-turn user message: enough to tie an error back to the
request that caused it, without persisting conversation content.

Previews are masked HERE (not delegated to the host log pipeline): truncation
is not desensitization — a phone/ID/bank-card number fits in 50 chars — and
agent-core also runs under hosts without jiuwenswarm's LogRecord-factory
masking. The masking itself is shared: ``common/security/pii_mask.mask_pii_text``
is the canonical PII pattern set for both agent-core and the jiuwenswarm
host pipeline (which applies it again idempotently on top).
"""

from __future__ import annotations

from typing import Any

from openjiuwen.core.common.security.pii_mask import pii_preview

# 预览长度
_PREVIEW_CHARS = 5


def _content_text(content: Any) -> str:
    """Best-effort text extraction from a message ``content`` field."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict) and isinstance(item.get("text"), str):
                parts.append(item["text"])
        return " ".join(parts)
    return ""


def _message_role_and_text(message: Any) -> tuple[str, str]:
    """Extract (role, text) from dict or BaseMessage-style objects.

    openai_account 等 client 传入的是 BaseMessage
    对象而非 dict，只认 dict 会把 messages_chars 统计成 0。
    """
    if isinstance(message, dict):
        return str(message.get("role") or ""), _content_text(message.get("content"))
    role = getattr(message, "role", "")
    if isinstance(role, str):
        role_value = role
    else:
        role_value = getattr(role, "value", str(role or ""))
    return str(role_value or ""), _content_text(getattr(message, "content", None))


def summarize_messages_for_log(messages: Any) -> dict[str, Any]:
    """Count/char summary of a messages list plus a short last-user preview.

    The preview targets the LAST user message: in a multi-turn conversation
    the first user message is the oldest turn, while an error must be tied
    to the current one. The preview is pattern-masked before truncation.
    """
    if not isinstance(messages, list):
        return {"messages_count": 0, "messages_chars": 0, "last_user_preview": ""}
    total_chars = 0
    last_user_preview = ""
    for message in messages:
        role, text = _message_role_and_text(message)
        if not role and not text:
            continue
        total_chars += len(text)
        if role == "user" and text:
            last_user_preview = text
    return {
        "messages_count": len(messages),
        "messages_chars": total_chars,
        "last_user_preview": pii_preview(last_user_preview, expose=_PREVIEW_CHARS),
    }


def summarize_llm_payload(params: dict[str, Any]) -> dict[str, Any]:
    """messages/tools summary fields to spread into a structured log call."""
    summary = summarize_messages_for_log(params.get("messages"))
    tools = params.get("tools")
    summary["tools_count"] = len(tools) if isinstance(tools, list) else 0
    return summary


def summarize_content_for_log(content: Any) -> dict[str, Any]:
    """Char count + short masked preview of a raw content string (e.g. reply)."""
    text = content if isinstance(content, str) else ("" if content is None else str(content))
    return {"content_chars": len(text), "content_preview": pii_preview(text, expose=_PREVIEW_CHARS)}


def safe_len(value: Any) -> int:
    """Log 字段的类型安全长度。

    ``len(parser_content or "")`` 对无 __len__ 的解析结果（如
    MarkdownContent dataclass / pydantic 模型）抛 TypeError——若恰好落在
    try/except 内会污染解析路径（结果被置 None）。本函数：sized 用 len；
    非 sized 取常见文本属性（raw_content/content/text）长度；否则 0。
    """
    if value is None:
        return 0
    try:
        return len(value)
    except TypeError:
        pass
    for attr in ("raw_content", "content", "text"):
        attr_val = getattr(value, attr, None)
        if isinstance(attr_val, str):
            return len(attr_val)
    return 0


__all__ = ["safe_len", "summarize_content_for_log", "summarize_llm_payload", "summarize_messages_for_log"]
