# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""RuntimePoolResolver and BackendTeamReadView over a real in-memory database."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest
import pytest_asyncio

from openjiuwen.agent_teams.context import reset_session_id, set_session_id
from openjiuwen.agent_teams.extensions.ops import TaskDependencyInfo
from openjiuwen.agent_teams.extensions.resolver import (
    BackendTeamReadView,
    RuntimePoolResolver,
)
from openjiuwen.agent_teams.runtime.pool import ActiveTeam, TeamRuntimePool
from openjiuwen.agent_teams.tools.database import TeamDatabase
from openjiuwen.agent_teams.tools.database.config import DatabaseConfig

SESSION_ID = "resolver-session"


@dataclass
class _StubBackend:
    """Structural stand-in for TeamBackend: only db and team_name are read."""

    db: TeamDatabase
    team_name: str


@dataclass
class _StubAgent:
    """Structural stand-in for the leader agent of one pool entry."""

    team_backend: Any


@pytest_asyncio.fixture
async def db():
    """Provide an initialized in-memory TeamDatabase with a session bound."""
    token = set_session_id(SESSION_ID)
    database = TeamDatabase(DatabaseConfig(connection_string=":memory:"))
    try:
        await database.initialize()
        yield database
    finally:
        await database.close()
        reset_session_id(token)


@dataclass
class _PoolWorld:
    """A real pool seeded with one active entry over the stub backend."""

    pool: TeamRuntimePool = field(default_factory=TeamRuntimePool)

    async def add_entry(self, backend: _StubBackend, *, session_id: str) -> None:
        entry = ActiveTeam(
            team_name=backend.team_name,
            agent=_StubAgent(team_backend=backend),  # type: ignore[arg-type]
            current_session_id=session_id,
        )
        await self.pool.add(entry)


async def _seed(database: TeamDatabase) -> None:
    """Create one team with a member, two tasks, a dependency and messages."""
    await database.team.create_team("research-team", "Research", "leader")
    await database.member.create_member(
        "researcher",
        "research-team",
        "Researcher",
        "card-id",
        "ready",
    )
    await database.task.create_task("t1", "research-team", "t1", "work", "pending")
    await database.task.create_task("t2", "research-team", "t2", "upstream", "in_progress")
    mutation = await database.task.mutate_dependency_graph(
        team_name="research-team",
        add_edges=[("t1", "t2")],
    )
    assert mutation.ok, mutation.reason
    # Completing the depended-on task marks the dependency resolved.
    assert await database.task.update_task_status("t2", "completed")
    await database.message.create_message("m1", "research-team", "leader", "hello")
    await database.message.create_message(
        "m2",
        "research-team",
        "leader",
        "broadcast body",
        broadcast=True,
    )


@pytest.mark.asyncio
async def test_resolver_wraps_the_sessions_active_entry(db) -> None:
    """The session's entry resolves; other sessions or teams do not."""
    await _seed(db)
    backend = _StubBackend(db=db, team_name="research-team")
    world = _PoolWorld()
    await world.add_entry(backend, session_id=SESSION_ID)

    resolver = RuntimePoolResolver(world.pool)
    view = await resolver.resolve(SESSION_ID, "research-team")
    assert isinstance(view, BackendTeamReadView)

    assert await resolver.resolve("other-session", "research-team") is None
    assert await resolver.resolve(SESSION_ID, "other-team") is None
    assert await resolver.resolve(None, "research-team") is None


@pytest.mark.asyncio
async def test_view_queries_without_a_bound_session_contextvar(db) -> None:
    """The view binds its session around every query itself."""
    await _seed(db)
    view = BackendTeamReadView(
        _StubBackend(db=db, team_name="research-team"),
        session_id=SESSION_ID,
    )
    # Drop the contextvar to prove the view rebinds it per query.
    token = set_session_id("unrelated-session")
    try:
        info = await view.team_info()
        assert info is not None and info.team_name == "research-team"

        roster = await view.members()
        assert [m.member_name for m in roster] == ["researcher"]
        assert await view.member("researcher") is not None
        assert await view.member("ghost") is None

        tasks = await view.tasks()
        assert {t.task_id for t in tasks} == {"t1", "t2"}
        assert await view.task("t1") is not None
        assert await view.task("missing") is None

        dependencies = await view.task_dependencies("t1")
        assert dependencies == [
            TaskDependencyInfo(task_id="t1", depends_on_task_id="t2", resolved=True),
        ]

        messages = {m.message_id: m for m in await view.messages()}
        assert set(messages) == {"m1", "m2"}
        assert messages["m2"].broadcast is True
        assert await view.message("m1") is not None
        assert await view.message("missing") is None
    finally:
        reset_session_id(token)


@pytest.mark.asyncio
async def test_view_owns_no_team_rows(db) -> None:
    """A team with no rows still answers queries without raising."""
    view = BackendTeamReadView(
        _StubBackend(db=db, team_name="ghost-team"),
        session_id=SESSION_ID,
    )
    assert await view.team_info() is None
    assert await view.members() == []
    assert await view.tasks() == []
    assert await view.messages() == []
