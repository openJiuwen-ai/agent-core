# coding: utf-8
"""In-process pool of active TeamAgent runtimes.

Replaces the single-active model with a multi-team object pool. Each
:class:`ActiveTeam` holds a TeamAgent leader instance, the session it is
currently bound to, its lifecycle state, and the :class:`InteractGate`
that gates concurrent interact deliveries.

Pool key is ``team_name`` only — a given team is bound to at most one
session at a time. Switching sessions for the same team goes through
``recover_for_existing_session`` (warm recover), not through pool
duplication. Multi-team-per-session is supported naturally by having
multiple ``ActiveTeam`` entries that share a session id.
"""

from __future__ import annotations

import asyncio
from dataclasses import (
    dataclass,
    field,
)
from enum import Enum
from typing import TYPE_CHECKING

from openjiuwen.agent_teams.runtime.gate import InteractGate

if TYPE_CHECKING:
    from openjiuwen.agent_teams.agent.team_agent import TeamAgent


class RuntimeState(str, Enum):
    """Top-level state of an ActiveTeam in the pool."""

    RUNNING = "running"
    PAUSED = "paused"


@dataclass(slots=True)
class ActiveTeam:
    """An in-memory TeamAgent runtime currently held by the pool."""

    team_name: str
    agent: "TeamAgent"
    current_session_id: str
    state: RuntimeState = RuntimeState.RUNNING
    interact_gate: InteractGate = field(default_factory=InteractGate)


@dataclass(frozen=True, slots=True)
class ActiveTeamInfo:
    """Read-only snapshot of a pooled team for external observers.

    Excludes the live ``TeamAgent`` reference and the ``InteractGate``
    so SDK/CLI consumers cannot accidentally mutate runtime state by
    holding the entry. Produced from :meth:`TeamRuntimePool.list_all_info`.
    """

    team_name: str
    current_session_id: str
    state: RuntimeState
    gate_closed: bool


class TeamRuntimePool:
    """Process-local pool of active TeamAgent runtimes keyed by team name."""

    def __init__(self) -> None:
        self._teams: dict[str, ActiveTeam] = {}
        self._lock = asyncio.Lock()

    async def get(self, team_name: str) -> ActiveTeam | None:
        """Return the entry for ``team_name`` or ``None``."""
        async with self._lock:
            return self._teams.get(team_name)

    async def has_active(self, team_name: str) -> bool:
        """Check whether the pool currently holds an entry for ``team_name``."""
        async with self._lock:
            return team_name in self._teams

    async def add(self, entry: ActiveTeam) -> None:
        """Register an active team, replacing any existing entry under the same name."""
        async with self._lock:
            self._teams[entry.team_name] = entry

    async def remove(self, team_name: str) -> ActiveTeam | None:
        """Drop the entry for ``team_name`` and return it, or ``None`` if absent."""
        async with self._lock:
            return self._teams.pop(team_name, None)

    async def list_team_names(self) -> list[str]:
        """Return a snapshot of pooled team names."""
        async with self._lock:
            return list(self._teams.keys())

    async def teams_for_session(self, session_id: str) -> list[ActiveTeam]:
        """Return active teams currently bound to ``session_id``."""
        async with self._lock:
            return [team for team in self._teams.values() if team.current_session_id == session_id]

    async def list_all_info(self) -> list[ActiveTeamInfo]:
        """Return read-only snapshots of every pooled team.

        Excludes the live ``TeamAgent`` reference so external consumers
        cannot mutate runtime state through the returned entries.
        """
        async with self._lock:
            return [
                ActiveTeamInfo(
                    team_name=entry.team_name,
                    current_session_id=entry.current_session_id,
                    state=entry.state,
                    gate_closed=entry.interact_gate.closed,
                )
                for entry in self._teams.values()
            ]


__all__ = [
    "ActiveTeam",
    "ActiveTeamInfo",
    "RuntimeState",
    "TeamRuntimePool",
]
