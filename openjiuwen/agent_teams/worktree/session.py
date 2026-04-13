# coding: utf-8

"""Worktree session state management via ContextVar.

Uses a mutable container (list) inside the ContextVar so that
mutations propagate across ``asyncio.gather`` Task boundaries.
``asyncio.gather`` copies the ContextVar binding (the *reference*
to the container), not the container itself — so tool calls
within the same agent share the same container and see each
other's changes.

Inter-agent isolation is achieved naturally: in-process spawn
copies the ContextVar reference, then the child agent's
``init_cwd()`` / first tool call operates on the same container
until it explicitly replaces it.
"""

from contextvars import ContextVar
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from openjiuwen.agent_teams.worktree.models import WorktreeSession


@dataclass
class WorktreeSessionState:
    """Mutable container for the active worktree session.

    asyncio.gather copies the ContextVar binding (the *reference*
    to this object), not the object itself -- so tool calls within
    the same agent share the same instance and see each other's
    mutations.
    """
    session: "WorktreeSession | None" = None


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


def init_session_state() -> None:
    """Eagerly create the session state holder in the current context.

    Must be called before asyncio.gather copies the context, to ensure
    tool calls within the same agent share the same mutable holder.
    Typically called during agent setup (e.g. _register_team_tools).
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
