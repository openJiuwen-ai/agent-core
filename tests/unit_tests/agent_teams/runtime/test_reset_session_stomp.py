# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""reset_session must clear pending_resume on the LIVE session, not a throwaway
session RMW -- otherwise a live session whose in-memory state still carries the
marker writes it back via its post_run (the authoritative full-overwrite flush),
re-introducing the deleted marker.

Stomp mechanism (code facts): AgentTeamStorage.save is a full-overwrite of the
entire global state per (session_id, team_id="agent_team") blob (inmemory.py:410-
415 / persistence.py:172-206); all team sessions share team_id="agent_team"
(agent_team.py:30; _build_session manager.py:1336; _create_agent_team_session
team_runner.py:786-796). reset's throwaway clear (manager.py pre-fix) mutates a
DIFFERENT session's in-memory copy; the live session's stale copy (still
carrying the marker) is flushed back by its post_run (team_runner.py:291) ->
marker returns. Clearing on the live session makes its post_run flush the
already-cleared state.
"""

from __future__ import annotations

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


async def _seed_live_session(session_id: str, team_name: str, *, query: str = "minesweeper"):
    """Build a real live AgentTeamSession with marker in memory + blob."""
    live = create_agent_team_session(session_id=session_id, source_metadata_enabled=False)
    await live.pre_run()
    ctx = TeamRuntimeContext(db_config=_inmemory_db_config())
    merge_team_namespace(live, team_name, {"context": ctx.model_dump()})
    merge_pending_resume(live, team_name, {"query": query})
    await live.flush_checkpoint()  # blob has marker; live in-memory has marker
    return live


def _mock_entry(team_name: str, session_id: str, live) -> ActiveTeam:
    """ActiveTeam whose agent exposes the live session + no-op teardown.

    ``session_manager()`` returns an object whose ``team_session`` is the live
    session (the seam reset_session reads). ``spawned_handles`` empty ->
    ``_clear_inprocess_members_inflight`` is a no-op. ``stop_coordination`` is
    an async no-op so the live session stays bound for the post-reset flush
    simulation (in production, stop_coordination releases it, but by then the
    live clear already ran).
    """

    async def _noop_stop_coordination() -> None:
        pass

    agent = SimpleNamespace(
        session_manager=lambda: SimpleNamespace(team_session=live),
        spawn_manager=SimpleNamespace(spawned_handles={}),
        stop_coordination=_noop_stop_coordination,
    )
    return ActiveTeam(team_name=team_name, agent=agent, current_session_id=session_id)


async def _read_resume(session_id: str, team_name: str):
    s = create_agent_team_session(session_id=session_id, source_metadata_enabled=False)
    await s.pre_run()
    return read_pending_resume(s, team_name)


@pytest.mark.asyncio
@pytest.mark.level1
async def test_reset_clears_on_live_session_no_stomp_after_post_run(isolated_checkpointer):
    """RED: reset must clear on the LIVE session so a later post_run (full
    overwrite of the live in-memory) does NOT write the marker back. With the
    throwaway-RMW path (pre-fix), the live session's in-memory still carries the
    marker and its post_run re-introduces it."""
    session_id = "sess-stomp-1"
    team_name = "oc_team_stomp_1"
    token = set_session_id(session_id)
    try:
        live = await _seed_live_session(session_id, team_name, query="minesweeper")
        assert read_pending_resume(live, team_name) == {"query": "minesweeper"}

        manager = TeamRuntimeManager()
        manager._pool._teams[team_name] = _mock_entry(team_name, session_id, live)

        result = await manager.reset_session(
            team_name=team_name, session_id=session_id, force=True
        )
        assert result is True

        # Simulate the live session's authoritative save (post_run's commit =
        # flush_checkpoint; post_run = close_stream + commit, and the stomp is the
        # commit's full-overwrite save). flush_checkpoint exercises exactly that
        # without the unopened-stream close_stream risk.
        await live.flush_checkpoint()

        assert await _read_resume(session_id, team_name) is None
    finally:
        reset_session_id(token)


@pytest.mark.asyncio
@pytest.mark.level0
async def test_reset_production_path_post_run_before_reset(isolated_checkpointer):
    """Production path (jiuwenswarm quiesces -> post_run runs before reset):
    live session already flushed (marker persisted), reset clears on live +
    flush, no later post_run. Behavior unchanged."""
    session_id = "sess-stomp-prod-1"
    team_name = "oc_team_stomp_prod"
    token = set_session_id(session_id)
    try:
        live = await _seed_live_session(session_id, team_name, query="minesweeper")
        await live.post_run()  # simulate jiuwenswarm _cleanup_runtime_locals quiesce

        manager = TeamRuntimeManager()
        manager._pool._teams[team_name] = _mock_entry(team_name, session_id, live)

        result = await manager.reset_session(
            team_name=team_name, session_id=session_id, force=True
        )
        assert result is True
        assert await _read_resume(session_id, team_name) is None
    finally:
        reset_session_id(token)


@pytest.mark.asyncio
@pytest.mark.level1
async def test_reset_live_clear_failure_returns_false(isolated_checkpointer, monkeypatch):
    """clear_pending_resume raising on the live session -> reset_failed ->
    return False (unified with the C2 task-board/marker failure semantics).
    Caller retries; the live in-memory clear did not succeed so the stomp
    prevention failed, which must surface rather than be masked as success."""
    session_id = "sess-stomp-fail-1"
    team_name = "oc_team_stomp_fail"
    token = set_session_id(session_id)
    try:
        live = await _seed_live_session(session_id, team_name, query="minesweeper")

        def _boom_clear(*args, **kwargs):  # noqa: ARG001
            raise RuntimeError("clear_pending_resume boom")

        # manager.py imports clear_pending_resume locally at call time, so
        # patching the module attr is picked up by both the live-clear and the
        # throwaway clear -- both surface as reset_failed -> False.
        monkeypatch.setattr(
            "openjiuwen.agent_teams.runtime.metadata.clear_pending_resume",
            _boom_clear,
        )

        manager = TeamRuntimeManager()
        manager._pool._teams[team_name] = _mock_entry(team_name, session_id, live)

        result = await manager.reset_session(
            team_name=team_name, session_id=session_id, force=True
        )
        assert result is False
    finally:
        reset_session_id(token)


@pytest.mark.asyncio
@pytest.mark.level1
async def test_reset_force_false_active_clears_on_live_session(isolated_checkpointer):
    """force=False + active: the live-clear is decoupled from ``force`` so the
    stomp is closed even when the caller resets without force-stopping.
    stop_team is NOT called (entry stays pooled), but the live in-memory marker
    is cleared and the throwaway persists; a later post_run does not stomp."""
    session_id = "sess-stomp-noforce-1"
    team_name = "oc_team_stomp_noforce"
    token = set_session_id(session_id)
    try:
        live = await _seed_live_session(session_id, team_name, query="minesweeper")
        manager = TeamRuntimeManager()
        manager._pool._teams[team_name] = _mock_entry(team_name, session_id, live)

        result = await manager.reset_session(
            team_name=team_name, session_id=session_id, force=False
        )
        assert result is True
        # force=False did not stop_team -> entry still pooled
        assert team_name in manager._pool._teams

        # simulate the live session's authoritative save (post_run's commit)
        await live.flush_checkpoint()
        assert await _read_resume(session_id, team_name) is None
    finally:
        reset_session_id(token)


@pytest.mark.asyncio
@pytest.mark.level1
async def test_release_clears_on_live_session_no_stomp_after_post_run(isolated_checkpointer):
    """release_session(force=True) must clear on the LIVE session so a later
    post_run does NOT write the marker back (same stomp as reset_session)."""
    session_id = "sess-release-stomp-1"
    team_name = "oc_team_release_stomp"
    token = set_session_id(session_id)
    try:
        live = await _seed_live_session(session_id, team_name, query="minesweeper")
        manager = TeamRuntimeManager()
        manager._pool._teams[team_name] = _mock_entry(team_name, session_id, live)

        await manager.release_session(session_id=session_id, force=True)

        # Simulate the live session's authoritative save (post_run's commit).
        await live.flush_checkpoint()

        assert await _read_resume(session_id, team_name) is None
    finally:
        reset_session_id(token)


@pytest.mark.asyncio
@pytest.mark.level0
async def test_reset_live_session_idempotent(isolated_checkpointer):
    """Second reset on the same live session is a no-op (marker already gone,
    clear_pending_resume returns False, no flush)."""
    session_id = "sess-stomp-idem-1"
    team_name = "oc_team_stomp_idem"
    token = set_session_id(session_id)
    try:
        live = await _seed_live_session(session_id, team_name)
        manager = TeamRuntimeManager()
        manager._pool._teams[team_name] = _mock_entry(team_name, session_id, live)

        first = await manager.reset_session(
            team_name=team_name, session_id=session_id, force=True
        )
        # re-inject entry (stop_team removed it)
        manager._pool._teams[team_name] = _mock_entry(team_name, session_id, live)
        second = await manager.reset_session(
            team_name=team_name, session_id=session_id, force=True
        )
        assert first is True and second is True
        assert await _read_resume(session_id, team_name) is None
    finally:
        reset_session_id(token)
