# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Multimodal token usage extraction + storage.

record_multimodal_token_usage now RETURNS the usage dict instead of writing
to a global. Callers (e.g. vision.py) accumulate usages from multiple model
calls (OCR + VQA) and store the aggregated total once via
store_multimodal_usage(), which jiuwenclaw's after_tool_call consumes.
"""
from __future__ import annotations

import logging
import threading
from typing import Any

logger = logging.getLogger(__name__)

_last_multimodal_usage: dict | None = None
_lock = threading.Lock()


def store_multimodal_usage(usage: dict | None) -> None:
    """Store aggregated usage for jiuwenclaw after_tool_call to consume.

    Called ONCE per tool invocation (after accumulating all model calls),
    not per model call. Thread-safe via lock.
    """
    global _last_multimodal_usage
    with _lock:
        _last_multimodal_usage = usage


def consume_multimodal_usage() -> dict | None:
    """Read and clear the stored multimodal token usage. Called by jiuwenclaw after_tool_call."""
    global _last_multimodal_usage
    with _lock:
        usage = _last_multimodal_usage
        _last_multimodal_usage = None
    return usage


def record_multimodal_token_usage(resp: Any, model_name: str, system: str = "openai") -> dict | None:
    """Extract token usage from a multimodal API response and RETURN it.

    Does NOT write to the global — caller is responsible for accumulating
    and calling store_multimodal_usage() once per tool invocation.

    Args:
        resp: OpenAI ChatCompletion object (has .usage) or raw dict (has ["usage"]).
        model_name: Model name used for the call.
        system: LLM provider system label (default "openai").

    Returns:
        Usage dict {"model", "system", "input_tokens", "output_tokens"} or None.
    """
    try:
        usage = getattr(resp, "usage", None)
        if usage is None and isinstance(resp, dict):
            usage = resp.get("usage")
        if not usage:
            return None

        def _get(obj: Any, key: str) -> int:
            v = getattr(obj, key, None)
            if v is None and isinstance(obj, dict):
                v = obj.get(key)
            return int(v or 0)

        input_tokens = _get(usage, "prompt_tokens")
        output_tokens = _get(usage, "completion_tokens")
        if not input_tokens and not output_tokens:
            return None

        return {
            "model": model_name,
            "system": system,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
        }
    except Exception as exc:
        logger.warning("[multimodal_telemetry] record_multimodal_token_usage failed: %s", exc)
        return None


def aggregate_usage(usages: list[dict | None]) -> dict | None:
    """Aggregate multiple usage dicts into one (sum tokens, keep first model).

    None entries are skipped. If all entries are None, returns None.
    """
    valid = [u for u in usages if u]
    if not valid:
        return None
    total_input = sum(u.get("input_tokens", 0) for u in valid)
    total_output = sum(u.get("output_tokens", 0) for u in valid)
    first = valid[0]
    return {
        "model": first.get("model", ""),
        "system": first.get("system", "openai"),
        "input_tokens": total_input,
        "output_tokens": total_output,
    }
