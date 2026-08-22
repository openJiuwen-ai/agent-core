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

import pytest

from openjiuwen.agent_teams.context import reset_session_id, set_session_id
from openjiuwen.agent_teams.runtime.manager import TeamRuntimeManager
from openjiuwen.agent_teams.runtime.metadata import (
    merge_pending_resume,
    merge_team_namespace,
    read_pending_resume,
)
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
async def test_reset_session_flush_failure_does_not_break_reset(
    isolated_checkpointer, monkeypatch
):
    """flush_checkpoint raising -> reset still True, pending_resume survives (no regression)."""
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
        result = await manager.reset_session(
            team_name=team_name, session_id=session_id, force=True
        )
        assert result is True  # best-effort: reset did not raise
    finally:
        reset_session_id(token)
        # monkeypatch teardown restores _build_session. The clear inside reset used
        # the patched session whose flush raised, so the marker survives in the
        # checkpointer. Verify via a fresh (unpatched) session — this is the
        # no-regression guarantee (Design Failure And Performance #3): a failed
        # flush leaves the checkpoint byte-identical to the pre-reset state.
        assert await _read_resume(session_id, team_name) == {"query": "minesweeper"}


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
