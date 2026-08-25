# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Retry guards for model-service calls used by auto-coordinating harness."""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Awaitable, Callable

from openjiuwen.rsi.text_encoding import (
    has_unrepaired_mojibake,
    repair_text_mojibake,
)

DEFAULT_MODEL_CALL_MAX_RETRIES = 20
DEFAULT_MODEL_CALL_RETRY_DELAY_SECONDS = 1.0
DEFAULT_MODEL_CALL_MAX_RETRY_DELAY_SECONDS = 10.0
_TIMEOUT_MARKERS = (
    "timeout",
    "timed out",
    "time out",
    "read timed out",
    "deadline",
)
_TRANSIENT_SERVICE_MARKERS = (
    "bad gateway",
    "service unavailable",
    "gateway timeout",
    "internal server error",
    "connection error",
    "connection reset",
    "connection refused",
    "server disconnected",
    "remote protocol error",
    "temporarily unavailable",
    "too many requests",
    "rate limit",
)
# Some OpenAI-compatible gateways report connector startup failures as HTTP
# 401 even though authentication succeeded. Keep these markers deliberately
# narrow so a genuine invalid credential still fails immediately.
_TRANSIENT_CONNECTOR_BACKEND_MARKERS = (
    "database system is not yet accepting connections",
    "consistent recovery state has not been yet reached",
    "database system is starting up",
    "database system is in recovery mode",
)
_NON_RETRYABLE_SERVICE_MARKERS = (
    "budget has been exceeded",
    "budget_exceeded",
    "insufficient_quota",
    "invalid api key",
    "authentication failed",
    "permission denied",
)
_TRANSIENT_STATUS_RE = re.compile(r"(?:error\s+code|status(?:\s+code)?|http)\D{0,16}(408|409|429|5\d\d)\b")
_TRANSIENT_HTML_STATUS_RE = re.compile(r"<(?:title|h1)>\s*5\d\d\b")

logger = logging.getLogger(__name__)


class RetryableModelOutputError(RuntimeError):
    """Raised when a model call returns retryable but unusable output."""


async def run_model_call_with_retries(
    call: Callable[[], Awaitable[str]],
    *,
    operation_name: str,
    max_retries: int = DEFAULT_MODEL_CALL_MAX_RETRIES,
    initial_retry_delay_seconds: float = DEFAULT_MODEL_CALL_RETRY_DELAY_SECONDS,
    max_retry_delay_seconds: float = DEFAULT_MODEL_CALL_MAX_RETRY_DELAY_SECONDS,
) -> str:
    """Run a model call, retrying transient failures and unusable outputs.

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
            delay = _retry_delay_seconds(
                exc,
                attempt=attempt,
                initial_delay_seconds=initial_retry_delay_seconds,
                max_delay_seconds=max_retry_delay_seconds,
            )
            logger.warning(
                "%s attempt %s/%s failed transiently; retrying in %.1fs: %s",
                operation_name,
                attempt,
                attempts,
                delay,
                _error_excerpt(exc),
            )
            await asyncio.sleep(delay)
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
    if any(marker in message for marker in _TRANSIENT_CONNECTOR_BACKEND_MARKERS):
        return True
    if any(marker in message for marker in _NON_RETRYABLE_SERVICE_MARKERS):
        return False
    if any(marker in message for marker in _TIMEOUT_MARKERS):
        return True
    if any(marker in message for marker in _TRANSIENT_SERVICE_MARKERS):
        return True
    if _TRANSIENT_STATUS_RE.search(message):
        return True
    return _TRANSIENT_HTML_STATUS_RE.search(message) is not None


def _retry_delay_seconds(
    exc: BaseException,
    *,
    attempt: int,
    initial_delay_seconds: float,
    max_delay_seconds: float,
) -> float:
    if isinstance(exc, RetryableModelOutputError):
        return 0.0
    initial = max(0.0, float(initial_delay_seconds or 0.0))
    maximum = max(initial, float(max_delay_seconds or 0.0))
    return min(maximum, initial * (2 ** max(0, attempt - 1)))


def _error_excerpt(exc: BaseException, limit: int = 300) -> str:
    text = " ".join(str(exc).split())
    if len(text) <= limit:
        return text
    return f"{text[:limit]}..."


__all__ = [
    "DEFAULT_MODEL_CALL_MAX_RETRIES",
    "DEFAULT_MODEL_CALL_MAX_RETRY_DELAY_SECONDS",
    "DEFAULT_MODEL_CALL_RETRY_DELAY_SECONDS",
    "RetryableModelOutputError",
    "is_retryable_model_call_failure",
    "model_output_has_mojibake",
    "run_model_call_with_retries",
]
