# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""DatabaseResolver and BackendTeamReadView over a real in-memory database."""

from __future__ import annotations

import pytest
import pytest_asyncio

from openjiuwen.agent_teams.context import reset_session_id, set_session_id
from openjiuwen.agent_teams.extensions.ops import TaskDependencyInfo
from openjiuwen.agent_teams.extensions.resolver import (
    BackendTeamReadView,
    DatabaseResolver,
)
from openjiuwen.agent_teams.tools.database import TeamDatabase
from openjiuwen.agent_teams.tools.database.config import DatabaseConfig

SESSION_ID = "resolver-session"


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


def _resolver(database: TeamDatabase) -> DatabaseResolver:
    """Build a resolver whose factory hands out the fixture's database."""

    async def factory() -> TeamDatabase:
        return database

    return DatabaseResolver(factory)


@pytest.mark.asyncio
async def test_resolver_answers_from_the_database(db) -> None:
    """Teams with rows resolve from the database, not from the runtime pool."""
    await _seed(db)

    resolver = _resolver(db)
    view = await resolver.resolve(SESSION_ID, "research-team")
    assert isinstance(view, BackendTeamReadView)

    # Stopped teams stay queryable: no runtime pool participates in
    # resolution, and the session id selects dynamic tables, not access.
    assert await resolver.resolve("other-session", "research-team") is not None
    # Unknown teams and missing sessions still resolve to None.
    assert await resolver.resolve(SESSION_ID, "other-team") is None
    assert await resolver.resolve(None, "research-team") is None


@pytest.mark.asyncio
async def test_resolver_with_no_database_resolves_to_none() -> None:
    """A host without team storage configured resolves every team to None."""

    async def factory() -> TeamDatabase | None:
        return None

    resolver = DatabaseResolver(factory)
    assert await resolver.resolve(SESSION_ID, "research-team") is None


@pytest.mark.asyncio
async def test_view_queries_without_a_bound_session_contextvar(db) -> None:
    """The view binds its session around every query itself."""
    await _seed(db)
    view = BackendTeamReadView(
        db,
        "research-team",
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
async def test_view_isolates_per_session_dynamic_tables(db) -> None:
    """Task and message rows live in per-session tables; one view sees one set."""
    await _seed(db)
    # The same team under another session carries its own task tables;
    # create_cur_session_tables is the idempotent per-session DDL path
    # (initialize only creates the first bound session's tables).
    token = set_session_id("other-session")
    try:
        await db.create_cur_session_tables()
        await db.task.create_task("t9", "research-team", "t9", "other work", "pending")
    finally:
        reset_session_id(token)

    view = BackendTeamReadView(
        db,
        "research-team",
        session_id=SESSION_ID,
    )
    assert {t.task_id for t in await view.tasks()} == {"t1", "t2"}

    other_view = BackendTeamReadView(
        db,
        "research-team",
        session_id="other-session",
    )
    assert {t.task_id for t in await other_view.tasks()} == {"t9"}


@pytest.mark.asyncio
async def test_resolver_reads_empty_for_sessions_without_tables(db) -> None:
    """A session that never ran reads empty, and the read path never creates tables."""
    await _seed(db)

    # A session that never ran has no dynamic tables yet...
    token = set_session_id("fresh-session")
    try:
        assert not await db.has_cur_session_tables("task")
        assert not await db.has_cur_session_tables("message")
    finally:
        reset_session_id(token)

    # ...a known team under it still resolves, the queries answer empty
    # data instead of raising, and they still create no tables.
    resolver = _resolver(db)
    view = await resolver.resolve("fresh-session", "research-team")
    assert view is not None
    assert await view.tasks() == []
    assert await view.task("t1") is None
    assert await view.task_dependencies("t1") == []
    assert await view.messages() == []
    assert await view.message("m1") is None

    token = set_session_id("fresh-session")
    try:
        assert not await db.has_cur_session_tables("task")
        assert not await db.has_cur_session_tables("message")
    finally:
        reset_session_id(token)


@pytest.mark.asyncio
async def test_view_owns_no_team_rows(db) -> None:
    """A team with no rows still answers queries without raising."""
    view = BackendTeamReadView(
        db,
        "ghost-team",
        session_id=SESSION_ID,
    )
    assert await view.team_info() is None
    assert await view.members() == []
    assert await view.tasks() == []
    assert await view.messages() == []
