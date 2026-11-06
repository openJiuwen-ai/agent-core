# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Structured ``[CompressionApplied]`` event logging shared by context processors.

Emits one single-line INFO log per completed compression carrying the fields
needed to answer "how many compressions ran and what were the water levels"
directly from ``context_engine.log`` (batch feishu-msg-defects-20261106 P5).
The legacy ``[RuleCompression]`` line stays untouched for existing log
consumers; this event is purely additive.
"""

from __future__ import annotations

import json
from typing import Any, Iterable

# Keep aligned with the repository-wide ``len(text) // 3`` fallback used by the
# context processors (see rule_compression ``CHARACTERS_PER_TOKEN``).
CHARACTERS_PER_TOKEN = 3

_PLACEHOLDER = "n/a"
_SESSION_FALLBACK = "unknown_session"


def est_tokens(chars: int) -> int:
    """Estimate a token count from a character length via the shared heuristic."""
    return max(int(chars) // CHARACTERS_PER_TOKEN, 0)


def content_chars(content: Any) -> int:
    """Serialized length in characters of one message content value."""
    if isinstance(content, str):
        return len(content)
    if content is None:
        return 0
    try:
        return len(json.dumps(content, ensure_ascii=False))
    except TypeError:
        return len(str(content))


def messages_total_chars(messages: Iterable[Any]) -> int:
    """Total serialized content length in characters across messages."""
    return sum(content_chars(getattr(message, "content", None)) for message in messages)


def resolve_session_id(context: Any) -> str:
    """Best-effort session id extraction from a context with a stable fallback."""
    method = getattr(context, "session_id", None)
    if not callable(method):
        return _SESSION_FALLBACK
    try:
        return str(method() or _SESSION_FALLBACK)
    except Exception:
        return _SESSION_FALLBACK


def count_messages_tokens_if_available(context: Any, messages: Iterable[Any]) -> int | None:
    """Count tokens via the context tokenizer; ``None`` when unavailable.

    Returning ``None`` (instead of a silent character estimate) lets the event
    log keep the ``est_tokens`` annotation honest.
    """
    factory = getattr(context, "token_counter", None)
    if not callable(factory):
        return None
    try:
        counter = factory()
    except Exception:
        return None
    if not counter:
        return None
    try:
        return int(counter.count_messages(list(messages)))
    except Exception:
        return None


def log_compression_applied(
    logger: Any,
    *,
    session_id: str,
    processor: str,
    strategy: str,
    trigger: str,
    before_chars: int,
    after_chars: int,
    before_tokens: int | None = None,
    after_tokens: int | None = None,
    tool_call_id: str | None = None,
) -> None:
    """Emit one structured ``[CompressionApplied]`` INFO line.

    When real tokenizer counts are supplied for both sides they are logged as
    ``tokens=<before>-><after>``; otherwise the token water levels fall back to
    character-derived estimates logged under ``est_tokens=<before>-><after>``
    so estimated values stay explicitly annotated.
    """
    if before_tokens is not None and after_tokens is not None:
        tokens_part = f"{before_tokens}->{after_tokens}"
        est_part = _PLACEHOLDER
    else:
        tokens_part = _PLACEHOLDER
        est_part = f"{est_tokens(before_chars)}->{est_tokens(after_chars)}"
    logger.info(
        "[CompressionApplied] session_id=%s processor=%s strategy=%s trigger=%s "
        "chars=%s->%s tokens=%s est_tokens=%s tool_call_id=%s",
        session_id,
        processor,
        strategy,
        trigger,
        before_chars,
        after_chars,
        tokens_part,
        est_part,
        str(tool_call_id) if tool_call_id is not None else _PLACEHOLDER,
    )


__all__ = [
    "CHARACTERS_PER_TOKEN",
    "content_chars",
    "count_messages_tokens_if_available",
    "est_tokens",
    "log_compression_applied",
    "messages_total_chars",
    "resolve_session_id",
]
