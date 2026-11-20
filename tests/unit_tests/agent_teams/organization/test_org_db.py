# coding: utf-8

import asyncio
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import event, inspect
from sqlalchemy.engine import Connection, Engine, ExecutionContext
from sqlalchemy.exc import OperationalError

from openjiuwen.agent_teams.context import reset_session_id, set_session_id
from openjiuwen.agent_teams.organization.db import OrgDbContext, json_dumps, json_loads
from openjiuwen.agent_teams.organization.manager import TeamOrganizationManager
from openjiuwen.agent_teams.organization.schema import ORG_STATIC_TABLE_NAMES
from openjiuwen.agent_teams.tools.database import DatabaseConfig, DatabaseType, TeamDatabase
from openjiuwen.core.common.exception.errors import BaseError


@pytest.mark.asyncio
async def test_org_db_context_creates_static_tables():
    db = TeamDatabase(DatabaseConfig(db_type=DatabaseType.SQLITE, connection_string=":memory:"))
    await OrgDbContext(db).initialize()
    assert db.engine is not None
    async with db.engine.connect() as conn:
        table_names = await conn.run_sync(lambda sync_conn: set(inspect(sync_conn).get_table_names()))
        message_columns = await conn.run_sync(
            lambda sync_conn: {column["name"] for column in inspect(sync_conn).get_columns("org_leader_message")}
        )
        receipt_columns = await conn.run_sync(
            lambda sync_conn: {
                column["name"] for column in inspect(sync_conn).get_columns("org_leader_message_receipt")
            }
        )
        org_task_columns = await conn.run_sync(
            lambda sync_conn: {col["name"] for col in inspect(sync_conn).get_columns("org_task")}
        )
    assert set(ORG_STATIC_TABLE_NAMES).issubset(table_names)
    assert {"org_summary_team", "org_summary_execution"}.issubset(table_names)
    assert "read_at" not in message_columns
    assert "read_at" not in receipt_columns
    assert {
        "aggregation_json",
        "failure_code",
        "failure_reason",
        "failed_at",
    }.issubset(org_task_columns)
    await db.close()


def test_json_helpers_roundtrip_and_defaults():
    assert json_dumps(None) is None
    assert json_loads(None, []) == []
    assert json_loads("{bad", {"ok": False}) == {"ok": False}
    payload = {"a": 1, "b": "中文"}
    assert json_loads(json_dumps(payload), {}) == payload


@pytest.mark.asyncio
async def test_org_db_context_runs_static_ddl_once(monkeypatch):
    from openjiuwen.agent_teams.organization import db as org_db

    db = TeamDatabase(DatabaseConfig(db_type=DatabaseType.SQLITE, connection_string=":memory:"))
    calls: list[int] = []
    original = org_db.ensure_org_static_tables

    def tracked_ensure_org_static_tables(sync_conn) -> None:
        calls.append(1)
        original(sync_conn)

    monkeypatch.setattr(org_db, "ensure_org_static_tables", tracked_ensure_org_static_tables)
    context = OrgDbContext(db)
    await context.initialize()
    await context.initialize()
    assert len(calls) == 1
    await db.close()


@pytest.mark.asyncio
async def test_org_db_context_adds_missing_summary_execution_updated_at():
    from openjiuwen.agent_teams.organization import db as org_db

    db = TeamDatabase(DatabaseConfig(db_type=DatabaseType.SQLITE, connection_string=":memory:"))
    await db.initialize()
    assert db.engine is not None

    async with db.engine.begin() as conn:
        await conn.exec_driver_sql(
            """
            CREATE TABLE org_summary_execution (
                execution_id TEXT PRIMARY KEY,
                organization_id TEXT,
                root_task_id TEXT,
                summary_task_id TEXT,
                summary_team_id TEXT,
                status TEXT,
                created_at BIGINT
            )
            """
        )
        await conn.run_sync(org_db.ensure_org_static_tables)

    async with db.engine.connect() as conn:
        columns = await conn.run_sync(
            lambda sync_conn: {column["name"] for column in inspect(sync_conn).get_columns("org_summary_execution")}
        )
    assert "updated_at" in columns
    await db.close()


