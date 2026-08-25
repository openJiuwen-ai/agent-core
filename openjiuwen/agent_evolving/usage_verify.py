# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""USAGE_VERIFY helpers for evolution-layer LLM calls.

These calls bypass the chat stream (no ``llm_usage`` / ``chat.usage_metadata``),
so their tokens are not accumulated into session ``usageByDay`` / the frontend
usage stats modal. Logs here surface provider ``usage_metadata`` for ops
verification while explicitly marking ``countedInUsageStatsModal=False``.
"""

from __future__ import annotations

from typing import Any, Mapping, Optional

from openjiuwen.core.common.logging import logger


def extract_usage_snapshot(response: Any) -> Optional[dict[str, Any]]:
    """Normalize ``AssistantMessage.usage_metadata`` (or dict) into a plain dict."""
    usage = getattr(response, "usage_metadata", None)
    if usage is None and isinstance(response, Mapping):
        usage = response.get("usage_metadata") or response.get("usage")
    if usage is None:
        return None
    if isinstance(usage, Mapping):
        data = dict(usage)
    else:
        for method_name in ("model_dump", "dict"):
            serializer = getattr(usage, method_name, None)
            if not callable(serializer):
                continue
            try:
                payload = serializer()
            except Exception:  # pylint: disable=broad-exception-caught
                continue
            if isinstance(payload, dict):
                data = payload
                break
        else:
            return None
    input_tokens = int(data.get("input_tokens") or data.get("prompt_tokens") or 0)
    output_tokens = int(data.get("output_tokens") or data.get("completion_tokens") or 0)
    total_tokens = int(data.get("total_tokens") or (input_tokens + output_tokens))
    cache_tokens = int(data.get("cache_tokens") or data.get("cache_read_tokens") or 0)
    if input_tokens <= 0 and output_tokens <= 0 and total_tokens <= 0 and cache_tokens <= 0:
        return None
    snapshot: dict[str, Any] = {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total_tokens,
    }
    if cache_tokens > 0:
        snapshot["cache_tokens"] = cache_tokens
    return snapshot


def log_evolution_llm_usage(
    *,
    path: str,
    model: str | None = None,
    response: Any = None,
    provider_usage: Optional[Mapping[str, Any]] = None,
    extra: Optional[Mapping[str, Any]] = None,
    reason: str = (
        "evolution LLM is a side-path invoke; not written as chat.usage_metadata, "
        "so Relay/session usageByDay / 用量统计弹窗 do not include these tokens"
    ),
) -> Optional[dict[str, Any]]:
    """Log provider usage for an evolution-layer LLM call (not counted in FE stats)."""
    snapshot: Optional[dict[str, Any]]
    if provider_usage is not None:
        snapshot = extract_usage_snapshot({"usage_metadata": dict(provider_usage)})
    else:
        snapshot = extract_usage_snapshot(response)
    payload: dict[str, Any] = {
        "path": path,
        "model": model,
        "provider_usage": snapshot,
        "has_provider_usage": snapshot is not None,
        "countedInChatUsageMetadata": False,
        "countedInSessionUsage": False,
        "countedInUsageByDay": False,
        "countedInUsageStatsModal": False,
        "reason": reason,
    }
    if extra:
        payload.update(dict(extra))
    logger.info(
        "[USAGE_VERIFY][evolution:%s] LLM usage snapshot — NOT counted in frontend usage stats | %s",
        path,
        payload,
    )
    return snapshot
