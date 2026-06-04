# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Verify ``TeamMember.role`` survives leader cold-restart.

Before ``team_member.role`` was persisted, the leader's
``_human_agent_names`` cache was the only place that knew which
members were HITT humans. The cache was rebuilt from
``predefined_members`` at construction time, so **dynamically spawned**
human agents (created via ``spawn_human_agent`` after ``build_team``)
were silently lost across a leader-process restart — the next
``build_context_from_db`` call inferred ``TeamRole.TEAMMATE`` and the
restarted member came up with the wrong tool / rail / prompt profile.

These tests pin the new behaviour:

* ``spawn_member(role=HUMAN_AGENT)`` writes the role through to the DB
  row.
* ``is_human_agent`` / ``human_agent_names()`` query the DB directly,
  so a fresh ``TeamBackend`` instance sees dynamically-spawned humans
  immediately without any cache-refresh step.
* ``SpawnManager.build_context_from_db`` reads the role straight off
  the row, so it works even if the new backend has not touched the
  DB yet.
* The SQLite migration step backfills the ``role`` column on legacy
  DB files that pre-date this change.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio

from openjiuwen.agent_teams.agent.spawn_manager import SpawnManager
from openjiuwen.agent_teams.context import reset_session_id, set_session_id
from openjiuwen.agent_teams.messager import Messager
from openjiuwen.agent_teams.schema.blueprint import (
    DeepAgentSpec,
    TeamAgentSpec,
)
from openjiuwen.agent_teams.schema.team import (
    TeamMemberSpec,
    TeamRole,
)
from openjiuwen.agent_teams.tools.database import (
    DatabaseConfig,
    DatabaseType,
    TeamDatabase,
)
from openjiuwen.agent_teams.tools.database.engine import initialize_engine
from openjiuwen.agent_teams.tools.team import TeamBackend
from openjiuwen.core.single_agent import AgentCard

TEAM_NAME = "restore_team"
LEADER_NAME = "team_leader"


@pytest_asyncio.fixture
async def db():
    """Shared in-memory SQLite DB scoped to one test session.

    Does **not** pre-create the team row so individual tests can drive
    ``build_team`` themselves without colliding with the fixture.
    """
    token = set_session_id("restore_session")
    config = DatabaseConfig(db_type=DatabaseType.SQLITE, connection_string=":memory:")
    database = TeamDatabase(config)
    try:
        await database.initialize()
        yield database
    finally:
        await database.close()
        reset_session_id(token)


async def _seed_team(db: TeamDatabase) -> None:
    """Insert the ``TEAM_NAME`` row used by tests that bypass ``build_team``."""
    await db.team.create_team(
        team_name=TEAM_NAME,
        display_name="Restore",
        leader_member_name=LEADER_NAME,
    )


@pytest_asyncio.fixture
async def messager():
    yield AsyncMock(spec=Messager)


def _make_backend(db, messager) -> TeamBackend:
    """Construct a fresh ``TeamBackend`` against the shared DB.

    No ``predefined_members`` — the test cases exercise the dynamic
    spawn path, which is the one the legacy cache lost across restart.
    """
    return TeamBackend(
        team_name=TEAM_NAME,
        member_name=LEADER_NAME,
        is_leader=True,
        db=db,
        messager=messager,
        enable_hitt=True,
    )


def _human_agent_spawn_team_spec() -> TeamAgentSpec:
    """Build a leader spec that opens HITT but declares zero humans.

    Forces every human in the test to land via dynamic spawn instead
    of through the ``build_team`` predefined fan-out.
    """
    return TeamAgentSpec(
        agents={"leader": DeepAgentSpec()},
        team_name=TEAM_NAME,
        enable_hitt=True,
    )


# ---------------------------------------------------------------------------
# spawn_member(role=...) persists the role to the row
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.level0
async def test_spawn_member_persists_human_agent_role(db, messager):
    """``spawn_member(role=HUMAN_AGENT)`` writes ``human_agent`` to the
    ``team_member.role`` column."""
    await _seed_team(db)
    backend = _make_backend(db, messager)

    result = await backend.spawn_member(
        member_name="alice",
        display_name="Alice",
        agent_card=AgentCard(),
        desc="user avatar",
        role=TeamRole.HUMAN_AGENT,
    )
    assert result.ok

    row = await db.member.get_member("alice", TEAM_NAME)
    assert row is not None
    assert row.role == TeamRole.HUMAN_AGENT.value


@pytest.mark.asyncio
@pytest.mark.level0
async def test_spawn_member_default_role_is_teammate(db, messager):
    """The default ``role`` argument materializes ``teammate`` rows."""
    await _seed_team(db)
    backend = _make_backend(db, messager)

    result = await backend.spawn_member(
        member_name="dev-1",
        display_name="Dev 1",
        agent_card=AgentCard(),
        desc="backend dev",
    )
    assert result.ok

    row = await db.member.get_member("dev-1", TEAM_NAME)
    assert row is not None
    assert row.role == TeamRole.TEAMMATE.value


# ---------------------------------------------------------------------------
# is_human_agent / human_agent_names() query DB directly (no cache)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.level0
async def test_dynamic_human_agent_survives_backend_restart(db, messager):
    """A human spawned dynamically on backend #1 is still recognised
    after backend #1 is discarded and backend #2 reads the DB.

    Since ``is_human_agent`` queries the DB on every call, a fresh
    ``TeamBackend`` sees dynamically-spawned humans immediately —
    no cache-refresh step is needed.
    """
    await _seed_team(db)
    backend1 = _make_backend(db, messager)

    spawn_result = await backend1.spawn_human_agent(
        member_name="alice",
        display_name="Alice",
        desc="user avatar",
    )
    assert spawn_result.ok
    assert await backend1.is_human_agent("alice") is True

    # Simulate leader process restart: drop backend1, build a new
    # backend bound to the same DB.
    del backend1
    backend2 = _make_backend(db, messager)

    # backend2 sees alice as human_agent immediately from DB.
    assert await backend2.is_human_agent("alice") is True
    assert "alice" in await backend2.human_agent_names()