@pytest.mark.asyncio
async def test_team_organization_manager_shares_db_context():
    db = TeamDatabase(DatabaseConfig(db_type=DatabaseType.SQLITE, connection_string=":memory:"))
    manager = TeamOrganizationManager(organization_id="org-1", db=db)
    assert manager.task_pool.db_context is manager.message_service.db_context
    await manager.task_pool.initialize()
    await manager.message_service.initialize()
    assert manager.task_pool.db_context.sessions is manager.message_service.db_context.sessions
    await db.close()


@pytest.mark.asyncio
async def test_ensure_org_task_columns_normalizes_legacy_terminal_statuses():
    from openjiuwen.agent_teams.organization import db as org_db

    db = TeamDatabase(DatabaseConfig(db_type=DatabaseType.SQLITE, connection_string=":memory:"))
    await db.initialize()
    assert db.engine is not None

    def create_legacy_org_task(sync_conn) -> None:
        sync_conn.exec_driver_sql(
            """
            CREATE TABLE org_task (
                task_id TEXT PRIMARY KEY,
                organization_id TEXT,
                parent_task_id TEXT,
                root_task_id TEXT,
                creator_type TEXT,
                creator_id TEXT,
                creator_team_id TEXT,
                status TEXT,
                created_at INTEGER,
                updated_at INTEGER,
                title TEXT,
                description TEXT,
                task_type TEXT,
                required_capabilities_json TEXT,
                assignment_type TEXT,
                assigned_team_id TEXT,
                assigned_leader_id TEXT,
                assigned_by_team_id TEXT,
                assigned_at INTEGER,
                output_spec_json TEXT,
                output_context_json TEXT,
                output_abstract TEXT,
                metadata_json TEXT
            )
            """
        )
        sync_conn.exec_driver_sql(
            """
            INSERT INTO org_task (
                task_id, organization_id, parent_task_id, root_task_id,
                creator_type, creator_id, creator_team_id, status,
                created_at, updated_at, title, description, task_type,
                required_capabilities_json, assignment_type,
                assigned_team_id, assigned_leader_id, assigned_by_team_id,
                assigned_at, output_spec_json, output_context_json,
                output_abstract, metadata_json
            ) VALUES
            ('t-cancelled', 'org-1', NULL, 't-cancelled', 'client', 'c1', NULL,
             'CANCELLED', 1, 10, 'cancelled', 'd', NULL, '[]', 'unassigned',
             NULL, NULL, NULL, NULL, NULL, NULL, NULL, '{}'),
            ('t-expired', 'org-1', NULL, 't-expired', 'client', 'c1', NULL,
             'EXPIRED', 2, 20, 'expired', 'd', NULL, '[]', 'unassigned',
             NULL, NULL, NULL, NULL, NULL, NULL, NULL, '{}')
            """
        )

    async with db.engine.begin() as conn:
        await conn.run_sync(create_legacy_org_task)
        await conn.run_sync(org_db.ensure_org_static_tables)

    async with db.engine.connect() as conn:
        rows = await conn.exec_driver_sql(
            "SELECT task_id, status, failure_code, failed_at FROM org_task ORDER BY task_id"
        )
        data = {row[0]: (row[1], row[2], row[3]) for row in rows}

    assert data["t-cancelled"] == ("FAILED", "CANCELLED", 10)
    assert data["t-expired"] == ("FAILED", "EXPIRED", 20)
    await db.close()


def _record_schema_statements(db: TeamDatabase) -> list[tuple[str, bool]]:
    """Record every schema statement on the writer engine with the write-lock state.

    Hooks the SQL boundary (``before_cursor_execute``) instead of patching the
    module under test: each entry is ``(statement, write_lock_held)`` for DDL
    and schema introspection (``CREATE`` / ``ALTER`` / ``PRAGMA`` /
    ``sqlite_master``), which is what the org schema ensure issues. Callers run
    no concurrent writers, so a held lock can only belong to the DDL itself.
    """
    assert db.engine is not None and db.sessions is not None
    sessions = db.sessions
    recorded: list[tuple[str, bool]] = []

    def _on_execute(
        conn: Connection,
        cursor: Any,
        statement: str,
        parameters: Any,
        context: ExecutionContext | None,
        executemany: bool,
    ) -> None:
        upper = statement.lstrip().upper()
        if upper.startswith(("CREATE", "ALTER", "PRAGMA")) or "SQLITE_MASTER" in upper:
            recorded.append((statement, sessions.write_locked))

    event.listen(db.engine.sync_engine, "before_cursor_execute", _on_execute)
    return recorded


