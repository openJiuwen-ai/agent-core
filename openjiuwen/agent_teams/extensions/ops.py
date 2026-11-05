# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Read-side resources and call-scope bindings for the team Ops namespace.

The team Ops methods resolve their data through a
:class:`TeamResourceResolver`: given the session id and a team name it
returns a :class:`TeamReadView` — a read-only query surface over the
monitor models — or ``None`` when the team has no row in the database.
Binding the real resolver to the team database is host wiring; tests
supply fakes.

Two bindings carry call-scoped facts that Ops methods and the policy CORE
read instead of threading them through arguments:

* the **current team**, bound by the Team business entry for one event
  call (``team_name=None`` in a query means "this team", never "guess the
  only team in the session");
* the **policy build context**, bound by the policy build entry for one
  build so the CORE and every handler read one identical snapshot;
* the **policy build loader**, bound alongside the context so the CORE
  builds from the caller's template loader (the team's workspace cache)
  instead of whatever was registered at bus-init time.

All are contextvar-backed: concurrent calls stay isolated, a nested scope
restores the outer binding on exit, and return, exception and cancellation
all release the inner binding.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Iterator, Protocol

from openjiuwen.agent_teams.extensions.models import TeamPolicyContext
from openjiuwen.agent_teams.monitor.models import MemberInfo, MessageInfo, TaskInfo, TeamInfo
from openjiuwen.agent_teams.prompts.loader import TemplateLoader

_CURRENT_TEAM: ContextVar[str | None] = ContextVar("team_ops_current_team", default=None)
_POLICY_CONTEXT: ContextVar[TeamPolicyContext | None] = ContextVar(
    "team_ops_policy_context",
    default=None,
)
_POLICY_LOADER: ContextVar[TemplateLoader | None] = ContextVar(
    "team_ops_policy_loader",
    default=None,
)


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
    """Resolve a read view for one team.

    Implementations answer from the database — the source of truth — so a
    team stays queryable after its runtime is stopped. Cross-session
    visibility is capability-based: ``session_id`` selects which session's
    dynamic tables the view binds, it does not gate resolution.
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


@contextmanager
def bind_policy_context_scope(context: TeamPolicyContext) -> Iterator[TeamPolicyContext]:
    """Bind the policy build context for one build scope.

    The build entry binds the target's effective-config snapshot before
    triggering ``team.build_policy``; the CORE and every handler read the
    same value through :func:`current_policy_context`. Concurrent builds
    stay isolated; a nested build restores the outer binding on exit.

    Args:
        context: The snapshot to bind for the duration of the block.

    Yields:
        The bound context.
    """
    token = _POLICY_CONTEXT.set(context)
    try:
        yield context
    finally:
        _POLICY_CONTEXT.reset(token)


def current_policy_context() -> TeamPolicyContext | None:
    """Return the policy context bound to the current build, if any."""
    return _POLICY_CONTEXT.get()


@contextmanager
def bind_policy_loader_scope(loader: TemplateLoader) -> Iterator[TemplateLoader]:
    """Bind the policy build's template loader for one build scope.

    Registration-time loaders cannot follow per-team workspace caches (the
    bus is wired before any team exists), so the build entry binds the
    caller's loader — rail and tool hand in their cache-bound one — and the
    CORE reads it through :func:`current_policy_loader` instead of the
    registration fallback.

    Args:
        loader: The template loader to bind for the duration of the block.

    Yields:
        The bound loader.
    """
    token = _POLICY_LOADER.set(loader)
    try:
        yield loader
    finally:
        _POLICY_LOADER.reset(token)


def current_policy_loader() -> TemplateLoader | None:
    """Return the template loader bound to the current build, if any."""
    return _POLICY_LOADER.get()


__all__ = [
    "MessagePage",
    "TaskDependencyInfo",
    "TeamReadView",
    "TeamResourceResolver",
    "bind_policy_context_scope",
    "bind_policy_loader_scope",
    "bind_team_scope",
    "current_policy_context",
    "current_policy_loader",
    "current_team_name",
]
