# coding: utf-8
"""SessionManager contextvar Token lifecycle coverage.

Locks in the invariant that ``set_session_id`` calls inside
``bind_session`` are paired with ``reset_session_id`` on the matching
release / unbind paths so the agent_teams session_id contextvar never
leaks a stale binding across rounds.

Note: the prior version of this file asserted that ``manager.session_id``
exposed a cached string. After the contextvar-only refactor that property
no longer exists — the agent_teams session_id contextvar is the single
source of truth.
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest

from openjiuwen.agent_teams.agent.session_manager import SessionManager
from openjiuwen.agent_teams.agent.state import TeamAgentState
from openjiuwen.agent_teams.context import (
    get_session_id,
    reset_session_id,
    set_session_id,
)
from openjiuwen.agent_teams.schema.team import TeamRole


@pytest.fixture(autouse=True)
def _isolate_session_id_contextvar():
    """Reset the agent_teams session_id contextvar before and after each test.

    Some sibling tests in this suite (and elsewhere in the repository)
    call ``set_session_id`` without a matching ``reset_session_id`` —
    notably ``TeamAgent.recover_from_session``. Because contextvars
    persist across pytest tests within the same process, that leaks a
    non-empty default value into otherwise pristine tests. We force a
    known-clean baseline so assertions on ``get_session_id() == ""``
    remain stable regardless of execution order.
    """
    token = set_session_id("")
    try:
        yield
    finally:
        reset_session_id(token)


def _make_session(session_id: str) -> MagicMock:
    session = MagicMock()
    session.get_session_id.return_value = session_id
    return session


def _make_manager() -> SessionManager:
    state = TeamAgentState()
    configurator = MagicMock()
    configurator.team_backend = None
    configurator.spec = None
    configurator.role = TeamRole.TEAMMATE
    recovery_manager = MagicMock()
    return SessionManager(
        state=state,
        configurator=configurator,
        recovery_manager=recovery_manager,
    )


@pytest.mark.asyncio
async def test_bind_session_sets_contextvar():
    manager = _make_manager()
    session = _make_session("sess-A")

    assert get_session_id() == ""
    await manager.bind_session(session)
    assert get_session_id() == "sess-A"


@pytest.mark.asyncio
async def test_release_session_resets_contextvar_and_drops_session():
    manager = _make_manager()
    session = _make_session("sess-A")

    await manager.bind_session(session)
    assert get_session_id() == "sess-A"

    manager.release_session()
    # contextvar must be reset (no stale leak into a sibling spawn that
    # would otherwise inherit "sess-A" via ``contextvars.copy_context``)
    assert get_session_id() == ""
    # The live team_session is dropped on release so it cannot be
    # mutated after the round ends.
    assert manager.team_session is None


@pytest.mark.asyncio
async def test_rebind_resets_prior_token_then_release_returns_to_outer():
    """Multiple bind cycles must not stack tokens; release goes back to the
    contextvar's outer value, not the prior bind's value."""
    manager = _make_manager()

    await manager.bind_session(_make_session("sess-A"))
    await manager.bind_session(_make_session("sess-B"))
    assert get_session_id() == "sess-B"

    manager.release_session()
    # If tokens stacked, this would surface "sess-A". The prior bind's
    # token must have been reset before the second set, so we go to the
    # contextvar's pre-bind value (empty string in this isolated test).
    assert get_session_id() == ""


@pytest.mark.asyncio
async def test_release_after_cross_context_bind_does_not_raise():
    """Token reset from a different asyncio.Task must be tolerated.

    Recovery / spawn flows may stash a bound SessionManager on one task
    and tear it down on another; ``contextvars.Token.reset`` raises
    ``ValueError`` in that case. The manager must swallow it instead of
    propagating to the teardown path.
    """
    manager = _make_manager()

    async def bind_in_subtask():
        await manager.bind_session(_make_session("sess-X"))

    await asyncio.create_task(bind_in_subtask())
    # Now release in the parent task — token belongs to the subtask context
    # and ``reset`` would normally raise ValueError. We expect a clean
    # release.
    manager.release_session()
    assert manager.team_session is None
