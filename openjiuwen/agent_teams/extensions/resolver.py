# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Production resolver and read view over the team database.

The team Ops namespace resolves its data through a ``TeamResourceResolver``
(see ``ops.py``). :class:`DatabaseResolver` is the production
implementation: it answers from the database — the source of truth — so a
team stays queryable after its runtime is stopped, whether or not a
runtime for it exists in this process at all. The database instance comes
from a host-provided async factory (typically the process-wide shared
singleton keyed by connection string), which keeps engine creation out of
the per-query path.

This module deliberately keeps database consumption structural (no
imports from ``agent_teams.runtime`` / ``agent_teams.agent`` at module
level): those packages pull the full TeamAgent assembly chain, which
imports this package in turn.
"""

from __future__ import annotations

import contextlib
from typing import TYPE_CHECKING, Awaitable, Callable

from openjiuwen.agent_teams.context import (
    get_session_id,
    reset_session_id,
    set_session_id,
)
from openjiuwen.agent_teams.extensions.ops import TaskDependencyInfo, TeamReadView
from openjiuwen.agent_teams.monitor.models import MemberInfo, MessageInfo, TaskInfo, TeamInfo

if TYPE_CHECKING:
    from openjiuwen.agent_teams.tools.database import TeamDatabase


class BackendTeamReadView(TeamReadView):
    """Read view over one team's rows, bound to one session.

    Per-session dynamic tables resolve through the ``agent_teams.context``
    contextvar; every query binds the view's session id for its duration
    (the same pattern as ``TeamMonitor._bound_session``).
    """

    def __init__(
        self,
        db: "TeamDatabase",
        team_name: str,
        *,
        session_id: str | None,
        hide_dm: bool = False,
    ) -> None:
        """Initialize the view over the shared team database.

        Args:
            db: The team database; team and member rows live in its static
                tables, task and message rows in per-session dynamic ones.
            team_name: The team this view queries.
            session_id: Session whose dynamic tables the queries bind; bound
                around every query so per-session tables resolve. The Ops
                call may run outside the leader's loop.
            hide_dm: Whether direct (non-broadcast) messages are hidden
                for this caller; the host's visibility policy.
        """
        self._db = db
        self._team_name = team_name
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
        """Return the team's tasks, optionally filtered by status.

        A session that never ran a team has no task tables: its data is
        empty — reads never run DDL.
        """
        with self._bound_session():
            if not await self._db.has_cur_session_tables("task"):
                return []
            rows = await self._db.task.get_team_tasks(self._team_name, status=status)
        return [TaskInfo.from_internal(row) for row in rows]

    async def task(self, task_id: str) -> TaskInfo | None:
        """Return one task, or None if not found."""
        with self._bound_session():
            if not await self._db.has_cur_session_tables("task"):
                return None
            row = await self._db.task.get_task(task_id)
        return TaskInfo.from_internal(row) if row is not None else None

    async def task_dependencies(self, task_id: str) -> list[TaskDependencyInfo]:
        """Return the task's direct dependencies.

        A session that never ran a team has no task tables: no
        dependencies to report.
        """
        with self._bound_session():
            if not await self._db.has_cur_session_tables("task"):
                return []
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
        """Return the team's messages; the Ops layer filters and paginates.

        A session that never ran a team has no message tables: its data
        is empty — reads never run DDL.
        """
        with self._bound_session():
            if not await self._db.has_cur_session_tables("message"):
                return []
            rows = await self._db.message.get_team_messages(self._team_name)
        return [MessageInfo.from_internal(row) for row in rows]

    async def message(self, message_id: str) -> MessageInfo | None:
        """Return one message, or None if not found."""
        with self._bound_session():
            if not await self._db.has_cur_session_tables("message"):
                return None
            row = await self._db.message.get_message(message_id)
        return MessageInfo.from_internal(row) if row is not None else None


class DatabaseResolver:
    """Resolve read views directly over the team database.

    The database is the source of truth: a team with a row in the static
    ``team_info`` table is queryable whether or not a runtime for it is
    currently active, so stopped teams keep answering queries. The
    database instance comes from the host-provided factory, called at
    resolve time; nothing about the runtime pool participates in
    resolution, and the read path never runs DDL — dynamic tables are
    created only when a session binds (the team-runtime write path).
    """

    def __init__(
        self,
        db_factory: "Callable[[], Awaitable[TeamDatabase | None]]",
        *,
        hide_dm: bool = False,
    ) -> None:
        """Initialize the resolver over one database factory.

        Args:
            db_factory: Async factory returning an initialized
                ``TeamDatabase``, or ``None`` when the host has no team
                storage configured (every team then resolves to ``None``).
                The factory hands out the shared database — which session's
                dynamic tables a view binds is the view's per-query concern,
                not the factory's. Implementations should return a cached
                instance (e.g. the process-wide shared singleton keyed by
                connection string) — engine creation must stay out of the
                per-query path.
            hide_dm: The host's message visibility policy, forwarded to
                every resolved view.
        """
        self._db_factory = db_factory
        self._hide_dm = hide_dm

    async def resolve(self, session_id: str | None, team_name: str) -> TeamReadView | None:
        """Return the team's read view, or None when no team row exists.

        ``session_id`` selects which session's dynamic tables the view
        binds; it does not gate resolution — cross-session visibility is
        capability-based (knowing the team name is the access).
        """
        if not session_id:
            return None
        db = await self._db_factory()
        if db is None or not await db.team.team_exists(team_name):
            return None
        return BackendTeamReadView(
            db,
            team_name,
            session_id=session_id,
            hide_dm=self._hide_dm,
        )


__all__ = [
    "BackendTeamReadView",
    "DatabaseResolver",
]
