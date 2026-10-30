# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Retryable transport failures, distinct from harness execution failures."""

from __future__ import annotations

_TRANSIENT_ERROR_MARKERS = (
    "connection error",
    "connection reset",
    "connection refused",
    "connection aborted",
    "connectionerror",
    "remoteprotocolerror",
    "peer closed connection",
    "incomplete chunked read",
    "server disconnected",
    "remotedisconnected",
    "timed out",
    "timeout",
    "temporarily unavailable",
    "temporary failure in name resolution",
    "could not resolve host",
    "service unavailable",
    "rate limit",
    "too many requests",
    "http 429",
    "http 502",
    "http 503",
    "http 504",
)


def transient_transport_error(error: str) -> str:
    """Return the original error when it describes a retryable transport failure."""
    if any(marker in error.lower() for marker in _TRANSIENT_ERROR_MARKERS):
        return error
    return ""
