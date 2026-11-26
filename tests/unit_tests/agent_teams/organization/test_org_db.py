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


@pytest.mark.asyncio
async def test_ensure_org_static_tables_is_idempotent_when_columns_already_exist() -> None:
    """Re-running ensure_org_static_tables on a fully-migrated DB is a no-op.

    Covers the cross-process race where two processes both see a column
    missing and both fire ALTER TABLE ADD COLUMN — the second one must not
    abort the outer transaction. Simulates the race by pre-adding every
    org_task / org_summary_execution / org_info column, then calling
    ensure_org_static_tables inside an open transaction and asserting the
    commit succeeds.
    """
    from openjiuwen.agent_teams.organization import db as org_db

    database = TeamDatabase(DatabaseConfig(db_type=DatabaseType.SQLITE, connection_string=":memory:"))
    await database.initialize()
    assert database.engine is not None

    # First call migrates to the latest schema.
    async with database.engine.begin() as conn:
        await conn.run_sync(org_db.ensure_org_static_tables)

    # Second call inside an explicit transaction: every column already exists,
    # so every ALTER would fail. The SAVEPOINT + DBAPIError catch in
    # _add_column_idempotent must swallow the "duplicate column" error and
    # leave the transaction committable.
    async with database.engine.begin() as conn:
        await conn.run_sync(org_db.ensure_org_static_tables)
        # If the failed ALTER poisoned the transaction, commit (implicit at
        # block exit) would raise InFailedSQLTransaction. Reaching here means
        # the savepoint correctly isolated the failure.

    # Sanity: the org tables and their columns are still present.
    async with database.engine.connect() as conn:
        org_task_columns = await conn.run_sync(
            lambda sync_conn: {c["name"] for c in inspect(sync_conn).get_columns("org_task")}
        )
        for name, _ in org_db._ORG_TASK_NEW_COLUMNS:
            assert name in org_task_columns
        org_info_columns = await conn.run_sync(
            lambda sync_conn: {c["name"] for c in inspect(sync_conn).get_columns("org_info")}
        )
        assert "unclaimed_task_policy_json" in org_info_columns

    await database.close()


@pytest.mark.asyncio
async def test_add_column_idempotent_swallows_duplicate_column() -> None:
    """_add_column_idempotent catches duplicate-column errors on all backends.

    Direct unit test for the helper: pre-create a table with the target
    column, then call _add_column_idempotent. It must not raise and must
    not poison the surrounding transaction.
    """
    from openjiuwen.agent_teams.organization import db as org_db

    database = TeamDatabase(DatabaseConfig(db_type=DatabaseType.SQLITE, connection_string=":memory:"))
    await database.initialize()
    assert database.engine is not None

    async with database.engine.begin() as conn:
        await conn.exec_driver_sql("CREATE TABLE idempotent_test (id INTEGER PRIMARY KEY)")
        await conn.exec_driver_sql("ALTER TABLE idempotent_test ADD COLUMN marker TEXT")
        # Column now exists; _add_column_idempotent must swallow the error.
        await conn.run_sync(
            lambda sync_conn: org_db._add_column_idempotent(
                sync_conn, "idempotent_test", "marker", "TEXT"
            )
        )
        # Surrounding transaction must still be committable (implicit commit
        # at block exit). A follow-up write proves the transaction is alive.
        await conn.exec_driver_sql("INSERT INTO idempotent_test (id, marker) VALUES (1, 'x')")

    async with database.engine.connect() as conn:
        rows = await conn.exec_driver_sql("SELECT marker FROM idempotent_test WHERE id = 1")
        assert rows.fetchone()[0] == "x"

    await database.close()


