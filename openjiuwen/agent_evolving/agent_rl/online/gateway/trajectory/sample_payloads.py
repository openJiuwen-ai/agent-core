# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Shared helpers for normalized gateway sample payloads."""

from __future__ import annotations

import uuid
from typing import Any, Optional

from ..common import fit_list, utc_now_iso


def coerce_logprobs(values: Any, expected_len: int) -> list[float]:
    """Convert arbitrary logprob values to a fixed-length float list."""
    out: list[float] = []
    if isinstance(values, list):
        for item in values:
            try:
                out.append(float(item))
            except (TypeError, ValueError):
                continue
    return fit_list(out, expected_len)


def build_sample(
    *,
    user_id: str,
    session_id: str,
    turn_num: int,
    mode: str,
    io_mode: str,
    model: Any,
    messages: list[dict[str, Any]],
    tools: Any,
    assistant_message: dict[str, Any],
    usage: dict[str, Any],
    finish_reason: Optional[str],
    prompt_text: str,
    prompt_ids: list[int],
    response_text: str,
    response_ids: list[int],
    response_logprobs: list[float],
    tool_calls: list[dict[str, Any]],
    request_extras: Optional[dict[str, Any]] = None,
    sample_id: Optional[str] = None,
    created_at: Optional[str] = None,
    extra_fields: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Build normalized sample payload used by online and rail paths."""
    input_ids = prompt_ids + response_ids
    sample = {
        "sample_id": sample_id or str(uuid.uuid4()),
        "created_at": created_at or utc_now_iso(),
        "user_id": user_id,
        "session_id": session_id,
        "turn_num": turn_num,
        "mode": mode,
        "io_mode": io_mode,
        "model": model,
        "request": {
            "messages": messages,
            "tools": tools,
            **(request_extras or {}),
        },
        "response": {
            "message": assistant_message,
            "usage": usage,
            "finish_reason": finish_reason,
        },
        "trajectory": {
            "input_ids": input_ids,
            "attention_mask": [1] * len(input_ids),
            "response_mask": [0] * len(prompt_ids) + [1] * len(response_ids),
            "prompt_text": prompt_text,
            "prompt_ids": prompt_ids,
            "response_text": response_text,
            "response_ids": response_ids,
            "response_logprobs": response_logprobs,
            "tool_calls": tool_calls,
        },
    }
    if extra_fields:
        sample.update(extra_fields)
    return sample
