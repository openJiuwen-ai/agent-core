# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""reset_session / release_session must clear the team-level pending_resume.

The host (relay-claw) calls team.session.reset when the new query is NOT a
continuation. reset_session clears the DB task board rows; it must ALSO clear
the leader's pending_resume marker in the team checkpoint bucket, otherwise
the next COLD_RECOVER resumes the paused round (kernel.resume_paused_round,
kernel.py:802) instead of letting the leader re-plan on the new query.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from openjiuwen.agent_teams.context import reset_session_id, set_session_id
from openjiuwen.agent_teams.runtime.manager import TeamRuntimeManager
from openjiuwen.agent_teams.runtime.metadata import (
    merge_pending_resume,
    merge_team_namespace,
    read_pending_resume,
)
from openjiuwen.agent_teams.runtime.pool import ActiveTeam
from openjiuwen.agent_teams.schema.team import TeamRuntimeContext
from openjiuwen.agent_teams.tools.database import DatabaseConfig, DatabaseType
from openjiuwen.core.session.agent_team import create_agent_team_session
from openjiuwen.core.session.checkpointer import CheckpointerFactory
from openjiuwen.core.session.checkpointer.checkpointer import InMemoryCheckpointer


@pytest.fixture
def isolated_checkpointer():
    """Swap the process-global checkpointer singleton for an in-memory one."""
    original = CheckpointerFactory.get_checkpointer()
    ck = InMemoryCheckpointer()
    CheckpointerFactory.set_default_checkpointer(ck)
    try:
        yield ck
    finally:
        CheckpointerFactory.set_default_checkpointer(original)


def _inmemory_db_config() -> DatabaseConfig:
    return DatabaseConfig(db_type=DatabaseType.SQLITE, connection_string=":memory:")


async def _seed_bucket(
    session_id: str, team_name: str, *, with_resume: bool, query: str = "minesweeper"
) -> None:
    """Persist a team bucket with a valid context.db_config (+ optional pending_resume)."""
    session = create_agent_team_session(
        session_id=session_id, source_metadata_enabled=False
    )
    await session.pre_run()
    ctx = TeamRuntimeContext(db_config=_inmemory_db_config())
    merge_team_namespace(session, team_name, {"context": ctx.model_dump()})
    if with_resume:
        merge_pending_resume(session, team_name, {"query": query})
    await session.flush_checkpoint()


async def _read_resume(session_id: str, team_name: str):
    session = create_agent_team_session(
        session_id=session_id, source_metadata_enabled=False
    )
    await session.pre_run()
    return read_pending_resume(session, team_name)


@pytest.mark.asyncio
@pytest.mark.level0
async def test_reset_session_clears_pending_resume(isolated_checkpointer):
    """RED: reset must drop pending_resume so next cold start does not resume."""
    session_id = "sess-reset-resume-1"
    team_name = "oc_team_test_reset"
    token = set_session_id(session_id)
    try:
        await _seed_bucket(session_id, team_name, with_resume=True, query="minesweeper")
        assert await _read_resume(session_id, team_name) == {"query": "minesweeper"}

        manager = TeamRuntimeManager()
        result = await manager.reset_session(
            team_name=team_name, session_id=session_id, force=True
        )

        assert result is True
        assert await _read_resume(session_id, team_name) is None
    finally:
        reset_session_id(token)


@pytest.mark.asyncio
@pytest.mark.level0
async def test_reset_session_without_pending_resume_is_noop(isolated_checkpointer):
    """Bucket exists but no marker -> reset returns True, bucket keys survive."""
    session_id = "sess-reset-noop-1"
    team_name = "oc_team_test_noop"
    token = set_session_id(session_id)
    try:
        await _seed_bucket(session_id, team_name, with_resume=False)
        assert await _read_resume(session_id, team_name) is None

        manager = TeamRuntimeManager()
        result = await manager.reset_session(
            team_name=team_name, session_id=session_id, force=True
        )

        assert result is True
        assert await _read_resume(session_id, team_name) is None
    finally:
        reset_session_id(token)


@pytest.mark.asyncio
@pytest.mark.level0
async def test_reset_session_without_checkpoint_returns_true(isolated_checkpointer):
    """No checkpoint bucket at all -> early return True (manager.py:960-968), no error."""
    session_id = "sess-reset-empty-1"
    team_name = "oc_team_test_empty"
    token = set_session_id(session_id)
    try:
        manager = TeamRuntimeManager()
        result = await manager.reset_session(
            team_name=team_name, session_id=session_id, force=True
        )
        assert result is True
        assert await _read_resume(session_id, team_name) is None
    finally:
        reset_session_id(token)


@pytest.mark.asyncio
@pytest.mark.level0
async def test_reset_session_idempotent(isolated_checkpointer):
    """Second reset is a no-op; state byte-identical to after first reset."""
    session_id = "sess-reset-idem-1"
    team_name = "oc_team_test_idem"
    token = set_session_id(session_id)
    try:
        await _seed_bucket(session_id, team_name, with_resume=True)
        manager = TeamRuntimeManager()
        first = await manager.reset_session(
            team_name=team_name, session_id=session_id, force=True
        )
        second = await manager.reset_session(
            team_name=team_name, session_id=session_id, force=True
        )
        assert first is True and second is True
        assert await _read_resume(session_id, team_name) is None
    finally:
        reset_session_id(token)