async def _table_names(db: TeamDatabase) -> set[str]:
    assert db.engine is not None
    async with db.engine.connect() as conn:
        return await conn.run_sync(lambda sync_conn: set(inspect(sync_conn).get_table_names()))


@pytest.mark.asyncio
async def test_org_db_context_reuses_team_database_sessions() -> None:
    db = TeamDatabase(DatabaseConfig(db_type=DatabaseType.SQLITE, connection_string=":memory:"))
    sessions = await OrgDbContext(db).initialize()
    # One provider (one write lock) for org + DAO writes; a second DbSessions
    # would race the DAO writes for the small write pool.
    assert sessions is db.sessions
    assert await OrgDbContext(db).initialize() is sessions
    await db.close()


@pytest.mark.asyncio
async def test_org_static_ddl_runs_under_team_database_write_lock(tmp_path: Path) -> None:
    db = TeamDatabase(DatabaseConfig(db_type=DatabaseType.SQLITE, connection_string=str(tmp_path / "team.db")))
    await db.initialize()
    recorded = _record_schema_statements(db)

    await OrgDbContext(db).initialize()

    org_creates = [lock_held for stmt, lock_held in recorded if "CREATE TABLE" in stmt.upper() and "org_" in stmt]
    assert org_creates, "org static tables should be created on a fresh database"
    # Every org DDL statement must run while the shared write lock is held,
    # i.e. serialised with DAO writes rather than on a bare engine.begin().
    assert all(org_creates), recorded
    await db.close()


@pytest.mark.asyncio
async def test_find_organization_ids_runs_static_ddl_once_per_database() -> None:
    from openjiuwen.agent_teams.organization.task_pool import OrgTaskManager

    db = TeamDatabase(DatabaseConfig(db_type=DatabaseType.SQLITE, connection_string=":memory:"))
    assert await OrgTaskManager.find_organization_ids_for_team(db, "team-a") == []
    recorded = _record_schema_statements(db)
    # Each lookup builds a fresh OrgDbContext (team.list polling path); once
    # the schema is ensured no further DDL / introspection hits the database.
    for _ in range(3):
        assert await OrgTaskManager.find_organization_ids_for_team(db, "team-a") == []
    assert recorded == []
    await db.close()


@pytest.mark.asyncio
async def test_org_static_ddl_runs_for_each_new_database() -> None:
    first = TeamDatabase(DatabaseConfig(db_type=DatabaseType.SQLITE, connection_string=":memory:"))
    await OrgDbContext(first).initialize()
    await first.close()
    # The "schema ready" mark belongs to one provider: a new database (a new,
    # empty :memory: store) must build the org tables again.
    second = TeamDatabase(DatabaseConfig(db_type=DatabaseType.SQLITE, connection_string=":memory:"))
    await second.initialize()
    recorded = _record_schema_statements(second)
    await OrgDbContext(second).initialize()
    assert any("CREATE TABLE" in stmt.upper() and "org_info" in stmt for stmt, _ in recorded)
    assert "org_info" in await _table_names(second)
    await second.close()


@pytest.mark.asyncio
async def test_org_context_does_not_reopen_closed_database() -> None:
    from openjiuwen.agent_teams.organization.task_pool import OrgTaskManager

    db = TeamDatabase(DatabaseConfig(db_type=DatabaseType.SQLITE, connection_string=":memory:"))
    bound = OrgDbContext(db)
    await bound.initialize()
    await db.close()
    # Neither a held org store nor a fresh lookup (team.list polling builds a
    # new context per call) may resurrect engines and the checkpointer.
    with pytest.raises(BaseError, match="closed"):
        await bound.initialize()
    with pytest.raises(BaseError, match="closed"):
        _ = bound.sessions
    with pytest.raises(BaseError, match="closed"):
        await OrgTaskManager.find_organization_ids_for_team(db, "team-a")
    assert db.engine is None
    assert db.sessions is None


