# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Shared context-window token counting used by compressors and PA-11."""

from __future__ import annotations

import json
from typing import Any

from openjiuwen.core.common.logging import logger
from openjiuwen.core.context_engine.content_sanitize import sanitize_content_for_text
from openjiuwen.core.context_engine.context.context_utils import ContextUtils
from openjiuwen.core.foundation.llm import BaseMessage


def estimate_content_tokens(content: Any) -> int:
    """Estimate token count from sanitized content when no counter is available."""
    text = sanitize_content_for_text(content)
    return max(len(text) // 3, 1)


def count_messages_tokens(
    messages: list[BaseMessage],
    token_counter: Any,
    processor_type: str = "",
    *,
    usage_aware: bool = False,
) -> int:
    """Count messages with the ContextEngine tokenizer and its character fallback.

    Usage-aware counting uses the last valid model usage baseline and estimates
    only messages appended after that baseline. Partial-list callers should leave
    ``usage_aware`` false because cumulative usage cannot size an isolated span.
    """
    if not messages:
        return 0
    if usage_aware:
        usage_tokens = count_usage_tokens_with_tail(messages)
        if usage_tokens is not None:
            return usage_tokens
    if token_counter is not None:
        try:
            return token_counter.count_messages(messages)
        except Exception as exc:  # pragma: no cover - defensive fallback
            prefix = f"[{processor_type}] " if processor_type else ""
            logger.warning(f"{prefix}token_counter failed, fallback to char-based estimate: {exc}")
    return sum(estimate_content_tokens(getattr(message, "content", "")) for message in messages)


def count_usage_tokens_with_tail(messages: list[BaseMessage]) -> int | None:
    """Return the last valid model-reported total plus the estimated appended tail."""
    for index in range(len(messages) - 1, -1, -1):
        message = messages[index]
        if not ContextUtils.has_valid_usage_metadata(message):
            continue
        usage_tokens = message.usage_metadata.total_tokens
        tail_tokens = sum(
            len(sanitize_content_for_text(getattr(item, "content", ""))) // 3
            for item in messages[index + 1 :]
        )
        return usage_tokens + tail_tokens
    return None


def count_tools_tokens(tools: list[Any], token_counter: Any, processor_type: str = "") -> int:
    """Count tool definitions with the ContextEngine tokenizer or its estimate."""
    if token_counter is not None:
        try:
            return token_counter.count_tools(tools)
        except Exception as exc:  # pragma: no cover - defensive fallback
            if processor_type:
                logger.warning("[%s] tool token counter failed: %s", processor_type, exc)
            else:
                logger.warning("tool token counter failed: %s", exc)
    return sum(_estimate_tool_tokens(tool) for tool in tools)


def count_context_window_tokens(
    messages: list[BaseMessage],
    tools: list[Any],
    token_counter: Any,
    processor_type: str = "",
) -> int:
    """Use the compressor's usage-baseline, tokenizer, and estimate policy."""
    usage_tokens = count_usage_tokens_with_tail(messages)
    if usage_tokens is not None:
        # The baseline already includes the system prompt and tools from that call.
        return usage_tokens
    return count_messages_tokens(messages, token_counter, processor_type) + count_tools_tokens(
        tools,
        token_counter,
        processor_type,
    )


def _estimate_tool_tokens(tool: Any) -> int:
    if hasattr(tool, "model_dump"):
        text = json.dumps(tool.model_dump(mode="json"), ensure_ascii=False, sort_keys=True)
    else:
        text = str(tool)
    return max(len(text) // 3, 1) if text else 0


__all__ = [
    "count_context_window_tokens",
    "count_messages_tokens",
    "count_tools_tokens",
    "count_usage_tokens_with_tail",
    "estimate_content_tokens",
]
