# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Production resolver and read view over the team runtime pool.

The team Ops namespace resolves its data through a ``TeamResourceResolver``
(see ``ops.py``). :class:`RuntimePoolResolver` is the production
implementation: it looks up the session's active pool entry and wraps the
entry's team backend into a :class:`BackendTeamReadView`. Hosts that own
the Runner pass ``TeamRuntimeManager.pool`` at wiring time —
``register(bus, RuntimePoolResolver(manager.pool))``.

This module deliberately keeps pool/entry consumption structural (no
imports from ``agent_teams.runtime`` / ``agent_teams.agent`` at module
level): those packages pull the full TeamAgent assembly chain, which
imports this package in turn. The global-runner lookup below is a
function-local import for the same reason — it only runs at resolve
time, when the host process has the runner loaded anyway.
"""

from __future__ import annotations

import contextlib
from typing import TYPE_CHECKING, Protocol, Sequence

from openjiuwen.agent_teams.context import (
    get_session_id,
    reset_session_id,
    set_session_id,
)
from openjiuwen.agent_teams.extensions.ops import TaskDependencyInfo, TeamReadView
from openjiuwen.agent_teams.monitor.models import MemberInfo, MessageInfo, TaskInfo, TeamInfo

if TYPE_CHECKING:
    from openjiuwen.agent_teams.tools.team import TeamBackend


class _PoolEntryAgent(Protocol):
    """The leader agent of one active pool entry (structural subset)."""

    @property
    def team_backend(self) -> "TeamBackend | None":
        """Return the entry's team backend."""
        ...


class _PoolEntry(Protocol):
    """One active team entry (structural subset of the pool's entry type)."""

    @property
    def agent(self) -> _PoolEntryAgent:
        """Return the entry's leader agent."""
        ...


class _RuntimePool(Protocol):
    """The runtime pool (structural subset of TeamRuntimePool)."""

    async def teams_for_session(self, session_id: str) -> Sequence[_PoolEntry]:
        """Return the active teams currently bound to ``session_id``."""
        ...


class BackendTeamReadView(TeamReadView):
    """Read view over one team backend, bound to one session.

    Per-session dynamic tables resolve through the ``agent_teams.context``
    contextvar; every query binds the view's session id for its duration
    (the same pattern as ``TeamMonitor._bound_session``).
    """

    def __init__(
        self,
        team_backend: "TeamBackend",
        *,
        session_id: str,
        hide_dm: bool = False,
    ) -> None:
        """Initialize the view over one backend's database.

        Args:
            team_backend: The active entry's team backend.
            session_id: Session the runtime instance runs under; bound
                around every query so per-session tables resolve.
            hide_dm: Whether direct (non-broadcast) messages are hidden
                for this caller; the host's visibility policy.
        """
        self._db = team_backend.db
        self._team_name = team_backend.team_name
        self._session_id = session_id
        self._hide_dm = hide_dm

    @property
    def hide_dm(self) -> bool:
        """Whether direct messages are hidden for this caller."""
        return self._hide_dm

    @contextlib.contextmanager
    def _bound_session(self):
        """Bind the view's session id for one query.

        Per-session dynamic tables are looked up through the contextvar;
        the Ops call may run outside the leader's loop, so the id is bound
        for the duration of every query when it differs from the current
        one.
        """
        token = None
        if self._session_id and get_session_id() != self._session_id:
            token = set_session_id(self._session_id)
        try:
            yield
        finally:
            if token is not None:
                reset_session_id(token)

    async def team_info(self) -> TeamInfo | None:
        """Return the team's info, or None if the team row is gone."""
        with self._bound_session():
            team = await self._db.team.get_team(self._team_name)
        return TeamInfo.from_internal(team) if team is not None else None

    async def members(self, status: str | None = None) -> list[MemberInfo]:
        """Return the member roster, optionally filtered by status."""
        with self._bound_session():
            rows = await self._db.member.get_team_members(self._team_name, status=status)
        return [MemberInfo.from_internal(row) for row in rows]

    async def member(self, member_name: str) -> MemberInfo | None:
        """Return one member, or None if not found in this team."""
        with self._bound_session():
            row = await self._db.member.get_member(member_name, self._team_name)
        return MemberInfo.from_internal(row) if row is not None else None

    async def tasks(self, status: str | None = None) -> list[TaskInfo]:
        """Return the team's tasks, optionally filtered by status."""
        with self._bound_session():
            rows = await self._db.task.get_team_tasks(self._team_name, status=status)
        return [TaskInfo.from_internal(row) for row in rows]

    async def task(self, task_id: str) -> TaskInfo | None:
        """Return one task, or None if not found."""
        with self._bound_session():
            row = await self._db.task.get_task(task_id)
        return TaskInfo.from_internal(row) if row is not None else None

    async def task_dependencies(self, task_id: str) -> list[TaskDependencyInfo]:
        """Return the task's direct dependencies."""
        with self._bound_session():
            rows = await self._db.task.get_task_dependencies(task_id)
        return [
            TaskDependencyInfo(
                task_id=row.task_id,
                depends_on_task_id=row.depends_on_task_id,
                resolved=bool(row.resolved),
            )
            for row in rows
        ]

    async def messages(self) -> list[MessageInfo]:
        """Return the team's messages; the Ops layer filters and paginates."""
        with self._bound_session():
            rows = await self._db.message.get_team_messages(self._team_name)
        return [MessageInfo.from_internal(row) for row in rows]

    async def message(self, message_id: str) -> MessageInfo | None:
        """Return one message, or None if not found."""
        with self._bound_session():
            row = await self._db.message.get_message(message_id)
        return MessageInfo.from_internal(row) if row is not None else None


