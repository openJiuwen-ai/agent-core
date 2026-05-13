# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Mutable state for the interactive Team CLI.

:class:`TeamCliState` is the single owner of CLI-side runtime state —
active routing target, per-team stream tasks, and human-agent inbox
watch bindings. Command handlers receive the state via ``TeamCli`` and
mutate it through narrow methods rather than reaching into fields.

The state intentionally tracks what the *CLI* is doing; the runtime
pool (`TeamRuntimePool`) remains the source of truth for what the
manager is actually running. The CLI mirrors only the data it needs
to drive UI affordances (active selection, stream consumer cancellation).
"""

from __future__ import annotations

import asyncio
from dataclasses import (
    dataclass,
    field,
)
from typing import (
    TYPE_CHECKING,
    Any,
)

if TYPE_CHECKING:
    from rich.console import Console

    from openjiuwen.agent_teams.cli.spec_loader import SpecRegistry


@dataclass(slots=True)
class StreamHandle:
    """Per-team stream consumer task + runtime_ready barrier.

    Attributes:
        team_name: Team this stream belongs to.
        session_id: Session id the stream is bound to.
        runtime_ready: Future resolved with the ``team.runtime_ready``
            event payload the first time it fires; the consumer may
            time out waiting on this (default 30s) before treating the
            start as failed.
        task: The asyncio task running ``Runner.run_agent_team_streaming``.
        cancelled: Whether the CLI has issued a deliberate stop on this
            handle. Used to suppress noisy cancellation logs.
    """

    team_name: str
    session_id: str
    runtime_ready: asyncio.Future[dict[str, Any]]
    task: asyncio.Task[None]
    cancelled: bool = False


@dataclass(frozen=True, slots=True)
class WatchBinding:
    """Active human-agent inbox subscription installed by ``/team watch``."""

    team_name: str
    session_id: str
    member_name: str


@dataclass(slots=True)
class TeamCliState:
    """Full state held by a single TeamCli instance."""

    spec_registry: "SpecRegistry"
    console: "Console"
    active_team_name: str | None = None
    active_session_id: str | None = None
    pending_team_name: str | None = None
    pending_session_id: str | None = None
    stream_handles: dict[str, StreamHandle] = field(default_factory=dict)
    watch_bindings: dict[tuple[str, str, str], WatchBinding] = field(default_factory=dict)
    history_session_ids: dict[str, set[str]] = field(default_factory=dict)

    def remember_session(self, team_name: str, session_id: str) -> None:
        """Record a (team, session) pair so ``/team delete`` can collect ids."""
        bucket = self.history_session_ids.setdefault(team_name, set())
        bucket.add(session_id)

    def known_sessions(self, team_name: str) -> list[str]:
        """Return sorted history of session ids ever seen for ``team_name``."""
        return sorted(self.history_session_ids.get(team_name, set()))

    def set_active(self, team_name: str | None, session_id: str | None) -> None:
        """Update active routing target + clear pending markers."""
        self.active_team_name = team_name
        self.active_session_id = session_id
        self.pending_team_name = None
        self.pending_session_id = None

    def set_pending(self, team_name: str | None, session_id: str | None) -> None:
        """Mark an in-progress switch — used by switch handlers for rollback."""
        self.pending_team_name = team_name
        self.pending_session_id = session_id


__all__ = [
    "StreamHandle",
    "TeamCliState",
    "WatchBinding",
]
