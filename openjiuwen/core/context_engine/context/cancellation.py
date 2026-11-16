# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Cancellation cause registry for context-engine processors.

A user stop can pause a resumable runtime or cancel it outright. Both reach the
context engine as ``asyncio.CancelledError``, which carries no origin, so a
processor cannot tell a normal user action from an internal failure. Entry
points that know the origin mark the session before cancelling; the processor's
cancellation handler resolves the mark and reports ``paused`` / ``cancelled``
instead of the legacy ``failed``.

Marks expire, and an unmarked cancellation keeps the legacy ``failed``
behaviour, so this registry never relabels a failure on its own.

Marks are **not consumed** when resolved: a single pause/cancel can cancel
several processors (including sub-session contexts) that each need to resolve
the same mark. The flip side is a residual window — until the mark is cleared,
overwritten, or expired (TTL), any *other* cancellation for that session also
resolves to the marked cause. Entry points that resume a paused runtime must
therefore call :func:`clear_cancellation_cause`, and marking entry points
should only mark when a real cancellation is about to happen.
"""

from __future__ import annotations

import threading
import time
from enum import Enum
from typing import Optional

DEFAULT_TTL_SECONDS = 30.0
"""How long a mark stays resolvable after the cancelling call."""

_MAX_ENTRIES = 1024

_lock = threading.Lock()
_causes: dict[str, tuple[str, float]] = {}
"""session_id -> (cause value, monotonic expiry)."""


class CancellationCause(str, Enum):
    """Origin of a user-facing cancellation."""

    PAUSE = "pause"
    CANCEL = "cancel"


def _normalize_session_id(session_id: object) -> str:
    return str(session_id or "").strip()


def _coerce_cause(cause: object) -> Optional[CancellationCause]:
    if isinstance(cause, CancellationCause):
        return cause
    try:
        return CancellationCause(str(cause or "").strip().lower())
    except ValueError:
        return None


def _prune_locked(now: float) -> None:
    expired = [key for key, (_, expires_at) in _causes.items() if expires_at <= now]
    for key in expired:
        _causes.pop(key, None)
    if len(_causes) <= _MAX_ENTRIES:
        return
    # Keep the most recently marked entries; expired ones were just removed.
    overflow = sorted(_causes.items(), key=lambda item: item[1][1])[: len(_causes) - _MAX_ENTRIES]
    for key, _ in overflow:
        _causes.pop(key, None)


def mark_cancellation_cause(
    session_id: object,
    cause: object,
    *,
    ttl_seconds: float = DEFAULT_TTL_SECONDS,
) -> bool:
    """Record ``cause`` for ``session_id`` until the next cancellation.

    Returns ``False`` when the session id or cause is unusable. A repeat mark
    replaces the previous one, so pause followed by cancel resolves as cancel.
    """
    sid = _normalize_session_id(session_id)
    normalized = _coerce_cause(cause)
    if not sid or normalized is None:
        return False
    expires_at = time.monotonic() + max(float(ttl_seconds), 0.0)
    with _lock:
        now = time.monotonic()
        _prune_locked(now)
        _causes[sid] = (normalized.value, expires_at)
    return True


def clear_cancellation_cause(session_id: object) -> None:
    """Drop the mark for ``session_id`` (for example when a round resumes)."""
    sid = _normalize_session_id(session_id)
    if not sid:
        return
    with _lock:
        _causes.pop(sid, None)


def resolve_cancellation_cause(session_id: object) -> Optional[CancellationCause]:
    """Return the unexpired cause for ``session_id``, if any.

    Sub-sessions inherit their parent's cause: ``<parent>:<agent>`` (team
    protocol adapter) and ``<parent>_sub_<id>`` (subagent sessions). When
    several ancestors carry marks, the most specific (longest matching key)
    wins: a member-level mark is never shadowed by a team-level one.

    The mark is deliberately left in place: one user action can cancel
    several processors (including sub-session contexts) that all resolve the
    same mark. Callers that resume the runtime are responsible for clearing
    it via :func:`clear_cancellation_cause`.
    """
    sid = _normalize_session_id(session_id)
    if not sid:
        return None
    now = time.monotonic()
    with _lock:
        _prune_locked(now)
        entry = _causes.get(sid)
        if entry is not None:
            return CancellationCause(entry[0])
        best_key: str | None = None
        best_value: str | None = None
        for key, (value, expires_at) in _causes.items():
            if expires_at <= now:
                continue
            if not (sid.startswith(f"{key}:") or sid.startswith(f"{key}_sub_")):
                continue
            if best_key is None or len(key) > len(best_key):
                best_key = key
                best_value = value
        if best_key is not None:
            return CancellationCause(best_value)
        return None


def reset_cancellation_causes() -> None:
    """Drop every mark. Intended for tests and process teardown."""
    with _lock:
        _causes.clear()


__all__ = [
    "CancellationCause",
    "DEFAULT_TTL_SECONDS",
    "clear_cancellation_cause",
    "mark_cancellation_cause",
    "reset_cancellation_causes",
    "resolve_cancellation_cause",
]