class RuntimePoolResolver:
    """Resolve read views from the Runner's team runtime pool.

    Locates the session's accessible pool entry by team name and wraps its
    team backend; never a cross-session lookup. A team with no entry in
    this session's pool resolves to ``None`` (the Ops layer turns that
    into an explicit error).
    """

    def __init__(self, pool: _RuntimePool, *, hide_dm: bool = False) -> None:
        """Initialize the resolver over one pool.

        Args:
            pool: The runtime pool holding the active team entries
                (``TeamRuntimeManager.pool`` at host wiring time).
            hide_dm: The host's message visibility policy, forwarded to
                every resolved view.
        """
        self._pool = pool
        self._hide_dm = hide_dm

    async def resolve(self, session_id: str | None, team_name: str) -> TeamReadView | None:
        """Return the team's read view, or None when not accessible."""
        if not session_id:
            return None
        entries = await self._pool.teams_for_session(session_id)
        for entry in entries:
            backend = entry.agent.team_backend
            if backend is not None and backend.team_name == team_name:
                return BackendTeamReadView(
                    backend,
                    session_id=session_id,
                    hide_dm=self._hide_dm,
                )
        return None


class GlobalRunnerPoolResolver:
    """Resolve read views through the global Runner's team pool, lazily.

    Hosts wire the team Ops namespace at process start (``ops.build``
    freezes handlers), but the global Runner's team runtime manager is
    created lazily on the first team run — so this resolver looks the
    manager up at resolve time instead of holding a reference. Before any
    team has run the lookup finds no manager and every team resolves to
    ``None`` (an explicit Ops error), which is the correct answer: no
    active team exists yet.
    """

    def __init__(self, *, hide_dm: bool = False) -> None:
        """Initialize the lazy resolver.

        Args:
            hide_dm: The host's message visibility policy, forwarded to
                every resolved view.
        """
        self._hide_dm = hide_dm

    async def resolve(self, session_id: str | None, team_name: str) -> TeamReadView | None:
        """Return the team's read view, or None when not accessible."""
        if not session_id:
            return None
        from openjiuwen.core.runner.runner import GLOBAL_RUNNER

        # Read the manager slot directly (the runner creates it lazily on the
        # first team run); creating it here would skip the runner's own wiring.
        manager = vars(GLOBAL_RUNNER).get("_team_runtime_manager")
        if manager is None:
            return None
        resolver = RuntimePoolResolver(manager.pool, hide_dm=self._hide_dm)
        return await resolver.resolve(session_id, team_name)


__all__ = [
    "BackendTeamReadView",
    "GlobalRunnerPoolResolver",
    "RuntimePoolResolver",
]