@pytest.mark.asyncio
async def test_closed_team_database_refuses_reinitialize() -> None:
    db = TeamDatabase(DatabaseConfig(db_type=DatabaseType.SQLITE, connection_string=":memory:"))
    await db.initialize()
    assert db.sessions is not None
    await db.close()
    # A provider bound to the disposed engine must not leak out of close(),
    # and closing is terminal: build a new TeamDatabase to reuse the storage.
    assert db.sessions is None
    with pytest.raises(BaseError, match="closed"):
        await db.initialize()
    assert db.engine is None


@contextmanager
def _record_disposed_engines() -> Iterator[list[Engine]]:
    """Record every engine disposed while the block runs (SQL-boundary event)."""
    disposed: list[Engine] = []

    def _on_dispose(engine: Engine) -> None:
        disposed.append(engine)

    event.listen(Engine, "engine_disposed", _on_dispose)
    try:
        yield disposed
    finally:
        event.remove(Engine, "engine_disposed", _on_dispose)


@contextmanager
def _foreign_write_lock(db_path: Path) -> Iterator[None]:
    """Hold SQLite's write lock on ``db_path`` from a foreign connection (another process's writer)."""
    conn = sqlite3.connect(db_path, isolation_level=None)
    try:
        conn.execute("BEGIN EXCLUSIVE")
        yield
    finally:
        conn.rollback()
        conn.close()


@pytest.mark.asyncio
async def test_failed_static_ddl_disposes_the_engines_it_built(tmp_path: Path) -> None:
    db_path = tmp_path / "team.db"
    db = TeamDatabase(DatabaseConfig(db_type=DatabaseType.SQLITE, connection_string=str(db_path), db_timeout=0))
    with _foreign_write_lock(db_path), _record_disposed_engines() as disposed:
        with pytest.raises(OperationalError, match="locked"):
            await db.initialize()
    # The engines never reached TeamDatabase, so initialize_engine itself must
    # dispose both pools; a polling caller would otherwise leak a pair per try.
    assert len(set(map(id, disposed))) == 2
    assert db.sessions is None and db.engine is None
    # The failure is not a close: once the lock is gone a retry succeeds.
    await db.initialize()
    assert db.sessions is not None
    await db.close()


@pytest.mark.asyncio
async def test_cancelled_initialize_releases_engines_and_hides_sessions(tmp_path: Path) -> None:
    db_path = tmp_path / "team.db"
    config = DatabaseConfig(db_type=DatabaseType.SQLITE, connection_string=str(db_path), db_timeout=0)
    seed = TeamDatabase(config)
    await seed.initialize()
    await seed.close()

    db = TeamDatabase(config)
    token = set_session_id("cancelled_init_session")
    try:
        # Static tables exist (read-only checks pass the foreign lock); the
        # bind-time session-table DDL then backs off on the lock, which is
        # where the caller gives up and cancels.
        with _foreign_write_lock(db_path), _record_disposed_engines() as disposed:
            init_task = asyncio.create_task(db.initialize())
            while db.engine is None and not init_task.done():
                await asyncio.sleep(0.01)
            init_task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await init_task
    finally:
        reset_session_id(token)
    # A half-initialized database must neither hand out its provider (a retry
    # would leave two write locks alive) nor keep the pools it just built.
    assert len(set(map(id, disposed))) == 2
    assert db.sessions is None
    assert db.engine is None and db.read_engine is None
    await db.initialize()
    assert db.sessions is not None
    await db.close()


@pytest.mark.asyncio
async def test_close_waits_for_inflight_initialize_and_releases_it(tmp_path: Path) -> None:
    db = TeamDatabase(DatabaseConfig(db_type=DatabaseType.SQLITE, connection_string=str(tmp_path / "team.db")))
    init_task = asyncio.create_task(db.initialize())
    # Let initialize() take the init lock and park on its first DB round-trip.
    await asyncio.sleep(0)
    assert not init_task.done()
    await db.close()
    await init_task
    # The in-flight initialize must not publish engines after close() returned.
    assert db.closed
    assert db.engine is None and db.read_engine is None
    assert db.sessions is None
