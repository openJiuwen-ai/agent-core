# coding: utf-8
"""Module-level registries for shared in-process resources.

In single-process mode, leader and all teammates share the same
TeamRuntime and InMemoryTeamDatabase instances, keyed by team_id.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from openjiuwen.agent_teams.tools.memory_database import InMemoryTeamDatabase
    from openjiuwen.core.multi_agent.team_runtime.team_runtime import TeamRuntime

_shared_runtimes: dict[str, "TeamRuntime"] = {}
_shared_dbs: dict[str, "InMemoryTeamDatabase"] = {}


def get_or_create_runtime(team_id: str) -> "TeamRuntime":
    """Return the shared TeamRuntime for *team_id*, creating it on first call."""
    if team_id not in _shared_runtimes:
        from openjiuwen.core.multi_agent.team_runtime.team_runtime import TeamRuntime

        _shared_runtimes[team_id] = TeamRuntime()
    return _shared_runtimes[team_id]


def get_or_create_memory_db(team_id: str) -> "InMemoryTeamDatabase":
    """Return the shared InMemoryTeamDatabase for *team_id*, creating it on first call."""
    if team_id not in _shared_dbs:
        from openjiuwen.agent_teams.tools.memory_database import InMemoryTeamDatabase

        _shared_dbs[team_id] = InMemoryTeamDatabase()
    return _shared_dbs[team_id]


def cleanup_shared_resources(team_id: str) -> None:
    """Remove cached runtime and database for *team_id*."""
    _shared_runtimes.pop(team_id, None)
    _shared_dbs.pop(team_id, None)
