# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Small, dependency-free helpers for outbound request limits."""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

DEFAULT_MAX_REQUEST_MB = 10
DEFAULT_TIMEOUT_SECONDS = 120
MAX_REQUEST_MB = 1024
MAX_TIMEOUT_SECONDS = 3600
_MB = 1024 * 1024
MAX_HEADER_BYTES = 64 * 1024
MAX_URL_CHARS = 8192


class OutboundLimitError(ValueError):
    """Raised before connecting when an outbound request violates a hard limit."""

    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


def parse_positive_int(value: Any, *, name: str, default: int, maximum: int) -> int:
    """Parse a positive integer while rejecting booleans and lossy coercions."""
    if value is None:
        return default
    message = f"{name} must be an integer in [1, {maximum}]"
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(message)
    if not 1 <= value <= maximum:
        raise ValueError(message)
    return value


def request_limit_bytes(max_request_mb: int) -> int:
    return max_request_mb * _MB


def _stream_remaining_size(value: Any) -> int | None:
    """Return remaining bytes for a seekable file-like object when knowable."""
    fileno = getattr(value, "fileno", None)
    tell = getattr(value, "tell", None)
    if not callable(fileno) or not callable(tell):
        return None
    try:
        return max(0, os.fstat(fileno()).st_size - tell())
    except (OSError, TypeError, ValueError):
        return None


def _value_size(value: Any) -> int | None:
    if value is None:
        return 0
    if isinstance(value, bytes):
        return len(value)
    if isinstance(value, bytearray | memoryview):
        return len(value)
    if isinstance(value, str):
        return len(value.encode("utf-8"))
    if isinstance(value, Path):
        try:
            return value.stat().st_size
        except OSError:
            return None
    return _stream_remaining_size(value)


def estimate_request_body_bytes(kwargs: Mapping[str, Any]) -> int | None:
    """Best-effort upper estimate for common requests/aiohttp body forms.

    ``None`` means the body is streaming or otherwise not knowable before send.
    Known multipart overhead is deliberately overestimated by 1 KiB per part.
    """
    files = kwargs.get("files")
    if kwargs.get("json") is not None:
        if files:
            return None
        return len(
            json.dumps(kwargs["json"], ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        )

    total = 0
    data = kwargs.get("data")
    if data is not None:
        size = _value_size(data)
        if size is None:
            if not isinstance(data, Mapping):
                return None
            size = len(urlencode(data, doseq=True).encode("ascii"))
        total += size

    if not files:
        return total
    if not isinstance(files, Mapping):
        return None
    for value in files.values():
        payload = value
        if isinstance(value, tuple):
            if len(value) < 2:
                return None
            payload = value[1]
        size = _value_size(payload)
        if size is None:
            return None
        total += size + 1024
    return total


def enforce_request_size(kwargs: Mapping[str, Any], limit_bytes: int) -> None:
    headers = kwargs.get("headers")
    if headers is not None:
        if not isinstance(headers, Mapping):
            raise OutboundLimitError(
                "OUTBOUND_INVALID_HEADERS",
                "outbound request headers must be a mapping",
            )
        header_bytes = sum(
            len(str(key).encode("utf-8")) + len(str(value).encode("utf-8")) + 4
            for key, value in headers.items()
        )
        if header_bytes > MAX_HEADER_BYTES:
            raise OutboundLimitError(
                "OUTBOUND_HEADERS_TOO_LARGE",
                f"outbound headers are {header_bytes} bytes; limit is {MAX_HEADER_BYTES} bytes",
            )
    size = estimate_request_body_bytes(kwargs)
    if size is None:
        raise OutboundLimitError(
            "OUTBOUND_REQUEST_SIZE_UNKNOWN",
            "streaming outbound request body has no enforceable size",
        )
    if size > limit_bytes:
        raise OutboundLimitError(
            "OUTBOUND_REQUEST_TOO_LARGE",
            f"outbound request body is {size} bytes; limit is {limit_bytes} bytes",
        )


def cap_timeout(existing: Any, maximum_seconds: int, *, aiohttp_module: Any = None) -> Any:
    """Apply the policy total-time ceiling without lengthening a caller timeout."""
    if aiohttp_module is not None:
        if isinstance(existing, aiohttp_module.ClientTimeout):
            current = existing.total
            total = maximum_seconds if current is None else min(float(current), maximum_seconds)
            return aiohttp_module.ClientTimeout(
                total=total,
                connect=existing.connect,
                sock_read=existing.sock_read,
                sock_connect=existing.sock_connect,
                ceil_threshold=existing.ceil_threshold,
            )
        if existing is None:
            return aiohttp_module.ClientTimeout(total=maximum_seconds)
        if isinstance(existing, (int, float)):
            return aiohttp_module.ClientTimeout(total=min(float(existing), maximum_seconds))
    if existing is None:
        return maximum_seconds
    if isinstance(existing, tuple):
        return tuple(
            maximum_seconds if item is None else min(float(item), maximum_seconds)
            for item in existing
        )
    if isinstance(existing, (int, float)):
        return min(float(existing), maximum_seconds)
    return existing


__all__ = [
    "DEFAULT_MAX_REQUEST_MB",
    "DEFAULT_TIMEOUT_SECONDS",
    "MAX_HEADER_BYTES",
    "MAX_REQUEST_MB",
    "MAX_TIMEOUT_SECONDS",
    "MAX_URL_CHARS",
    "OutboundLimitError",
    "cap_timeout",
    "enforce_request_size",
    "estimate_request_body_bytes",
    "parse_positive_int",
    "request_limit_bytes",
]
