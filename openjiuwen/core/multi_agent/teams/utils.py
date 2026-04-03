# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""teams/utils.py -- Standalone session lifecycle helpers.

Provides two module-level helpers that encapsulate the session lifecycle
for Team.invoke() and Team.stream() when called without a Runner
(i.e. session=None, standalone mode).

They are extracted here rather than placed on BaseTeam so that:
- BaseTeam stays thin and infrastructure-agnostic.
- The helpers are independently importable and testable.
- Any Team in this package can opt in without inheritance changes.

Public API::

    from openjiuwen.core.multi_agent.teams.utils import (
        standalone_invoke_context,
        standalone_stream_context,
        make_team_session,
    )
"""
from __future__ import annotations

import asyncio
import uuid
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Awaitable, Callable, Optional, Tuple

from openjiuwen.core.session.agent_team import Session, create_agent_team_session


def make_team_session(card, message: Any) -> Session:
    """Create a fresh AgentTeamSession, reusing conversation_id when present.

    Extracts ``conversation_id`` from *message* if it is a dict; falls back
    to a new UUID so every call gets a unique session.

    Args:
        card:    TeamCard of the owning team (provides team_id).
        message: User input - dict or str.

    Returns:
        A new :class:`Session` bound to the team.
    """
    sid = (
        message.get("conversation_id") if isinstance(message, dict) else None
    ) or str(uuid.uuid4())
    return create_agent_team_session(session_id=sid, team_id=card.id)


@asynccontextmanager
async def standalone_invoke_context(
    runtime,
    card,
    message: Any,
    session: Optional[Session] = None,
) -> AsyncIterator[Tuple[Session, str]]:
    """Async context manager that owns the full session lifecycle for invoke().

    When *session* is ``None`` (standalone / no Runner), this helper:

    1. Creates a fresh :class:`Session` via :func:`make_team_session`.
    2. Calls ``session.pre_run()``.
    3. Binds the session to *runtime*.
    4. Yields ``(session, session_id)`` to the caller.
    5. In the ``finally`` block: unbinds, cleans up the message bus, and
       calls ``session.post_run()``.

    When *session* is already a :class:`Session` (Runner path), the context
    manager is a no-op wrapper - it just yields the existing session so the
    caller's code stays uniform.

    Args:
        runtime: TeamRuntime of the owning team.
        card:    TeamCard of the owning team.
        message: User input - dict or str.
        session: Externally supplied session (Runner), or ``None``.

    Yields:
        ``(team_session, session_id)`` tuple.
    """
    caller_owns = isinstance(session, Session)
    if caller_owns:
        team_session = session
    else:
        team_session = make_team_session(card, message)
        await team_session.pre_run(
            inputs=message if isinstance(message, dict) else None
        )
        runtime.bind_team_session(team_session)

    sid = team_session.get_session_id()
    try:
        yield team_session, sid
    finally:
        if not caller_owns:
            runtime.unbind_team_session(sid)
            await runtime.cleanup_session(sid)
            await team_session.post_run()


async def standalone_stream_context(
    runtime,
    card,
    message: Any,
    run_coro: Callable[[Session, str], Awaitable[None]],
    session: Optional[Session] = None,
) -> AsyncIterator[Any]:
    """Async generator that owns the full session lifecycle for stream().

    Runs *run_coro* in a background Task while the caller
    consumes chunks from ``session.stream_iterator()`` concurrently.

    Standalone path (session=None):

    1. Creates a fresh session, calls ``pre_run``, binds to *runtime*.
    2. Spawns a background Task that runs ``run_coro(session, sid)``.
    3. In the Task's ``finally``: unbinds, cleans up message bus, calls
       ``post_run``.
    4. Yields chunks from ``session.stream_iterator()``.
    5. In the outer ``finally``: awaits the Task (protects against early break)
       then re-raises any background exception.

    Runner path (session is a Session):

    1. Spawns a background Task that runs ``run_coro(session, sid)``.
    2. In the Task's ``finally``: calls ``session.close_stream()`` so that
       ``stream_iterator`` receives END_FRAME and exits cleanly.
    3. Yields chunks and awaits the Task.

    Args:
        runtime:  TeamRuntime of the owning team.
        card:     TeamCard of the owning team.
        message:  User input - dict or str.
        run_coro: ``async (session, sid) -> None`` - the Team's
                  actual work (send / publish / run_chain etc.).
        session:  Externally supplied session (Runner), or ``None``.

    Yields:
        Stream chunks emitted by the run_coro.
    """
    caller_owns = isinstance(session, Session)
    if caller_owns:
        team_session = session
    else:
        team_session = make_team_session(card, message)
        await team_session.pre_run(
            inputs=message if isinstance(message, dict) else None
        )
        runtime.bind_team_session(team_session)

    sid = team_session.get_session_id()
    bg_exc: Optional[BaseException] = None

    async def _bg() -> None:
        nonlocal bg_exc
        try:
            await run_coro(team_session, sid)
        except Exception as exc:  # noqa: BLE001
            bg_exc = exc
        finally:
            if not caller_owns:
                runtime.unbind_team_session(sid)
                await runtime.cleanup_session(sid)
                await team_session.post_run()
            else:
                # Runner owns lifecycle; just signal end-of-stream.
                await team_session.close_stream()

    task = asyncio.create_task(_bg())
    try:
        async for chunk in team_session.stream_iterator():
            yield chunk
    finally:
        await task  # always wait - protects against consumer break/throw
        if bg_exc is not None:
            raise bg_exc  # propagate background exception to caller


__all__ = [
    "make_team_session",
    "standalone_invoke_context",
    "standalone_stream_context",
]
