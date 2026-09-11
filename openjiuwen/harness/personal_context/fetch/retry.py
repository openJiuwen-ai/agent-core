"""Bounded retry helpers for PersonalContext provider reads."""

from __future__ import annotations

import asyncio
import errno
import json
import logging
import random
import re
from collections.abc import Awaitable, Callable
from typing import TypeVar

import aiohttp

_T = TypeVar("_T")
_LOGGER = logging.getLogger(__name__)
_MAX_ATTEMPTS = 3
_BASE_DELAYS_SECONDS = (1.0, 2.0)
_MAX_JITTER_SECONDS = 0.25
_FILE_CHANGED_ERRNO = getattr(errno, "ESTALE", 116)
_SAFE_LABEL = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_REASON_KINDS = frozenset(
    {
        "connection",
        "timeout",
        "http_408",
        "http_429",
        "http_5xx",
        "cli_transient",
        "file_busy",
        "file_changed",
        "empty_response",
        "invalid_json",
    }
)


async def _sleep(delay: float) -> None:
    await asyncio.sleep(delay)


def _jitter_seconds() -> float:
    return random.random() * _MAX_JITTER_SECONDS


def _root_cause(exc: BaseException) -> BaseException:
    current = exc
    seen: set[int] = set()
    while id(current) not in seen:
        seen.add(id(current))
        cause = getattr(current, "cause", None) or current.__cause__
        if not isinstance(cause, BaseException):
            break
        current = cause
    return current


def retry_reason_from_http_status(status: int) -> str | None:
    """Return a safe retry reason for one HTTP status."""

    if status == 408:
        return "http_408"
    if status == 429:
        return "http_429"
    if 500 <= status <= 599:
        return "http_5xx"
    return None


def classify_transport_error(exc: BaseException) -> str | None:
    """Classify retryable transport failures without exposing their detail."""

    root = _root_cause(exc)
    if isinstance(root, (asyncio.TimeoutError, TimeoutError)):
        return "timeout"
    if isinstance(root, aiohttp.ClientResponseError):
        return retry_reason_from_http_status(root.status)
    if isinstance(root, (aiohttp.ClientConnectionError, aiohttp.ClientPayloadError)):
        return "connection"
    return None


def classify_file_error(exc: BaseException) -> str | None:
    """Classify only transient file sharing and concurrent-change failures."""

    root = _root_cause(exc)
    if isinstance(root, OSError) and root.errno == _FILE_CHANGED_ERRNO:
        return "file_changed"
    if not isinstance(root, OSError):
        return None
    if getattr(root, "winerror", None) in {32, 33}:
        return "file_busy"
    if root.errno in {errno.EAGAIN, errno.EBUSY, errno.ETXTBSY}:
        return "file_busy"
    return None


def classify_payload_error(exc: BaseException) -> str | None:
    """Classify an empty or temporarily undecodable payload."""

    root = _root_cause(exc)
    if isinstance(root, EOFError):
        return "empty_response"
    if isinstance(root, json.JSONDecodeError):
        return "invalid_json"
    return None


def _safe_label(value: str) -> str:
    return value if _SAFE_LABEL.fullmatch(value) else "invalid"


def _emit_retry_event(*, provider: str, operation: str, attempt: int, reason: str, outcome: str) -> None:
    level = logging.INFO if outcome == "recovered" else logging.WARNING
    _LOGGER.log(
        level,
        "personal_context_provider_read_retry",
        extra={
            "provider": _safe_label(provider),
            "operation": _safe_label(operation),
            "attempt": attempt,
            "max_attempts": _MAX_ATTEMPTS,
            "reason_kind": reason,
            "outcome": outcome,
        },
    )


async def retry_provider_read(
    operation: Callable[[], Awaitable[_T]],
    *,
    provider: str,
    operation_name: str,
    classify: Callable[[BaseException], str | None],
) -> _T:
    """Run one provider read with the fixed PersonalContext retry budget."""

    last_reason: str | None = None
    for attempt in range(1, _MAX_ATTEMPTS + 1):
        try:
            result = await operation()
        except Exception as exc:
            reason = classify(exc)
            if reason not in _REASON_KINDS:
                raise
            last_reason = reason
            if attempt == _MAX_ATTEMPTS:
                _emit_retry_event(
                    provider=provider,
                    operation=operation_name,
                    attempt=attempt,
                    reason=reason,
                    outcome="exhausted",
                )
                raise
            _emit_retry_event(
                provider=provider,
                operation=operation_name,
                attempt=attempt,
                reason=reason,
                outcome="retrying",
            )
            await _sleep(_BASE_DELAYS_SECONDS[attempt - 1] + _jitter_seconds())
            continue
        if last_reason is not None:
            _emit_retry_event(
                provider=provider,
                operation=operation_name,
                attempt=attempt,
                reason=last_reason,
                outcome="recovered",
            )
        return result
    raise AssertionError("retry loop is unreachable")
