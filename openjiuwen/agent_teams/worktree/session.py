# coding: utf-8

"""Worktree session state management via ContextVar.

Each coroutine (teammate) gets its own isolated session state.
In-process spawn propagates via ``contextvars.copy_context()``;
subprocess spawn is naturally isolated by separate memory spaces.
"""

from contextvars import ContextVar

from openjiuwen.agent_teams.worktree.models import WorktreeSession

_current_session: ContextVar[WorktreeSession | None] = ContextVar(
    "worktree_session",
    default=None,
)


def get_current_session() -> WorktreeSession | None:
    """Get the active worktree session for the current coroutine.

    Returns:
        The current WorktreeSession, or None if not in a worktree.
    """
    return _current_session.get()


def set_current_session(session: WorktreeSession | None) -> None:
    """Set or clear the active worktree session.

    Args:
        session: The WorktreeSession to set, or None to clear.
    """
    _current_session.set(session)


def require_current_session() -> WorktreeSession:
    """Get the active session, raising if not in a worktree.

    Returns:
        The current WorktreeSession.

    Raises:
        RuntimeError: If no worktree session is active.
    """
    session = _current_session.get()
    if session is None:
        raise RuntimeError("Not in a worktree session")
    return session
