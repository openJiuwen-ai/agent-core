# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Parent-session checkpoint namespace for subagent records and qa rounds."""

from __future__ import annotations

from typing import Any

SUBAGENTS_KEY = "subagents"
DEFAULT_SNAPSHOT_PAGE_SIZE = 20
MAX_TURNS_PER_INSTANCE = 50
MAX_ACTIVITIES_PER_INSTANCE = 50


def empty_subagent_bucket() -> dict[str, Any]:
    """Return an empty subagents namespace bucket."""
    return {"records": {}, "turns": {}, "activities": {}, "revision": 0}


def read_subagent_bucket(session) -> dict[str, Any]:
    """Return the subagents namespace bucket, or an empty bucket if absent."""
    bucket = session.get_state(SUBAGENTS_KEY)
    if not isinstance(bucket, dict):
        return empty_subagent_bucket()
    records = bucket.get("records")
    turns = bucket.get("turns")
    activities = bucket.get("activities")
    revision = bucket.get("revision", 0)
    return {
        "records": dict(records) if isinstance(records, dict) else {},
        "turns": dict(turns) if isinstance(turns, dict) else {},
        "activities": dict(activities) if isinstance(activities, dict) else {},
        "revision": int(revision) if isinstance(revision, int) else 0,
    }


def merge_subagent_bucket(session, partial: dict[str, Any]) -> None:
    """Merge ``partial`` into the subagents namespace (shallow merge on bucket keys)."""
    bucket = read_subagent_bucket(session)
    for key, value in partial.items():
        if key in {"records", "turns", "activities"} and isinstance(value, dict):
            merged = dict(bucket.get(key) or {})
            merged.update(value)
            bucket[key] = merged
        else:
            bucket[key] = value
    session.update_state({SUBAGENTS_KEY: bucket})


def max_persisted_records(max_subagents: int) -> int:
    """Upper bound on persisted subagent records for one parent session."""
    return max(max_subagents * 4, 40)


def trim_persisted_bucket(
    records: dict[str, Any],
    turns: dict[str, Any],
    *,
    max_records: int,
    max_turns_per_instance: int = MAX_TURNS_PER_INSTANCE,
    activities: dict[str, Any] | None = None,
    max_activities_per_instance: int = MAX_ACTIVITIES_PER_INSTANCE,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Apply FIFO record trimming and per-instance turn/activity limits."""
    trimmed_records = dict(records)
    trimmed_turns = {sid: list(items) for sid, items in turns.items() if isinstance(items, list)}
    trimmed_activities = {
        sid: list(items)
        for sid, items in (activities or {}).items()
        if isinstance(items, list)
    }

    for sid, items in list(trimmed_turns.items()):
        if len(items) <= max_turns_per_instance:
            continue
        trimmed_turns[sid] = items[-max_turns_per_instance:]

    for sid, items in list(trimmed_activities.items()):
        if len(items) <= max_activities_per_instance:
            continue
        trimmed_activities[sid] = items[-max_activities_per_instance:]

    if len(trimmed_records) <= max_records:
        return trimmed_records, trimmed_turns, trimmed_activities

    sorted_items = sorted(
        trimmed_records.items(),
        key=lambda item: _record_sort_key(item[1]),
    )
    excess = len(sorted_items) - max_records
    for sid, _raw in sorted_items[:excess]:
        trimmed_records.pop(sid, None)
        trimmed_turns.pop(sid, None)
        trimmed_activities.pop(sid, None)
    return trimmed_records, trimmed_turns, trimmed_activities


def _record_sort_key(raw: Any) -> float:
    if not isinstance(raw, dict):
        return 0.0
    closed_at = raw.get("closed_at_ms")
    if isinstance(closed_at, (int, float)):
        return float(closed_at)
    updated_at = raw.get("updated_at_ms")
    if isinstance(updated_at, (int, float)):
        return float(updated_at)
    created_at = raw.get("created_at_ms")
    if isinstance(created_at, (int, float)):
        return float(created_at)
    return 0.0


__all__ = [
    "DEFAULT_SNAPSHOT_PAGE_SIZE",
    "MAX_ACTIVITIES_PER_INSTANCE",
    "MAX_TURNS_PER_INSTANCE",
    "SUBAGENTS_KEY",
    "empty_subagent_bucket",
    "max_persisted_records",
    "merge_subagent_bucket",
    "read_subagent_bucket",
    "trim_persisted_bucket",
]
