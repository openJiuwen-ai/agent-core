# coding: utf-8

"""Generic worktree lifecycle events.

These events are decoupled from any specific event bus (team event topics,
external tracing, etc.). Callers wire a ``WorktreeEventHandler`` callback
into ``WorktreeManager`` and translate the generic events into whatever
transport their system uses.

Owner identification fields (``owner_id`` / ``tag``) are optional generic
markers. The team framework populates them from the team member's
``member_name`` / ``team_name``; single-agent callers may leave them as
``None``.
"""

from typing import Awaitable, Callable, Union

from pydantic import BaseModel


class WorktreeCreatedEvent(BaseModel):
    """Emitted after a worktree is created or recovered."""

    worktree_name: str
    """Slug identifier for this worktree."""

    worktree_path: str
    """Absolute path to the worktree directory."""

    owner_id: str | None = None
    """Owner identifier (e.g. team member name, agent id)."""

    tag: str | None = None
    """Owner grouping tag (e.g. team name)."""

    existed: bool = False
    """True if the worktree already existed (fast recovery path)."""


class WorktreeRemovedEvent(BaseModel):
    """Emitted after a worktree is removed."""

    worktree_name: str
    """Slug identifier for this worktree."""

    worktree_path: str
    """Absolute path the worktree used to live at."""

    owner_id: str | None = None
    """Owner identifier (e.g. team member name, agent id)."""

    tag: str | None = None
    """Owner grouping tag (e.g. team name)."""


WorktreeEvent = Union[WorktreeCreatedEvent, WorktreeRemovedEvent]
"""Union of all generic worktree lifecycle events."""

WorktreeEventHandler = Callable[[WorktreeEvent], Awaitable[None]]
"""Async callback receiving a generic worktree event."""