def test_add_column_idempotent_mysql_skips_savepoint_and_rechecks(monkeypatch):
    """On MySQL _add_column_idempotent does not wrap the ALTER in a
    SAVEPOINT — DDL implicitly commits *whether the DDL succeeds or
    fails*, so the savepoint is destroyed before ``begin_nested`` can
    roll back to it, and the savepoint's exit-time ROLLBACK would raise a
    second ``DBAPIError`` that masks the original "Duplicate column name"
    error.

    The helper instead runs the ALTER bare and, when a duplicate-column
    error is swallowed, follows it with an ``inspect`` re-check that
    confirms the column is actually present (the implicit commit makes
    the post-ALTER state the ground truth).

    This test drives the MySQL branch with a hand-rolled fake connection:
    the real implicit-commit behaviour cannot be reproduced on SQLite, so
    we verify the helper takes the right branch (no ``begin_nested``,
    ALTER issued, ``inspect`` re-check on swallow).
    """
    from unittest.mock import MagicMock, patch

    from sqlalchemy.exc import OperationalError  # OperationalError is a DBAPIError subclass

    from openjiuwen.agent_teams.organization import db as org_db

    fake_conn = MagicMock()
    fake_conn.dialect.name = "mysql"

    # The ALTER raises a duplicate-column error (the column was added by a
    # concurrent process). The helper must swallow this on the MySQL path.
    fake_conn.exec_driver_sql.side_effect = OperationalError(
        "ALTER TABLE t ADD COLUMN c TEXT",
        {},
        Exception("Duplicate column name 'c'"),
    )

    # begin_nested must NOT be called on the MySQL path; if it is, the
    # AssertionError fails the test loudly.
    fake_conn.begin_nested.side_effect = AssertionError("MySQL path must not call begin_nested")

    # inspect(sync_conn).get_columns(table) returns the column, confirming
    # a concurrent process added it; the re-check passes and the helper
    # returns without re-raising.
    fake_inspector = MagicMock()
    fake_inspector.get_columns.return_value = [{"name": "c"}]

    with patch.object(org_db, "inspect", return_value=fake_inspector):
        org_db._add_column_idempotent(fake_conn, "t", "c", "TEXT")

    fake_conn.begin_nested.assert_not_called()
    fake_conn.exec_driver_sql.assert_called_once()
    fake_inspector.get_columns.assert_called_once_with("t")


def test_add_column_idempotent_mysql_reraises_when_column_still_missing(monkeypatch):
    """On MySQL, if the duplicate-column error is swallowed but the
    ``inspect`` re-check finds the column still missing (an inconsistent
    state — the column should have been added by a concurrent process),
    the helper re-raises rather than silently leaving the column absent.
    """
    from unittest.mock import MagicMock, patch

    from sqlalchemy.exc import OperationalError

    from openjiuwen.agent_teams.organization import db as org_db

    fake_conn = MagicMock()
    fake_conn.dialect.name = "mysql"

    fake_conn.exec_driver_sql.side_effect = OperationalError(
        "ALTER TABLE t ADD COLUMN c TEXT",
        {},
        Exception("Duplicate column name 'c'"),
    )

    # inspect reports the column is NOT present — inconsistent with the
    # "Duplicate column name" error. The helper must surface this.
    fake_inspector = MagicMock()
    fake_inspector.get_columns.return_value = [{"name": "other"}]

    with patch.object(org_db, "inspect", return_value=fake_inspector):
        with pytest.raises(OperationalError):
            org_db._add_column_idempotent(fake_conn, "t", "c", "TEXT")

    fake_conn.begin_nested.assert_not_called()
    fake_inspector.get_columns.assert_called_once_with("t")


