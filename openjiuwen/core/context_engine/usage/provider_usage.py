# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Normalize legacy and provider-specific usage fields for aggregation."""

from __future__ import annotations

import math
from typing import Any

from openjiuwen.core.context_engine.usage.models import RequestKVCacheUsage


def request_usage_from_metadata(usage: Any) -> RequestKVCacheUsage:
    """Convert provider metadata to the canonical cache-usage contract.

    Missing values remain ``None``.  Invalid provider values are marked with
    ``invalid_reason`` and are intentionally not clamped into a plausible
    miss; the session aggregator will exclude that request from hit/miss
    totals while retaining its quality state.
    """
    if usage is None:
        return RequestKVCacheUsage()

    input_tokens, input_error = _parse_nonnegative(getattr(usage, "input_tokens", None), "input_tokens")
    raw_read = getattr(usage, "cache_read_tokens", None)
    if raw_read is None:
        # ``cache_tokens`` is the legacy read-token field.  The model default
        # is zero, which historically meant "not reported" when no explicit
        # cache field was present.
        legacy_cache = getattr(usage, "cache_tokens", None)
        raw_read = legacy_cache if legacy_cache not in (None, 0) else None
    read_tokens, read_error = _parse_nonnegative(raw_read, "cache_read_tokens")
    miss_tokens, miss_error = _parse_nonnegative(
        getattr(usage, "cache_miss_tokens", None), "cache_miss_tokens"
    )
    write_tokens, write_error = _parse_nonnegative(
        getattr(usage, "cache_write_tokens", None), "cache_write_tokens"
    )

    errors = [error for error in (input_error, read_error, miss_error, write_error) if error]
    invalid_reason = "; ".join(errors) or None
    if input_tokens is not None and read_tokens is not None and read_tokens > input_tokens:
        invalid_reason = _append_reason(invalid_reason, "cache_read_tokens_exceeds_input_tokens")

    miss_tokens_derived = False
    if invalid_reason is None and input_tokens is not None and read_tokens is not None:
        expected_miss = input_tokens - read_tokens
        if miss_tokens is None:
            miss_tokens = expected_miss
            miss_tokens_derived = True
        elif miss_tokens != expected_miss:
            invalid_reason = _append_reason(invalid_reason, "cache_read_plus_miss_does_not_equal_input")
        if write_tokens is not None and write_tokens > expected_miss:
            invalid_reason = _append_reason(invalid_reason, "cache_write_tokens_exceeds_cache_miss_tokens")

    authoritative = bool(getattr(usage, "cache_authoritative", False))
    source = getattr(usage, "cache_source", None) or (
        "provider_usage"
        if any(
            value is not None
            for value in (read_tokens, miss_tokens, write_tokens)
        )
        else "not_reported"
    )
    status = _cache_status(
        getattr(usage, "cache_status", None),
        input_tokens=input_tokens,
        read_tokens=read_tokens,
        miss_tokens=miss_tokens,
        invalid_reason=invalid_reason,
    )

    return RequestKVCacheUsage(
        input_tokens=input_tokens,
        cache_read_tokens=read_tokens,
        cache_miss_tokens=miss_tokens,
        cache_write_tokens=write_tokens,
        hit_rate=(
            read_tokens / input_tokens
            if read_tokens is not None and input_tokens and invalid_reason is None
            else None
        ),
        status=status,
        source=source,
        authoritative=authoritative,
        miss_tokens_derived=miss_tokens_derived,
        invalid_reason=invalid_reason,
    )


def _parse_nonnegative(value: Any, field_name: str) -> tuple[int | None, str | None]:
    if value is None:
        return None, None
    if isinstance(value, bool):
        return None, f"{field_name}_is_not_an_integer"
    if isinstance(value, int):
        return (value, None) if value >= 0 else (None, f"{field_name}_is_negative")
    if isinstance(value, float):
        if not math.isfinite(value) or not value.is_integer():
            return None, f"{field_name}_is_not_an_integer"
        integer = int(value)
        return (integer, None) if integer >= 0 else (None, f"{field_name}_is_negative")
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None, f"{field_name}_is_not_an_integer"
        digits = text[1:] if text.startswith("+") else text
        if not digits.isdigit():
            return None, f"{field_name}_is_not_an_integer"
        integer = int(text)
        return (integer, None) if integer >= 0 else (None, f"{field_name}_is_negative")
    return None, f"{field_name}_is_not_an_integer"


def _append_reason(current: str | None, reason: str) -> str:
    return f"{current}; {reason}" if current else reason


def _cache_status(
    status: Any,
    *,
    input_tokens: int | None,
    read_tokens: int | None,
    miss_tokens: int | None,
    invalid_reason: str | None,
) -> str:
    if invalid_reason:
        return "invalid"
    if input_tokens is None or read_tokens is None:
        return "not_reported"
    if isinstance(status, str) and status in {"miss", "partial_hit", "full_hit"}:
        return status
    if read_tokens <= 0:
        return "miss"
    if miss_tokens is not None:
        return "full_hit" if miss_tokens == 0 else "partial_hit"
    return "full_hit" if read_tokens == input_tokens else "partial_hit"


__all__ = ["request_usage_from_metadata"]
