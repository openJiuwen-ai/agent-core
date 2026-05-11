# coding: utf-8
"""Pure dispatch decision for ``run_agent_team_streaming``.

Resolves the (team, session, pool, checkpoint) state into one of the
documented :class:`RunActionKind` outcomes. No side effects: callers
collect the inputs (DB existence, checkpoint bucket presence, pool entry)
and let this function pick the path. The runtime manager is then free to
execute the corresponding effect (create / recover / resume / reject).

The decision table mirrors the design in
``runtime-manager-whimsical-emerson.md`` § 三:

================ ================ ============== ==================== =======
team_in_db       team_in_session  pool_entry     same_session?         kind
================ ================ ============== ==================== =======
False            False            None           —                    CREATE
False            False            present        —                    REJECT_INCONSISTENT
False            True             —              —                    REJECT_ORPHANED
True             False            None           —                    NEW_TEAM_IN_SESSION
True             True             None           —                    COLD_RECOVER
True             True             present        yes (RUNNING)        REJECT_RUNNING
True             True             present        yes (PAUSED)         RESUME_FROM_PAUSE
True             True             present        no                   WARM_RECOVER
True             False            present        any                  NEW_TEAM_IN_SESSION_WARM
================ ================ ============== ==================== =======
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional

from openjiuwen.agent_teams.runtime.pool import (
    ActiveTeam,
    RuntimeState,
)


class RunActionKind(str, Enum):
    """Outcome of dispatching a run request."""

    CREATE = "create"
    NEW_TEAM_IN_SESSION = "new_team_in_session"
    NEW_TEAM_IN_SESSION_WARM = "new_team_in_session_warm"
    COLD_RECOVER = "cold_recover"
    WARM_RECOVER = "warm_recover"
    RESUME_FROM_PAUSE = "resume_from_pause"
    REJECT_RUNNING = "reject_running"
    REJECT_ORPHANED = "reject_orphaned"
    REJECT_INCONSISTENT = "reject_inconsistent"


@dataclass(frozen=True, slots=True)
class RunAction:
    """Resolved action for a run request."""

    kind: RunActionKind
    require_spec: bool
    reason: Optional[str] = None


def decide_run_action(
    *,
    team_in_db: bool,
    team_in_session: bool,
    pool_entry: Optional[ActiveTeam],
    target_session_id: str,
    target_team_name: str,
) -> RunAction:
    """Pick the run path for the given (team, session, pool, checkpoint) state.

    Args:
        team_in_db: Whether ``target_team_name`` exists in the static team table.
        team_in_session: Whether the session checkpoint already has a bucket
            for ``target_team_name``.
        pool_entry: The pool entry for ``target_team_name`` if any, else None.
        target_session_id: The session id the caller wants to bind to.
        target_team_name: The team name being requested.

    Returns:
        A :class:`RunAction` describing what the runtime should do.
    """
    # Inconsistent: pool says active, DB says no row.
    if not team_in_db and pool_entry is not None:
        return RunAction(
            kind=RunActionKind.REJECT_INCONSISTENT,
            require_spec=False,
            reason=(
                f"team {target_team_name!r} present in pool but missing from DB"
            ),
        )

    # Orphaned: session bucket lingers but DB has no team row.
    if not team_in_db and team_in_session:
        return RunAction(
            kind=RunActionKind.REJECT_ORPHANED,
            require_spec=False,
            reason=(
                f"team {target_team_name!r} not in DB but session bucket "
                f"exists for {target_session_id!r}"
            ),
        )

    # Fresh team: needs a spec.
    if not team_in_db:
        return RunAction(kind=RunActionKind.CREATE, require_spec=True)

    # Cold paths (no pool entry, DB has team).
    if pool_entry is None:
        if team_in_session:
            return RunAction(kind=RunActionKind.COLD_RECOVER, require_spec=False)
        return RunAction(kind=RunActionKind.NEW_TEAM_IN_SESSION, require_spec=False)

    # Warm paths: pool already holds this team.
    if pool_entry.current_session_id == target_session_id:
        if pool_entry.state == RuntimeState.PAUSED:
            return RunAction(kind=RunActionKind.RESUME_FROM_PAUSE, require_spec=False)
        return RunAction(
            kind=RunActionKind.REJECT_RUNNING,
            require_spec=False,
            reason=(
                f"team {target_team_name!r} already running on session "
                f"{target_session_id!r}; use interact"
            ),
        )

    # Pool entry on a different session.
    if team_in_session:
        return RunAction(kind=RunActionKind.WARM_RECOVER, require_spec=False)
    return RunAction(kind=RunActionKind.NEW_TEAM_IN_SESSION_WARM, require_spec=False)


__all__ = ["RunAction", "RunActionKind", "decide_run_action"]