@pytest.mark.asyncio
async def test_run_ddl_idempotent_swallows_already_exists_on_savepoint_path() -> None:
    """_run_ddl_idempotent catches 'already exists' on SQLite/PG (savepoint path).

    Direct unit test for the helper: pre-create a table, then call
    _run_ddl_idempotent with a DDL callable that tries to CREATE TABLE the
    same name (bypassing checkfirst to force the race-failure path). The
    savepoint must isolate the failure so the outer transaction stays
    committable and a follow-up write succeeds.
    """
    from openjiuwen.agent_teams.organization import db as org_db

    database = TeamDatabase(DatabaseConfig(db_type=DatabaseType.SQLITE, connection_string=":memory:"))
    await database.initialize()
    assert database.engine is not None

    async with database.engine.begin() as conn:
        await conn.exec_driver_sql("CREATE TABLE ddl_idempotent_test (id INTEGER PRIMARY KEY)")
        # The table already exists; _run_ddl_idempotent must swallow the
        # "already exists" error via its SAVEPOINT + DBAPIError catch.
        await conn.run_sync(
            lambda sync_conn: org_db._run_ddl_idempotent(
                sync_conn,
                lambda: sync_conn.exec_driver_sql(
                    "CREATE TABLE ddl_idempotent_test (id INTEGER PRIMARY KEY)"
                ),
                lambda: True,
            )
        )
        # Surrounding transaction must still be committable. A follow-up write
        # proves the transaction is alive — if the failed CREATE had poisoned
        # it, this INSERT would raise InFailedSQLTransaction.
        await conn.exec_driver_sql("INSERT INTO ddl_idempotent_test (id) VALUES (1)")

    async with database.engine.connect() as conn:
        rows = await conn.exec_driver_sql("SELECT id FROM ddl_idempotent_test WHERE id = 1")
        assert rows.fetchone()[0] == 1

    await database.close()


def test_run_ddl_idempotent_mysql_skips_savepoint_and_rechecks():
    """On MySQL _run_ddl_idempotent does not wrap the DDL in a SAVEPOINT.

    DDL implicitly commits *whether the DDL succeeds or fails*, so the
    savepoint is destroyed before ``begin_nested`` can roll back to it. The
    helper instead runs the DDL bare and, when an ``already exists`` /
    ``duplicate`` error is swallowed, calls the ``verify`` callable to
    re-check that the DDL target actually exists.

    Drives the MySQL branch with a hand-rolled fake connection: the real
    implicit-commit behaviour cannot be reproduced on SQLite, so we verify
    the helper takes the right branch (no ``begin_nested``, DDL issued,
    ``verify`` called on swallow).
    """
    from unittest.mock import MagicMock, patch

    from openjiuwen.agent_teams.organization import db as org_db

    fake_conn = MagicMock()
    fake_conn.dialect.name = "mysql"

    fake_conn.exec_driver_sql.side_effect = OperationalError(
        "CREATE TABLE t (id INT)",
        {},
        Exception("Table 't' already exists"),
    )

    fake_conn.begin_nested.side_effect = AssertionError("MySQL path must not call begin_nested")

    # verify returns True — a concurrent process created the table, so the
    # helper returns without re-raising.
    verify = MagicMock(return_value=True)

    org_db._run_ddl_idempotent(
        fake_conn,
        lambda: fake_conn.exec_driver_sql("CREATE TABLE t (id INT)"),
        verify,
    )

    fake_conn.begin_nested.assert_not_called()
    fake_conn.exec_driver_sql.assert_called_once()
    verify.assert_called_once()


def test_run_ddl_idempotent_mysql_reraises_when_verify_fails():
    """On MySQL, if the 'already exists' error is swallowed but ``verify``
    returns False (an inconsistent state — the DDL target should exist),
    the helper re-raises rather than silently leaving it absent.
    """
    from unittest.mock import MagicMock, patch

    from openjiuwen.agent_teams.organization import db as org_db

    fake_conn = MagicMock()
    fake_conn.dialect.name = "mysql"

    fake_conn.exec_driver_sql.side_effect = OperationalError(
        "CREATE TABLE t (id INT)",
        {},
        Exception("Table 't' already exists"),
    )

    # verify returns False — the table is somehow still missing. The helper
    # must surface this inconsistency.
    verify = MagicMock(return_value=False)

    with pytest.raises(OperationalError):
        org_db._run_ddl_idempotent(
            fake_conn,
            lambda: fake_conn.exec_driver_sql("CREATE TABLE t (id INT)"),
            verify,
        )

    fake_conn.begin_nested.assert_not_called()
    verify.assert_called_once()
