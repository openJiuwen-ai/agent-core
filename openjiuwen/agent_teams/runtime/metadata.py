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
        "pending_resume": {"query": ...}, # optional, set by pause / consumed by start
    }

``pending_resume`` is what makes ``pause -> stop -> start`` equivalent to
``pause -> resume``: the leader's paused round stops at a clean inner-iteration
boundary and its context is committed on teardown, so a cold-started harness only
needs this marker (and the round's originating query) to continue it in place.

The query is **not** replayed into the continuation's context — that comes back
from the checkpoint. It drives the rounds that *follow* the continuation: a
task-plan continuation, or a failure retry. Without it a cold-resumed leader
stops after one round and never works off the rest of its plan.

This module owns access to that namespace; recovery and runtime code call
into here rather than poking dict keys directly. ``session.update_state``
performs a **deep merge** (``update_dict``): top-level keys are merged, not
replaced, and a sub-key is removed **only** when the incoming value is
explicitly ``None`` (the ``delete_by_key`` path). Rewriting a whole bucket
without a key does NOT drop it -- the merge preserves the existing sub-key.
So remove-class helpers here (``clear_pending_resume`` /
``remove_team_namespace``) issue a targeted ``None`` to delete, instead of
rewriting the map. Merge-class helpers (``merge_*``) add keys via the merge
and work as-is.
"""

from __future__ import annotations

from typing import Any

TEAMS_KEY = "teams"
TEAM_PENDING_RESUME_KEY = "pending_resume"
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


def read_pending_resume(session, team_name: str) -> dict[str, Any] | None:
    """Return the pending cold-resume payload, or ``None`` when absent."""
    bucket = read_team_namespace(session, team_name)
    if bucket is None:
        return None
    value = bucket.get(TEAM_PENDING_RESUME_KEY)
    if not isinstance(value, dict):
        return None
    return value


def merge_pending_resume(session, team_name: str, payload: dict[str, Any]) -> None:
    """Record what a later cold start needs to continue the paused round."""
    merge_team_namespace(session, team_name, {TEAM_PENDING_RESUME_KEY: dict(payload)})


def clear_pending_resume(session, team_name: str) -> bool:
    """Drop the pending-resume marker once consumed. ``True`` when removed.

    ``session.update_state`` performs a deep merge (``update_dict``): a key
    is removed only when the incoming value is explicitly ``None`` (the
    ``delete_by_key`` path). Rewriting the whole ``teams`` map without the
    marker does NOT remove it -- the merge preserves the existing sub-key.
    So issue a targeted ``None`` for the ``pending_resume`` sub-key, which
    deletes it while leaving the rest of the bucket (spec / context /
    db_state / lifecycle) untouched.
    """
    if read_pending_resume(session, team_name) is None:
        return False
    session.update_state({TEAMS_KEY: {team_name: {TEAM_PENDING_RESUME_KEY: None}}})
    return True


def remove_team_namespace(session, team_name: str) -> bool:
    """Drop the per-team bucket. Returns ``True`` when removed.

    Same deep-merge constraint as ``clear_pending_resume``: the bucket is
    removed by issuing a targeted ``None`` at the team level, not by
    rewriting the ``teams`` map (a rewrite would leave the bucket in place
    via the merge).
    """
    if team_name not in read_teams_bucket(session):
        return False
    session.update_state({TEAMS_KEY: {team_name: None}})
    return True


__all__ = [
    "TEAMS_KEY",
    "TEAM_PENDING_RESUME_KEY",
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
    "read_pending_resume",
    "merge_pending_resume",
    "clear_pending_resume",
    "remove_team_namespace",
]
