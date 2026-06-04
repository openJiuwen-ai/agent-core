# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Human-readable time rendering for agent-facing team text.

The team database stores timestamps as millisecond UTC epochs — the right
storage structure (cheap to sort, compare, and index; unambiguous across
processes and machines). But a raw epoch is useless to an LLM: it cannot
reliably reason about ordering or "how long ago" from a bare integer, which
breaks priority judgement when messages arrive with delay.

This module turns an epoch into ``<absolute local time> (<relative diff>)``,
e.g. ``2026-05-27 14:30:05 +08:00 (3 分钟前)``. The relative diff gives the
agent a sense of recency; the absolute time gives a stable anchor for
observability. The bucket-selection logic is pure Python and language
agnostic — only the wording lives in :mod:`openjiuwen.agent_teams.i18n`.
"""

from __future__ import annotations

from datetime import datetime, timezone

from openjiuwen.agent_teams.i18n import t

# Bucket boundaries in seconds.
_JUST_NOW_SECONDS = 10
_MINUTE_SECONDS = 60
_HOUR_SECONDS = 60 * 60
_DAY_SECONDS = 24 * 60 * 60


def _relative_key_and_value(delta_ms: int) -> tuple[str, int | None]:
    """Pick the relative-time i18n key and its numeric value.

    Pure numeric logic with no language coupling: the caller renders the
    returned key via :func:`i18n.t`. A negative delta (the timestamp is in
    the future, i.e. clock skew) and anything under ten seconds collapse to
    "just now" so the agent never sees a negative or noisy count.

    Args:
        delta_ms: ``now_ms - timestamp_ms``; positive means in the past.

    Returns:
        A ``(i18n_key, value)`` tuple. ``value`` is ``None`` for the
        "just now" bucket (the key carries no placeholder).
    """
    if delta_ms < 0:
        return "time.just_now", None
    seconds = delta_ms // 1000
    if seconds < _JUST_NOW_SECONDS:
        return "time.just_now", None
    if seconds < _MINUTE_SECONDS:
        return "time.seconds_ago", seconds
    if seconds < _HOUR_SECONDS:
        return "time.minutes_ago", seconds // _MINUTE_SECONDS
    if seconds < _DAY_SECONDS:
        return "time.hours_ago", seconds // _HOUR_SECONDS
    return "time.days_ago", seconds // _DAY_SECONDS


def _format_absolute(timestamp_ms: int) -> str:
    """Render an epoch as local wall-clock time with a numeric tz offset.

    Uses the runtime's local timezone (resolved via ``astimezone()``) and
    annotates the offset as ``+08:00`` so cross-machine readers can still
    align two absolute times unambiguously.

    Args:
        timestamp_ms: Millisecond UTC epoch.

    Returns:
        A string like ``2026-05-27 14:30:05 +08:00``.
    """
    dt = datetime.fromtimestamp(timestamp_ms / 1000, tz=timezone.utc).astimezone()
    base = dt.strftime("%Y-%m-%d %H:%M:%S")
    offset = dt.strftime("%z")  # e.g. "+0800"; empty only for naive datetimes
    if not offset:
        return base
    return f"{base} {offset[:3]}:{offset[3:]}"


def format_time_context(timestamp_ms: int | None, now_ms: int) -> str:
    """Render a timestamp as ``<absolute local time> (<relative diff>)``.

    Args:
        timestamp_ms: Millisecond UTC epoch to render, or ``None`` when the
            source field is unset (e.g. a task that never transitioned).
        now_ms: Current millisecond UTC epoch, used as the relative anchor.
            Always passed in so the function stays pure and testable.

    Returns:
        Localized text such as ``2026-05-27 14:30:05 +08:00 (3 分钟前)``,
        or ``time.unknown`` when ``timestamp_ms`` is ``None``.
    """
    if timestamp_ms is None:
        return t("time.unknown")
    key, value = _relative_key_and_value(now_ms - timestamp_ms)
    relative = t(key) if value is None else t(key, value=value)
    return f"{_format_absolute(timestamp_ms)} ({relative})"


__all__ = ["format_time_context"]
