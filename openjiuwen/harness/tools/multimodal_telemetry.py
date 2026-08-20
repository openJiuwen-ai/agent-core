# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Multimodal token usage extraction + storage."""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

_last_multimodal_usage: dict | None = None


def consume_multimodal_usage() -> dict | None:
    """Read and clear the last multimodal token usage. Called by jiuwenclaw after_tool_call."""
    global _last_multimodal_usage
    usage = _last_multimodal_usage
    _last_multimodal_usage = None
    return usage


def record_multimodal_token_usage(resp: Any, model_name: str, system: str = "openai") -> None:
    """Extract token usage from a multimodal API response and store for jiuwenclaw to pick up.

    Args:
        resp: OpenAI ChatCompletion object (has .usage) or raw dict (has ["usage"]).
        model_name: Model name used for the call.
        system: LLM provider system label (default "openai").
    """
    global _last_multimodal_usage
    try:
        usage = getattr(resp, "usage", None)
        if usage is None and isinstance(resp, dict):
            usage = resp.get("usage")
        if not usage:
            return

        def _get(obj: Any, key: str) -> int:
            v = getattr(obj, key, None)
            if v is None and isinstance(obj, dict):
                v = obj.get(key)
            return int(v or 0)

        input_tokens = _get(usage, "prompt_tokens")
        output_tokens = _get(usage, "completion_tokens")
        if not input_tokens and not output_tokens:
            return

        _last_multimodal_usage = {
            "model": model_name,
            "system": system,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
        }
    except Exception as exc:
        logger.warning("[multimodal_telemetry] record_multimodal_token_usage failed: %s", exc)