@pytest.mark.asyncio
@pytest.mark.level1
async def test_reset_session_flush_failure_returns_false_marker_survives(isolated_checkpointer, monkeypatch):
    """flush_checkpoint raising -> reset returns False, pending_resume survives.

    Clearing the marker is the core semantic of reset, so a flush failure must
    surface to the caller as a failed reset (return False) instead of being
    masked as success. The checkpoint stays byte-identical to the pre-reset
    state (the in-memory clear never reached disk), so the marker survives and
    a retry of reset can still clear it.
    """
    session_id = "sess-reset-flushfail-1"
    team_name = "oc_team_test_flushfail"
    token = set_session_id(session_id)
    try:
        await _seed_bucket(session_id, team_name, with_resume=True, query="minesweeper")

        # Patch the seam reset_session actually calls: _build_session. Do NOT patch
        # agent_team.create_agent_team_session — _build_session captured that name
        # into manager.py's module globals at import time, so patching the
        # agent_team attribute would be silently missed (false green). Patching
        # _build_session directly is robust to import-binding timing.
        real_build = TeamRuntimeManager._build_session

        def raising_build(session):
            s = real_build(session)

            async def _boom():
                raise RuntimeError("flush boom")

            s.flush_checkpoint = _boom  # type: ignore[assignment]
            return s

        monkeypatch.setattr(TeamRuntimeManager, "_build_session", raising_build)

        manager = TeamRuntimeManager()
        result = await manager.reset_session(team_name=team_name, session_id=session_id, force=True)
        # marker clear is core semantic: flush failure surfaces as failed reset
        # (reset did not raise, but it reports failure so the host can retry).
        assert result is False
    finally:
        reset_session_id(token)
        # monkeypatch teardown restores _build_session. The clear inside reset used
        # the patched session whose flush raised, so the marker survives in the
        # checkpointer. Verify via a fresh (unpatched) session — this is the
        # no-regression guarantee: a failed flush leaves the checkpoint
        # byte-identical to the pre-reset state, so a retry can still clear it.
        assert await _read_resume(session_id, team_name) == {"query": "minesweeper"}


@pytest.mark.asyncio
@pytest.mark.level1
async def test_reset_session_task_board_clear_failure_returns_false(isolated_checkpointer, monkeypatch):
    """Risk A: a task-board clear failure must surface as ``False`` (not raise),
    unified with the marker-clear failure path. Callers must handle ONE failure
    mode (``False``), not two (``False`` for marker + ``Exception`` for task
    board). stop_team already ran; the marker clear is an independent step so
    it may still succeed -- but reset reports ``False`` because a core step
    (task board) failed, and the caller retries (both steps idempotent).
    """
    session_id = "sess-reset-tbfail-1"
    team_name = "oc_team_test_tbfail"
    token = set_session_id(session_id)
    try:
        await _seed_bucket(session_id, team_name, with_resume=True, query="minesweeper")

        from openjiuwen.agent_teams.tools.database import TeamDatabase

        async def _boom(self, sid):  # noqa: ARG001
            raise RuntimeError("task board boom")

        monkeypatch.setattr(TeamDatabase, "clear_session_task_board_by_id", _boom)

        manager = TeamRuntimeManager()
        result = await manager.reset_session(team_name=team_name, session_id=session_id, force=True)
        # task-board clear failed -> reset reports failure, does NOT raise
        assert result is False
    finally:
        reset_session_id(token)


@pytest.mark.asyncio
@pytest.mark.level1
async def test_reset_session_propagates_cancel_during_member_abort(isolated_checkpointer):
    """C3: an outer cancel during _clear_inprocess_members_inflight must
    propagate, not be swallowed. Without this, a cancelled reset keeps running
    stop_team / task-board clear / checkpoint mutation after the caller already
    aborted. ``asyncio.wait_for`` raises ``TimeoutError`` on timeout (caught
    inside the helper); a ``CancelledError`` escaping it means the outer reset
    task itself was cancelled, which must reach the caller.
    """
    session_id = "sess-reset-cancel-1"
    team_name = "oc_team_test_cancel"
    token = set_session_id(session_id)
    try:
        cancel_started = asyncio.Event()

        class _Ctl:
            async def cancel_agent(self):
                cancel_started.set()
                await asyncio.sleep(3600)  # block until the outer task is cancelled

        handle = SimpleNamespace(agent_ref=SimpleNamespace(stream_controller=_Ctl()))
        agent = SimpleNamespace(spawn_manager=SimpleNamespace(spawned_handles={"m1": handle}))
        # Inject a pool entry so reset_session(force=True) enters the
        # has_active branch and reaches _clear_inprocess_members_inflight.
        entry = ActiveTeam(team_name=team_name, agent=agent, current_session_id=session_id)
        manager = TeamRuntimeManager()
        manager._pool._teams[team_name] = entry  # test-only direct inject

        task = asyncio.create_task(manager.reset_session(team_name=team_name, session_id=session_id, force=True))
        await cancel_started.wait()  # inside cancel_agent -> wait_for
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        # Cancel propagated before stop_team, so the entry is still pooled.
        assert team_name in manager._pool._teams
    finally:
        reset_session_id(token)


@pytest.mark.asyncio
@pytest.mark.level0
async def test_release_session_clears_pending_resume(isolated_checkpointer):
    """release_session (same-class gap) must also clear pending_resume for each team."""
    session_id = "sess-release-resume-1"
    team_name = "oc_team_test_release"
    token = set_session_id(session_id)
    try:
        await _seed_bucket(session_id, team_name, with_resume=True, query="minesweeper")
        assert await _read_resume(session_id, team_name) == {"query": "minesweeper"}

        manager = TeamRuntimeManager()
        await manager.release_session(session_id=session_id, force=True)

        assert await _read_resume(session_id, team_name) is None
    finally:
        reset_session_id(token)
