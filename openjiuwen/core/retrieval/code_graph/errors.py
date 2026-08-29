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
    BUILDING = "BUILDING"
    UNAVAILABLE = "UNAVAILABLE"
    ERROR = "ERROR"


class CodeGraphBusy(Exception):
    """The graph is updating longer than the interactive wait budget."""

    def __init__(
        self,
        status: CodeGraphStatus,
        message: str,
        *,
        index: object | None = None,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.message = message
        self.index = index


class CodeGraphLimitExceeded(Exception):
    """Repository (or process) is over a Code Graph hard cap. Do not publish a graph."""

    def __init__(
        self,
        message: str,
        *,
        limit: str,
        observed: int | str,
        cap: int | str,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.limit = limit
        self.observed = observed
        self.cap = cap

    def payload_extra(self) -> dict[str, object]:
        return {
            "reason": "limit_exceeded",
            "limit": self.limit,
            "observed": self.observed,
            "cap": self.cap,
            "next_actions": [
                {
                    "tool": "grep",
                    "reason": (
                        "repository exceeds Code Graph limits; search with "
                        "grep or read_file, or raise max_files, "
                        "max_source_bytes, max_build_rss_mb, or max_cache_size_mb"
                    ),
                }
            ],
        }


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
