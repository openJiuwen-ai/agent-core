# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Shared lightweight gateway constants and helpers."""

from __future__ import annotations

from datetime import datetime, timezone

NON_STANDARD_BODY_KEYS = {
    "session_id",
    "session_done",
    "turn_type",
    "memory_scope",
    "user_id",
    "workspace_id",
}


def utc_now_iso() -> str:
    """Return current UTC time in ISO-8601 format."""
    return datetime.now(timezone.utc).isoformat()


def fit_list(values: list[float], expected_len: int) -> list[float]:
    """Truncate or pad *values* so it has exactly *expected_len* entries."""
    if expected_len <= 0:
        return []
    if len(values) > expected_len:
        return values[:expected_len]
    if len(values) < expected_len:
        return values + [0.0] * (expected_len - len(values))
    return values
