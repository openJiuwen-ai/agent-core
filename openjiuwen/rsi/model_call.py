# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Retry guards for model-service calls used by auto-coordinating harness."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from openjiuwen.rsi.text_encoding import (
    has_unrepaired_mojibake,
    repair_text_mojibake,
)

DEFAULT_MODEL_CALL_MAX_RETRIES = 20
_TIMEOUT_MARKERS = (
    "timeout",
    "timed out",
    "time out",
    "read timed out",
    "deadline",
)


class RetryableModelOutputError(RuntimeError):
    """Raised when a model call returns retryable but unusable output."""


async def run_model_call_with_retries(
    call: Callable[[], Awaitable[str]],
    *,
    operation_name: str,
    max_retries: int = DEFAULT_MODEL_CALL_MAX_RETRIES,
) -> str:
    """Run a model call, retrying timeout failures and garbled text outputs.

    ``max_retries`` means retry count after the first attempt. A value of
    ``20`` performs up to twenty-one attempts. The final retryable error is raised
    without fallback so downstream stages never consume known-bad text.
    """
    attempts = max(1, int(max_retries or 0) + 1)
    last_retryable: BaseException | None = None
    for attempt in range(1, attempts + 1):
        try:
            raw = await call()
            if not str(raw or "").strip():
                raise RetryableModelOutputError(f"{operation_name} model output is empty")
            repaired = repair_text_mojibake(raw)
            if repaired != raw:
                if has_unrepaired_mojibake(repaired):
                    raise RetryableModelOutputError(f"{operation_name} model output contains mojibake")
                if attempt >= attempts:
                    return repaired
                raise RetryableModelOutputError(f"{operation_name} model output contains mojibake")
            if has_unrepaired_mojibake(raw):
                raise RetryableModelOutputError(f"{operation_name} model output contains mojibake")
            return raw
        except BaseException as exc:
            if not is_retryable_model_call_failure(exc):
                raise
            last_retryable = exc
            if attempt >= attempts:
                raise
            await asyncio.sleep(0)
    if last_retryable is not None:
        raise last_retryable
    raise RuntimeError(f"{operation_name} model call failed without result")


def model_output_has_mojibake(raw: str) -> bool:
    """Return whether raw model text is visibly mojibake."""
    text = str(raw or "")
    if not text:
        return False
    return repair_text_mojibake(text) != text or has_unrepaired_mojibake(text)


def is_retryable_model_call_failure(exc: BaseException) -> bool:
    """Return whether ``exc`` represents a transient model-service failure."""
    if isinstance(exc, (TimeoutError, asyncio.TimeoutError, RetryableModelOutputError)):
        return True
    message = str(exc).lower()
    return any(marker in message for marker in _TIMEOUT_MARKERS)


__all__ = [
    "DEFAULT_MODEL_CALL_MAX_RETRIES",
    "RetryableModelOutputError",
    "is_retryable_model_call_failure",
    "model_output_has_mojibake",
    "run_model_call_with_retries",
]