@pytest.mark.asyncio
@pytest.mark.level0
async def test_predefined_human_agent_survives_backend_restart(db, messager):
    """Predefined humans persisted via ``build_team`` are also recovered."""
    predefined_backend = TeamBackend(
        team_name=TEAM_NAME,
        member_name=LEADER_NAME,
        is_leader=True,
        db=db,
        messager=messager,
        predefined_members=[
            TeamMemberSpec(
                member_name="alice",
                display_name="Alice",
                role_type=TeamRole.HUMAN_AGENT,
                persona="Visual designer",
            ),
        ],
        enable_hitt=True,
    )
    # ``build_team`` is the path that walks predefined_members → spawn_human_agent.
    await predefined_backend.build_team(
        display_name="Restore",
        desc="t",
        leader_display_name="Leader",
        leader_desc="p",
    )
    assert await predefined_backend.is_human_agent("alice") is True

    del predefined_backend
    backend2 = _make_backend(db, messager)

    assert await backend2.is_human_agent("alice") is True


# ---------------------------------------------------------------------------
# spawn_manager.build_context_from_db round-trip
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.level0
async def test_build_context_reads_role_from_member_row(db, messager):
    """``SpawnManager.build_context_from_db`` returns ``HUMAN_AGENT``
    for a row that was spawned with ``role=HUMAN_AGENT``, independent
    of any in-memory cache state.
    """
    await _seed_team(db)
    backend = _make_backend(db, messager)
    await backend.spawn_member(
        member_name="alice",
        display_name="Alice",
        agent_card=AgentCard(),
        desc="user avatar",
        role=TeamRole.HUMAN_AGENT,
    )

    # Simulate a fresh leader process: build a brand-new backend, then
    # prove ``build_context_from_db`` still classifies the role
    # correctly via the persisted row alone — is_human_agent queries
    # DB directly so the answer is always current.
    leader = _human_agent_spawn_team_spec().build()
    fresh_backend = _make_backend(db, messager)
    leader._configurator._infra.team_backend = fresh_backend
    assert await fresh_backend.is_human_agent("alice") is True

    spawn_manager = SpawnManager(
        state=leader._state,
        configurator=leader._configurator,
        team_agent_getter=lambda: leader,
    )
    ctx = await spawn_manager.build_context_from_db("alice")

    assert ctx is not None
    assert ctx.role == TeamRole.HUMAN_AGENT


@pytest.mark.asyncio
@pytest.mark.level0
async def test_build_context_returns_teammate_for_ordinary_member(db, messager):
    """Plain teammates keep ``TeamRole.TEAMMATE`` after the restart roundtrip."""
    await _seed_team(db)
    backend = _make_backend(db, messager)
    await backend.spawn_member(
        member_name="dev-1",
        display_name="Dev 1",
        agent_card=AgentCard(),
        desc="backend dev",
    )

    leader = _human_agent_spawn_team_spec().build()
    fresh_backend = _make_backend(db, messager)
    leader._configurator._infra.team_backend = fresh_backend
    spawn_manager = SpawnManager(
        state=leader._state,
        configurator=leader._configurator,
        team_agent_getter=lambda: leader,
    )

    ctx = await spawn_manager.build_context_from_db("dev-1")
    assert ctx is not None
    assert ctx.role == TeamRole.TEAMMATE


# ---------------------------------------------------------------------------
# SQLite legacy-DB migration
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.level0
async def test_legacy_team_member_table_gets_role_column(tmp_path: Path):
    """A legacy SQLite file that pre-dates the ``role`` column is
    transparently migrated when the engine boots."""
    db_path = tmp_path / "legacy.db"

    # Build a "legacy" team_member table by hand — note the absence of
    # the ``role`` column.
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            """
            CREATE TABLE team_member (
                member_name TEXT NOT NULL,
                team_name TEXT NOT NULL,
                display_name TEXT NOT NULL,
                agent_card TEXT NOT NULL,
                status TEXT NOT NULL,
                mode TEXT NOT NULL,
                PRIMARY KEY (member_name, team_name)
            )
            """
        )
        conn.execute(
            """
            INSERT INTO team_member (
                member_name, team_name, display_name, agent_card, status, mode
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            ("legacy_member", TEAM_NAME, "Legacy", "{}", "READY", "build_mode"),
        )
        conn.commit()
    finally:
        conn.close()

    config = DatabaseConfig(db_type=DatabaseType.SQLITE, connection_string=str(db_path))
    engine, _ = await initialize_engine(config)
    try:
        # Probe the migrated schema directly through the raw driver
        # rather than spinning up TeamDatabase — keeps the assertion
        # tied to the migration step.
        conn = sqlite3.connect(str(db_path))
        try:
            columns = {row[1] for row in conn.execute("PRAGMA table_info(team_member)")}
            assert "role" in columns
            cursor = conn.execute(
                "SELECT role FROM team_member WHERE member_name = ?",
                ("legacy_member",),
            )
            assert cursor.fetchone() == (TeamRole.TEAMMATE.value,)
        finally:
            conn.close()
    finally:
        await engine.dispose()