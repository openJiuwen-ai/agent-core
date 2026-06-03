# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Per-team state namespace stored in agent team session checkpoints.

Session state organises per-team data under a dedicated ``teams`` bucket
keyed by ``team_name``. Each bucket holds the persisted spec, runtime
context, allocator state, and lifecycle hint for one team.

    state["teams"][team_name] = {
        "spec": ...,                      # TeamAgentSpec.model_dump()
        "context": ...,                   # TeamRuntimeContext.model_dump()
        "model_allocator_state": ...,     # optional
        "lifecycle": "running"|"paused",  # optional, written by pause path
        "db_state": "pending_create"|"created"|"cleaned",
    }

This module owns access to that namespace; recovery and runtime code call
into here rather than poking dict keys directly. ``session.update_state``
performs a shallow merge on the top level, so writes here always read the
current ``teams`` map, mutate it, and write the whole map back.
"""

from __future__ import annotations

from typing import Any

TEAMS_KEY = "teams"
TEAM_DB_STATE_KEY = "db_state"
TEAM_DB_STATE_PENDING_CREATE = "pending_create"
TEAM_DB_STATE_CREATED = "created"
TEAM_DB_STATE_CLEANED = "cleaned"


def read_teams_bucket(session) -> dict[str, dict[str, Any]]:
    """Return the full teams namespace, or an empty dict if absent."""
    teams = session.get_state(TEAMS_KEY)
    if not isinstance(teams, dict):
        return {}
    return teams


def read_team_namespace(session, team_name: str) -> dict[str, Any] | None:
    """Return the per-team bucket, or ``None`` when not persisted."""
    bucket = read_teams_bucket(session).get(team_name)
    if not isinstance(bucket, dict):
        return None
    return bucket


def read_team_names_in_session(session) -> list[str]:
    """List the team names persisted in the session."""
    return list(read_teams_bucket(session).keys())


def write_team_namespace(session, team_name: str, payload: dict[str, Any]) -> None:
    """Replace the per-team bucket with ``payload`` (full overwrite)."""
    teams = dict(read_teams_bucket(session))
    teams[team_name] = dict(payload)
    session.update_state({TEAMS_KEY: teams})


def merge_team_namespace(session, team_name: str, partial: dict[str, Any]) -> None:
    """Merge ``partial`` into the per-team bucket (creating it if absent)."""
    teams = dict(read_teams_bucket(session))
    bucket = dict(teams.get(team_name) or {})
    bucket.update(partial)
    teams[team_name] = bucket
    session.update_state({TEAMS_KEY: teams})


def read_team_db_state(session, team_name: str) -> str | None:
    """Return the persisted team DB lifecycle state, if present."""
    bucket = read_team_namespace(session, team_name)
    if bucket is None:
        return None
    value = bucket.get(TEAM_DB_STATE_KEY)
    if not isinstance(value, str):
        return None
    return value


def merge_team_db_state(session, team_name: str, state: str) -> None:
    """Persist the DB lifecycle state in the per-team bucket."""
    merge_team_namespace(session, team_name, {TEAM_DB_STATE_KEY: state})


def remove_team_namespace(session, team_name: str) -> bool:
    """Drop the per-team bucket. Returns ``True`` when removed."""
    teams = dict(read_teams_bucket(session))
    if team_name not in teams:
        return False
    del teams[team_name]
    session.update_state({TEAMS_KEY: teams})
    return True


__all__ = [
    "TEAMS_KEY",
    "TEAM_DB_STATE_KEY",
    "TEAM_DB_STATE_PENDING_CREATE",
    "TEAM_DB_STATE_CREATED",
    "TEAM_DB_STATE_CLEANED",
    "read_team_db_state",
    "read_teams_bucket",
    "read_team_namespace",
    "read_team_names_in_session",
    "write_team_namespace",
    "merge_team_namespace",
    "merge_team_db_state",
    "remove_team_namespace",
]
