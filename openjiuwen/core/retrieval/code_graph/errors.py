# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Structured status payloads for Code Graph tools (no LLM required)."""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Mapping


class CodeGraphStatus(StrEnum):
    """Terminal / availability status shared by tools and the subagent."""

    COMPLETE = "COMPLETE"
    PARTIAL = "PARTIAL"
    NO_MATCH = "NO_MATCH"
    # Several symbols share the requested name. The caller must disambiguate
    # with a full symbol_id; the graph must not pick one on its own.
    AMBIGUOUS = "AMBIGUOUS"
    STALE = "STALE"
    UNAVAILABLE = "UNAVAILABLE"
    ERROR = "ERROR"


def status_payload(
    status: CodeGraphStatus,
    *,
    message: str,
    extra: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a structured tool/service payload the LLM can branch on."""
    payload: dict[str, Any] = {
        "status": status.value,
        "message": message,
    }
    if extra:
        payload.update(dict(extra))
    return payload
