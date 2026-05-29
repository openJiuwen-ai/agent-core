# coding: utf-8

"""Worktree session state management via ContextVar.

Uses a mutable holder inside the ContextVar so that mutations propagate
across ``asyncio.gather`` Task boundaries. ``asyncio.gather`` copies the
ContextVar binding (the holder reference), not the holder itself, so tool
calls within the same agent share the same holder and see each other's
changes.
"""

from contextvars import ContextVar
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from openjiuwen.harness.tools.worktree.models import WorktreeSession


@dataclass
class WorktreeSessionState:
    """Mutable container for worktree state in the current agent session.

    asyncio.gather copies the ContextVar binding (the *reference*
    to this object), not the object itself -- so tool calls within
    the same agent share the same instance and see each other's
    mutations.
    """

    session: "WorktreeSession | None" = None
    default_worktree_name: str | None = None


_state: ContextVar[WorktreeSessionState | None] = ContextVar(
    "worktree_session_state",
    default=None,
)


def _get_state() -> WorktreeSessionState:
    s = _state.get()
    if s is None:
        s = WorktreeSessionState()
        _state.set(s)
    return s


def get_current_session() -> "WorktreeSession | None":
    """Get the active worktree session for the current agent.

    Returns:
        The current WorktreeSession, or None if not in a worktree.
    """
    return _get_state().session


def set_current_session(session: "WorktreeSession | None") -> None:
    """Set or clear the active worktree session.

    Args:
        session: The WorktreeSession to set, or None to clear.
    """
    _get_state().session = session


def get_default_worktree_name() -> str | None:
    """Get the session-scoped default worktree name.

    This mirrors Claude Code's plan-slug behaviour: the first unnamed
    enter_worktree call chooses a name, and later unnamed calls in the
    same conversation reuse it.
    """
    return _get_state().default_worktree_name


def set_default_worktree_name(name: str | None) -> None:
    """Set or clear the session-scoped default worktree name."""
    _get_state().default_worktree_name = name


def init_session_state() -> None:
    """Eagerly create the session state holder in the current context.

    Must be called before asyncio.gather copies the context, to ensure
    tool calls within the same agent share the same mutable holder.
    Typically called during agent setup (e.g. when registering worktree tools).
    """
    _get_state()


def require_current_session() -> "WorktreeSession":
    """Get the active session, raising if not in a worktree.

    Returns:
        The current WorktreeSession.

    Raises:
        RuntimeError: If no worktree session is active.
    """
    session = _get_state().session
    if session is None:
        raise RuntimeError("Not in a worktree session")
    return session
