# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Recent tool-call results ring buffer backed by session state (in-memory).

Stores the latest ``WINDOW_SIZE`` tool-call results per session as a plain
in-memory list inside ``session.state``.  No persistence, no disk I/O — the
buffer lives and dies with the agent session.

Keyed by ``_RECENT_RESULTS_STATE_KEY``; each entry is a dict::

    {
        "tool": "search",
        "args": {"query": "auth_flow"},
        "result": "<full result string>",
        "status": "success" | "failed",
        "error": None | "<exception string>",
        "timestamp": "2026-07-14T...",
    }
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

_RECENT_RESULTS_STATE_KEY = "_recent_tool_results"
_WINDOW_SIZE = 3

_ENTRY_REQUIRED_FIELDS: tuple[str, ...] = ("tool", "status", "timestamp")


def get_recent_results(session: Any) -> list[dict]:
    """Return the recent-results list stored in *session*.

    Returns an empty list when *session* is ``None`` or the key has never
    been written.  Never raises.

    Note: the returned list is a deep copy of the session state (production
    sessions back state access with ``deepcopy``).  Treat it as read-only —
    in-place mutations will NOT be persisted back to the session.  To add a
    new record, call :func:`record_tool_result` instead of mutating the
    returned list.
    """
    if session is None:
        return []
    try:
        state = session.get_state(_RECENT_RESULTS_STATE_KEY)
    except Exception:
        logger.exception(
            "[RecentResults] get_recent_results: failed to read state key=%s",
            _RECENT_RESULTS_STATE_KEY,
        )
        return []
    return state if isinstance(state, list) else []


def record_tool_result(session: Any, entry: dict) -> None:
    """Append *entry* to the ring buffer, keeping at most ``_WINDOW_SIZE``.

    No-op when *session* is ``None`` or *entry* is falsy.

    *entry* is expected to follow the schema documented in the module
    docstring (``tool`` / ``args`` / ``result`` / ``status`` / ``error`` /
    ``timestamp``).  Schema is not enforced — missing required fields are
    reported at DEBUG level but the entry is still stored, so callers are
    responsible for providing a complete entry.
    """
    if session is None or not entry:
        return
    if not isinstance(entry, dict):
        logger.debug(
            "[RecentResults] record_tool_result: entry is not a dict (type=%s); skipping",
            type(entry).__name__,
        )
        return
    missing = [f for f in _ENTRY_REQUIRED_FIELDS if f not in entry]
    if missing:
        logger.debug(
            "[RecentResults] record_tool_result: entry missing required fields=%s; "
            "storing anyway (caller is responsible for schema)",
            missing,
        )
    results = get_recent_results(session)
    results.append(entry)
    while len(results) > _WINDOW_SIZE:
        results.pop(0)
    try:
        session.update_state({_RECENT_RESULTS_STATE_KEY: results})
    except Exception:
        logger.exception(
            "[RecentResults] record_tool_result: failed to write state key=%s",
            _RECENT_RESULTS_STATE_KEY,
        )
        return
    logger.debug(
        "[RecentResults] recorded tool=%s status=%s queue_len=%d",
        entry.get("tool"),
        entry.get("status"),
        len(results),
    )


def clear_recent_results(session: Any) -> None:
    """Reset the ring buffer to empty.  No-op when *session* is ``None``."""
    if session is None:
        return
    try:
        session.update_state({_RECENT_RESULTS_STATE_KEY: []})
    except Exception:
        logger.exception(
            "[RecentResults] clear_recent_results: failed to write state key=%s",
            _RECENT_RESULTS_STATE_KEY,
        )
