# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Read-side resources and call-scope bindings for the team Ops namespace.

The team Ops methods resolve their data through a
:class:`TeamResourceResolver`: given the session id and a team name it
returns a :class:`TeamReadView` — a read-only query surface over the
monitor models — or ``None`` when the team is not accessible in that
session. Binding the real resolver to the runtime pool is host wiring;
tests supply fakes.

One binding carries a call-scoped fact that Ops methods read instead of
threading it through arguments: the **current team**, bound by the Team
business entry for one event call (``team_name=None`` in a query means
"this team", never "guess the only team in the session").

It is contextvar-backed: concurrent calls stay isolated, a nested scope
restores the outer binding on exit, and return, exception and cancellation
all release the inner binding.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Iterator, Protocol

from openjiuwen.agent_teams.monitor.models import MemberInfo, MessageInfo, TaskInfo, TeamInfo

_CURRENT_TEAM: ContextVar[str | None] = ContextVar("team_ops_current_team", default=None)


@dataclass(frozen=True, slots=True)
class MessagePage:
    """One page of team messages as returned by ``ops.team.messages``.

    ``next_cursor`` is ``None`` when no further results remain. The cursor
    is opaque to developers; the query side generates it bound to the
    current session, team and filters.
    """

    items: tuple[MessageInfo, ...]
    next_cursor: str | None


@dataclass(frozen=True, slots=True)
class TaskDependencyInfo:
    """One direct task dependency as exposed through the team Ops namespace.

    ``resolved`` says whether the dependency has been released (the
    depended-on task reached a terminal state). Only direct dependencies
    are exposed; no implicit traversal of the whole graph.
    """

    task_id: str
    depends_on_task_id: str
    resolved: bool


class TeamReadView(Protocol):
    """Read-only query surface over one accessible team."""

    @property
    def hide_dm(self) -> bool:
        """Whether direct (non-broadcast) messages are hidden for this caller."""
        ...

    async def team_info(self) -> TeamInfo | None:
        """Return the team's info, or None if the team row is gone."""
        ...

    async def members(self, status: str | None = None) -> list[MemberInfo]:
        """Return the member roster, optionally filtered by status."""
        ...

    async def member(self, member_name: str) -> MemberInfo | None:
        """Return one member, or None if not found in this team."""
        ...

    async def tasks(self, status: str | None = None) -> list[TaskInfo]:
        """Return the team's tasks, optionally filtered by status."""
        ...

    async def task(self, task_id: str) -> TaskInfo | None:
        """Return one task, or None if not found."""
        ...

    async def task_dependencies(self, task_id: str) -> list[TaskDependencyInfo]:
        """Return the task's direct dependencies."""
        ...

    async def messages(self) -> list[MessageInfo]:
        """Return the team's messages (the caller filters and paginates)."""
        ...

    async def message(self, message_id: str) -> MessageInfo | None:
        """Return one message, or None if not found."""
        ...


class TeamResourceResolver(Protocol):
    """Resolve a read view for one team within one session.

    Implementations locate the session's accessible Team Runtime and reuse
    its existing access checks — never a cross-session lookup by name.
    """

    async def resolve(self, session_id: str | None, team_name: str) -> TeamReadView | None:
        """Return the team's read view, or None when not accessible."""
        ...


@contextmanager
def bind_team_scope(team_name: str | None) -> Iterator[str | None]:
    """Bind the current team for one call scope.

    ``team_name=None`` is a deliberate "no current team" binding (a batch
    tool call that cannot attribute one team, for instance): queries then
    require an explicit ``team_name`` instead of guessing.

    Args:
        team_name: The team the current event call runs for, or None.

    Yields:
        The bound team name.
    """
    token = _CURRENT_TEAM.set(team_name)
    try:
        yield team_name
    finally:
        _CURRENT_TEAM.reset(token)


def current_team_name() -> str | None:
    """Return the team bound to the current call, if any."""
    return _CURRENT_TEAM.get()


__all__ = [
    "MessagePage",
    "TaskDependencyInfo",
    "TeamReadView",
    "TeamResourceResolver",
    "bind_team_scope",
    "current_team_name",
]
