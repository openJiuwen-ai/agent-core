# coding: utf-8
"""Process-global shared resources for agent teams.

Database and TeamRuntime are **process-global singletons** — they are
NOT partitioned by team_name or session_id.  Multiple teams and sessions
coexist inside the same instance; data isolation is handled internally
via team_name / session_id fields.

This applies to both single-process (in-process) mode and multi-process
mode: within a single OS process, all TeamAgent instances must share the
same database engine and the same runtime to avoid duplicated state.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Union

if TYPE_CHECKING:
    from openjiuwen.agent_teams.tools.database import TeamDatabase
    from openjiuwen.agent_teams.tools.memory_database import InMemoryTeamDatabase
    from openjiuwen.core.multi_agent.team_runtime.team_runtime import TeamRuntime

# ---- process-global singletons ------------------------------------------

_runtime: "TeamRuntime | None" = None
_memory_db: "InMemoryTeamDatabase | None" = None
# Database instances keyed by normalized db_type + connection_string.
_db_instances: dict[str, "TeamDatabase"] = {}


# ---- public API ----------------------------------------------------------

def get_shared_runtime() -> "TeamRuntime":
    """Return the process-global TeamRuntime, creating it on first call."""
    global _runtime
    if _runtime is None:
        from openjiuwen.core.multi_agent.team_runtime.team_runtime import TeamRuntime

        _runtime = TeamRuntime()
    return _runtime


def get_shared_db(config: Any) -> Union["TeamDatabase", "InMemoryTeamDatabase"]:
    """Return a process-global database instance matching *config*.

    * ``db_type == "memory"`` → single global InMemoryTeamDatabase.
    * ``db_type != "memory"``  → one TeamDatabase per db_type+connection_string.

    Multiple teams and sessions share the same instance; row-level
    isolation is provided by team_name / session_id columns.
    """
    if config.db_type == "memory":
        return _get_shared_memory_db()
    return _get_shared_db_instance(config)


def cleanup_shared_resources() -> None:
    """Reset all process-global singletons (e.g. between test runs)."""
    global _runtime, _memory_db
    _runtime = None
    _memory_db = None
    _db_instances.clear()

    from openjiuwen.agent_teams.messager.inprocess import cleanup_inprocess_bus

    cleanup_inprocess_bus()


# ---- internals -----------------------------------------------------------

def _get_shared_memory_db() -> "InMemoryTeamDatabase":
    global _memory_db
    if _memory_db is None:
        from openjiuwen.agent_teams.tools.memory_database import InMemoryTeamDatabase

        _memory_db = InMemoryTeamDatabase()
    return _memory_db


def _get_shared_db_instance(config: Any) -> "TeamDatabase":
    db_type = config.db_type
    conn_str = config.connection_string
    key = f"{db_type}::{conn_str}"
    if key not in _db_instances:
        from openjiuwen.agent_teams.tools.database import TeamDatabase

        _db_instances[key] = TeamDatabase(config)
    return _db_instances[key]
