# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Database engine initialization, session management, and table lifecycle."""

import asyncio
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import NamedTuple, TypeVar

from sqlalchemy import event, inspect
from sqlalchemy.exc import DBAPIError, NoSuchTableError, OperationalError
from sqlalchemy.exc import TimeoutError as SATimeoutError
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import AsyncAdaptedQueuePool, StaticPool
from sqlmodel import SQLModel

from openjiuwen.agent_teams.context import get_session_id
from openjiuwen.agent_teams.tools.database.config import DatabaseConfig, DatabaseType
from openjiuwen.agent_teams.tools.models import (
    TEAM_DYNAMIC_TABLE_PREFIXES,
    TEAM_STATIC_TABLES_TO_CLEAR,
    _get_message_model,
    _get_message_read_status_model,
    _get_review_vote_model,
    _get_task_dependency_model,
    _get_task_model,
    _sanitize_session_id_for_table,
    static_tables,
)
from openjiuwen.core.common.logging import team_logger


def get_current_time() -> int:
    """Return current time in milliseconds."""
    return int(round(time.time() * 1000))


def _db_type_is_client_server(db_type: "DatabaseType | str | None") -> bool:
    """Return whether ``db_type`` is a client/server backend (PostgreSQL / MySQL).

    ``None`` defaults to SQLite (legacy callers that don't pass ``db_type``),
    so the process-level write lock and the SQLite watchdog still apply.
    """
    if db_type is None:
        return False
    if isinstance(db_type, DatabaseType):
        return db_type.is_client_server
    try:
        return DatabaseType(str(db_type)).is_client_server
    except ValueError:
        return False


_T = TypeVar("_T")

# Retry budget for write operations that hit a transient ``database is
# locked``. With application-level write serialisation in place (see
# ``DbSessions``) this is rare — it only surfaces from WAL checkpoint
# edges or a foreign process touching the same file — so a short bounded
# back-off is enough.
_DB_RETRY_ATTEMPTS = 3
_DB_RETRY_BASE_DELAY = 0.5

# Watchdog for one DbSessions block (connection checkout + every statement
# inside the ``async with``). The pool guards checkout with its own
# ``pool_timeout``, but a statement submitted to a wedged driver thread
# awaits its result with no timeout at all — a hang there is invisible and
# permanent. Team DB transactions are small (single-row reads / batched
# mailbox flushes), so 30s is far beyond any legitimate block; hitting it
# means the driver is stuck, and a loud TimeoutError beats an eternal hang.
#
# For client/server backends (PostgreSQL / MySQL) the watchdog is widened to
# 60s: cross-network round-trips + transient slow queries make 30s a tight
# ceiling under load, and the process-level write lock is skipped (the DB
# itself is the arbiter), so watchdog contention cannot pile up the way
# queued writers do on SQLite.
_DB_SESSION_WATCHDOG_SECONDS_SQLITE = 30.0
_DB_SESSION_WATCHDOG_SECONDS_CLIENT_SERVER = 60.0

# Error types ``retry_on_transient`` may back off on: SQLite contention
# (``OperationalError``), write-pool checkout timeouts (``SATimeoutError``)
# and the ``DbSessions`` watchdog (builtin ``TimeoutError``). Callers that
# degrade on failure catch this same tuple. Only contention-flavoured
# ``OperationalError``s are retried (see ``_is_transient_db_error``).
_TRANSIENT_DB_ERRORS = (OperationalError, SATimeoutError, TimeoutError)
# SQLite messages that mark an ``OperationalError`` as contention
# ("database is locked", "database table is locked", SQLITE_BUSY wording);
# anything else (disk I/O error, malformed image, no such table) is permanent.
_TRANSIENT_OPERATIONAL_MARKERS = ("locked", "busy")
# Each watchdog hit already cost ``_DB_SESSION_WATCHDOG_SECONDS``, and a
# wedged driver rarely recovers within seconds, so retry it only once instead
# of the full ``_DB_RETRY_ATTEMPTS`` (bounds the worst case at ~2x watchdog).
_WATCHDOG_RETRY_ATTEMPTS = 2


def _add_column_idempotent(sync_conn, table: str, column: str, col_type: str) -> None:
    """Add a column, tolerating a concurrent add by another process.

    The check-then-ALTER pattern is not atomic across processes: two
    processes can both see the column missing and both issue
    ``ALTER TABLE ... ADD COLUMN``. The DB's own DDL lock serialises the
    two ALTERs, but the second one fails:

    - SQLite: ``OperationalError`` "duplicate column name".
    - PostgreSQL: ``ProgrammingError`` (SQLSTATE 42701) "column ... already
      exists". PG also puts the transaction into an aborted state after the
      failed statement, so the outer ``session.commit()`` would later raise
      ``InFailedSQLTransaction``.
    - MySQL: ``OperationalError`` (1060) "Duplicate column name".

    On SQLite / PostgreSQL the ALTER is wrapped in a SAVEPOINT
    (``begin_nested``) so the outer transaction stays committable when the
    duplicate-column error is swallowed. On MySQL the SAVEPOINT is skipped
    (DDL implicitly commits, destroying any active savepoint).

    On **both** paths the column's existence is re-checked via ``inspect``
    after a swallowed duplicate-column error; if the column is somehow still
    missing, the helper re-raises rather than silently leaving the column
    absent.
    """
    use_savepoint = sync_conn.dialect.name != "mysql"
    try:
        if use_savepoint:
            with sync_conn.begin_nested():
                sync_conn.exec_driver_sql(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}")
        else:
            sync_conn.exec_driver_sql(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}")
    except DBAPIError as exc:
        msg = str(exc).lower()
        if "duplicate column" not in msg and "already exists" not in msg:
            raise
        inspector = inspect(sync_conn)
        if column not in {col["name"] for col in inspector.get_columns(table)}:
            raise


def _drop_column_idempotent(sync_conn, table: str, column: str) -> None:
    """Drop a column, tolerating a concurrent drop by another process.

    Mirror of :func:`_add_column_idempotent` for the DROP path: the
    check-then-ALTER pattern is not atomic across processes, so two
    processes can both see the column present and both issue
    ``ALTER TABLE ... DROP COLUMN``. The DB's own DDL lock serialises the
    two ALTERs, but the second one fails:

    - SQLite: ``OperationalError`` "no such column: ...".
    - PostgreSQL: ``ProgrammingError`` (SQLSTATE 42703) "column ... does
      not exist". PG also puts the transaction into an aborted state after
      the failed statement, so the outer ``session.commit()`` would later
      raise ``InFailedSQLTransaction``.
    - MySQL: ``OperationalError`` (1091) "Can't DROP ...; check that
      column/key exists".

    On SQLite / PostgreSQL the ALTER is wrapped in a SAVEPOINT
    (``begin_nested``) so the outer transaction stays committable when the
    not-found error is swallowed. On MySQL the SAVEPOINT is skipped (DDL
    implicitly commits, destroying any active savepoint).

    On **both** paths the column's absence is re-checked via ``inspect``
    after a swallowed not-found error; if the column is somehow still
    present, the helper re-raises rather than silently leaving the column
    in place. The error-message match covers the per-backend wording above;
    any DDL error whose message does not look like a not-found is always
    re-raised.
    """
    use_savepoint = sync_conn.dialect.name != "mysql"
    try:
        if use_savepoint:
            with sync_conn.begin_nested():
                sync_conn.exec_driver_sql(f"ALTER TABLE {table} DROP COLUMN {column}")
        else:
            sync_conn.exec_driver_sql(f"ALTER TABLE {table} DROP COLUMN {column}")
    except DBAPIError as exc:
        msg = str(exc).lower()
        if not any(
            marker in msg
            for marker in ("no such column", "does not exist", "can't drop", "cannot drop")
        ):
            raise
        inspector = inspect(sync_conn)
        if column in {col["name"] for col in inspector.get_columns(table)}:
            raise


def _run_ddl_idempotent(
    sync_conn,
    ddl: Callable[[], None],
    verify: Callable[[], bool],
) -> None:
    """Run a DDL callable, tolerating a concurrent run by another process.

    Generalises :func:`_add_column_idempotent` to arbitrary DDL callables
    (``SQLModel.metadata.create_all`` / ``Table.create`` / ``Index.create``).
    The same check-then-act TOCTOU race applies: two processes both inspect
    a table or index as missing and both fire the ``CREATE`` statement; the
    DB's own DDL lock serialises the two ``CREATE``s but the second fails
    with ``already exists``.

    On SQLite / PostgreSQL the DDL is wrapped in a ``SAVEPOINT``
    (``begin_nested``) so the outer transaction stays committable when the
    ``already exists`` error is swallowed; on MySQL the ``SAVEPOINT`` is
    skipped (DDL implicitly commits).

    On **both** paths the ``verify`` callable is invoked after a swallowed
    error to re-check the DDL target actually exists. If verify reports the
    target still missing, the helper re-raises rather than silently leaving
    the schema absent.
    """
    use_savepoint = sync_conn.dialect.name != "mysql"
    try:
        if use_savepoint:
            with sync_conn.begin_nested():
                ddl()
        else:
            ddl()
    except DBAPIError as exc:
        msg = str(exc).lower()
        if "already exists" not in msg and "duplicate" not in msg:
            raise
        if not verify():
            raise


class DbSessions:
    """Read/write session provider.

    For SQLite (file or ``:memory:``) a process-wide ``asyncio.Lock`` funnels
    every write through one logical writer slot — SQLite permits a single
    writer at a time (a database-level lock), so writers never contend on
    the SQLite lock. For file-backed SQLite the reads and writes run on
    *separate* engines (a dedicated single-writer pool + a larger reader
    pool), so a slow write never starves reader-connection availability. For
    ``:memory:`` the two factories are the same object (no split).

    For PostgreSQL / MySQL the process-level write lock is **skipped**: the
    DB itself provides row-level locking + MVCC, so serialising writes at the
    process level only throttles throughput. Reads and writes still run under
    the watchdog so a wedged driver surfaces as a loud ``TimeoutError``.
    """

    def __init__(
        self,
        write_session_local: async_sessionmaker,
        read_session_local: async_sessionmaker | None = None,
        *,
        db_type: "DatabaseType | str | None" = None,
    ) -> None:
        """Bind the write / read session factories.

        Args:
            write_session_local: Session factory bound to the writer engine.
            read_session_local: Session factory bound to the reader engine.
                Defaults to ``write_session_local`` when no split exists
                (``:memory:`` / PostgreSQL / MySQL).
            db_type: Database backend. When set to a client/server backend
                (PostgreSQL / MySQL) the process-level write lock is skipped
                and a wider watchdog applies. ``None`` keeps the legacy
                SQLite behaviour (write lock + 30s watchdog) for backward
                compatibility.
        """
        self._write_session_local = write_session_local
        self._read_session_local = read_session_local or write_session_local
        self._write_lock = asyncio.Lock()
        # DDL serialisation lock — taken by ``create_cur_session_tables`` /
        # ``drop_cur_session_tables`` / ``cleanup_all_runtime_state`` /
        # ``drop_session_tables_by_id`` on **all** backends. DDL (CREATE /
        # DROP TABLE) is rare (bind-time + teardown) but must not interleave
        # with itself: two concurrent teardowns of the same session's tables
        # or a bind racing a drop can leave half-created / half-dropped
        # tables. On SQLite the per-write lock already serialises DDL with
        # every DAO write; this lock adds DDL-vs-DDL protection on
        # PostgreSQL / MySQL, where the per-write lock is skipped (the DB
        # itself is the write arbiter). DDL-vs-write is still guarded by the
        # DB's own table-level DDL lock (AccessExclusiveLock on PostgreSQL,
        # equivalent metadata lock on MySQL) — a DROP blocks until in-flight
        # writes commit, and a write that starts after the DROP completes
        # sees "table does not exist", which is the expected teardown
        # semantics, not data corruption.
        self._ddl_lock = asyncio.Lock()
        # Skip the process-level write lock for client/server backends:
        # PostgreSQL / MySQL provide native write concurrency (row locks +
        # MVCC), so serialising writes in-process only throttles throughput.
        # SQLite (file / :memory:) still needs the lock — it allows a single
        # writer at a time and the app-level lock avoids the SQLite lock
        # contention path entirely.
        self._use_write_lock = not _db_type_is_client_server(db_type)
        self._watchdog_seconds = (
            _DB_SESSION_WATCHDOG_SECONDS_CLIENT_SERVER
            if _db_type_is_client_server(db_type)
            else _DB_SESSION_WATCHDOG_SECONDS_SQLITE
        )

    @property
    def write_locked(self) -> bool:
        """Whether a ``write()`` block currently holds the process write lock.

        Returns ``False`` on client/server backends (PostgreSQL / MySQL)
        where ``write()`` skips the process-level lock — the DB itself is the
        arbiter. On SQLite returns whether the lock is currently held.
        """
        return self._use_write_lock and self._write_lock.locked()

    @asynccontextmanager
    async def ddl_lock(self) -> AsyncIterator[None]:
        """Acquire the DDL serialisation lock (all backends).

        Taken by the DDL / cleanup helpers (``create_cur_session_tables``,
        ``drop_cur_session_tables``, ``cleanup_all_runtime_state``,
        ``drop_session_tables_by_id``) so DDL operations serialise with each
        other. See ``DbSessions.__init__`` for the full rationale.
        """
        async with self._ddl_lock:
            yield

    @asynccontextmanager
    async def read(self) -> AsyncIterator[AsyncSession]:
        """Yield a session for read-only work; the write lock is not held.

        The whole block runs under the watchdog so a wedged driver surfaces
        as a loud ``TimeoutError`` instead of hanging the caller forever.
        """
        try:
            async with asyncio.timeout(self._watchdog_seconds):
                async with self._read_session_local() as session:
                    yield session
        except TimeoutError:
            team_logger.warning(
                "DbSessions.read() exceeded the %.0fs watchdog; the DB driver may be wedged",
                self._watchdog_seconds,
            )
            raise

    @asynccontextmanager
    async def write(self) -> AsyncIterator[AsyncSession]:
        """Yield a session for write work.

        For SQLite (file / ``:memory:``) the process-wide write lock is held:
        SQLite allows a single writer at a time, so the lock funnels writers
        through one logical slot and avoids the SQLite lock contention path.
        For PostgreSQL / MySQL the lock is **skipped**: the DB itself is the
        arbiter (row locks + MVCC), and an in-process lock would only
        serialise writes the DB would otherwise run concurrently.

        The lock is non-reentrant: a write must not open a nested ``write()``
        or it will deadlock. Compose multi-step writes inside a single
        ``write()`` block, and keep the lock on the outermost public method
        when one write helper delegates to another.

        The session block (not the lock wait — writers legitimately queue
        behind each other on SQLite) runs under the watchdog so a wedged
        driver surfaces as a loud ``TimeoutError``.
        """
        if not self._use_write_lock:
            try:
                async with asyncio.timeout(self._watchdog_seconds):
                    async with self._write_session_local() as session:
                        yield session
            except TimeoutError:
                team_logger.warning(
                    "DbSessions.write() exceeded the %.0fs watchdog; the DB driver may be wedged",
                    self._watchdog_seconds,
                )
                raise
            return
        async with self._write_lock:
            try:
                async with asyncio.timeout(self._watchdog_seconds):
                    async with self._write_session_local() as session:
                        yield session
            except TimeoutError:
                team_logger.warning(
                    "DbSessions.write() exceeded the %.0fs watchdog; the DB driver may be wedged",
                    self._watchdog_seconds,
                )
                raise


async def retry_on_locked(
    op: Callable[[], Awaitable[_T]],
    *,
    on_locked_result: _T,
    label: str,
) -> _T:
    """Run a write ``op`` with exponential back-off on transient DB errors.

    Retries two kinds of transient failure:

    - ``OperationalError`` ("database is locked") — SQLite write contention
      (WAL checkpoint edges or a foreign process touching the same file).
    - ``TimeoutError`` (pool exhausted) — a write checkout hit the
      ``AsyncAdaptedQueuePool`` ``size`` cap with ``overflow=0`` and timed
      out (``pool_timeout``). This surfaces under burst write concurrency
      where several writers race for the small write pool at once; the
      contention is transient, so a short back-off lets a checked-out
      connection free up and the retry succeed.

    The back-off sleep runs *between* ``op`` calls, never inside the session
    ``op`` opens: ``op`` must fully exit its ``async with write()`` block
    (releasing the connection) before returning, so a retry wait never pins
    a checked-out connection and starves the pool.

    Note: a *persistent* timeout (a checked-out connection never released,
    i.e. a real leak) is not cured by retry — the back-off only buys time
    for transient contention to clear; after the budget it still returns
    ``on_locked_result``.

    Args:
        op: Zero-arg coroutine factory performing one write attempt.
        on_locked_result: Value returned after the final failed attempt.
        label: Human-readable operation name for logs.

    Returns:
        The ``op`` result, or ``on_locked_result`` when every attempt hit
        a transient error.
    """
    for attempt in range(_DB_RETRY_ATTEMPTS):
        try:
            return await op()
        except (OperationalError, SATimeoutError) as e:
            if attempt >= _DB_RETRY_ATTEMPTS - 1:
                team_logger.error("%s failed after %d attempts: %s", label, _DB_RETRY_ATTEMPTS, e)
                return on_locked_result
            delay = _DB_RETRY_BASE_DELAY * (2**attempt)
            team_logger.warning(
                "%s hit a transient DB error (%s); retrying in %ss (attempt %d)",
                label,
                type(e).__name__,
                delay,
                attempt + 1,
            )
            await asyncio.sleep(delay)
    return on_locked_result


def _is_transient_db_error(e: BaseException) -> bool:
    """Whether ``e`` is worth a retry: contention or a timeout, not a permanent failure."""
    if isinstance(e, OperationalError):
        message = str(e.orig if e.orig is not None else e).lower()
        return any(marker in message for marker in _TRANSIENT_OPERATIONAL_MARKERS)
    return isinstance(e, (SATimeoutError, TimeoutError))


async def retry_on_transient(
    op: Callable[[], Awaitable[_T]],
    *,
    label: str,
) -> _T:
    """Run a write ``op`` with back-off on transient DB errors, raising on exhaustion.

    Same back-off as ``retry_on_locked`` but for callers that cannot degrade
    on their own: retries lock contention, pool checkout timeouts and the
    ``DbSessions.write()`` watchdog ``TimeoutError``, and re-raises the last
    error once the budget is spent instead of returning a fallback value.
    A permanent ``OperationalError`` (disk I/O error, malformed image, ...)
    is raised on the first attempt: backing off cannot fix it and would only
    stall every caller. Watchdog hits get a smaller budget
    (``_WATCHDOG_RETRY_ATTEMPTS``) because each one already waited out the
    full watchdog.

    Args:
        op: Zero-arg coroutine factory performing one write attempt; it must
            exit its ``async with write()`` block before returning.
        label: Human-readable operation name for logs.

    Returns:
        The ``op`` result.
    """
    attempt = 0
    watchdog_hits = 0
    while True:
        try:
            return await op()
        except _TRANSIENT_DB_ERRORS as e:
            attempt += 1
            if not _is_transient_db_error(e):
                team_logger.error("%s hit a non-retryable DB error: %s", label, e)
                raise
            if isinstance(e, TimeoutError):
                watchdog_hits += 1
            if attempt >= _DB_RETRY_ATTEMPTS or watchdog_hits >= _WATCHDOG_RETRY_ATTEMPTS:
                team_logger.error("%s failed after %d attempts: %s", label, attempt, e)
                raise
            delay = _DB_RETRY_BASE_DELAY * (2 ** (attempt - 1))
            team_logger.warning(
                "%s hit a transient DB error (%s); retrying in %ss (attempt %d)",
                label,
                type(e).__name__,
                delay,
                attempt,
            )
            await asyncio.sleep(delay)


def _get_table_names(sync_conn) -> list[str]:
    """Return all table names currently present in the database."""
    return list(inspect(sync_conn).get_table_names())


def _snapshot_existing_tables(sync_conn, table_names: list[str]) -> set[str]:
    """Return the subset of ``table_names`` that already exist right now.

    A single ``inspect().has_table`` call per name (no index/column PRAGMA),
    cheap relative to the per-table ``get_indexes`` it lets the migration skip.
    """
    inspector = inspect(sync_conn)
    return {name for name in table_names if inspector.has_table(name)}


def _drop_table(sync_conn, table_name: str) -> None:
    """Drop one table with raw SQL to avoid reflection-order issues."""
    quoted_name = table_name.replace('"', '""')
    sync_conn.exec_driver_sql(f'DROP TABLE IF EXISTS "{quoted_name}"')


def _clear_table(sync_conn, table_name: str) -> None:
    """Delete all rows from one reflected table."""
    quoted_name = table_name.replace('"', '""')
    sync_conn.exec_driver_sql(f'DELETE FROM "{quoted_name}"')


def _create_static_tables(sync_conn) -> None:
    """Create the static team-scoped schema, and only that.

    Scoped to ``static_tables()`` rather than the whole ``SQLModel.metadata``:
    the metadata registry also holds the per-session tables of every session
    the process has bound so far, which belong to
    ``create_cur_session_tables()`` and must not leak into an unrelated
    database. Creating them here would also make schema setup cost grow with
    the process's session history instead of with the schema itself.

    Each ``Table.create(checkfirst=False)`` is wrapped individually in
    :func:`_run_ddl_idempotent` rather than one ``create_all(checkfirst=True)``
    covering the set:

    - ``checkfirst=True`` on ``create_all`` is an in-process ``has_table`` probe
      → ``CREATE`` pair, not atomic across processes. ``initialize_engine``
      runs directly on the writer engine (no ``DbSessions`` is published yet,
      so no ``ddl_lock()`` is available here), and two processes concurrently
      initialising the same PG/MySQL database both read "missing" and both
      issue ``CREATE TABLE``; the DB's own DDL lock serialises the two
      ``CREATE``s but the second one fails with ``already exists``
      (PG ``ProgrammingError`` SQLSTATE 42P07; MySQL 1050).
      ``retry_on_transient`` does not cover ``already exists`` (it is not in
      ``_TRANSIENT_OPERATIONAL_MARKERS`` and PG raises ``ProgrammingError``,
      not ``OperationalError``), so without this wrapper the second
      ``initialize()`` crashes — the same issue-115 symptom-5 shape closed
      for ``_ensure_session_tables`` / ``ensure_org_static_tables``.

    - Per-table wrapping keeps the verify step honest: the previous
      ``create_all`` + ``verify == all(t.name in has_table_names for t in
      static_tables())`` form would re-raise on the first ``already exists``
      swallow when fewer than all static tables were present — the loser of
      the concurrent first-create race sees its own ``CREATE`` for table 1
      swallowed (the winner committed it) but its ``CREATE`` for tables 2..N
      never ran, so verify reports a partial set and the helper re-raises
      the swallowed error instead of letting it race forward to create the
      rest. Per-table verify = "is *this* table present" closes the race
      table-by-table instead of "are *all* tables present" which fails on
      the very first loser. ``create_all`` is still used per-table (not a
      hand-rolled ``CREATE TABLE`` DDL string) so the FK constraint ordering
      and the dialect-specific DDL compilation stay SQLAlchemy's job; the
      ``tables=[one_table]`` argument scopes each call to exactly one table.

    The tables are processed in SQLAlchemy's topological sort order
    (``sqlalchemy.sort_tables``), so a child table's ``CREATE`` never runs
    before its FK target exists. ``create_all(tables=[all])`` does this
    internally, but per-table wrapping would otherwise use the
    ``SQLModel.metadata.tables`` dict iteration order — which is the model
    registration order, not the FK dependency order. PG/MySQL enforce FK
    target existence at ``CREATE TABLE`` time (SQLite defers the check to
    ``INSERT``), so an out-of-order per-table create would fail on the
    child before the parent exists (issue #115 review round 14).
    """
    from sqlalchemy.sql.util import sort_tables

    for table in sort_tables(static_tables()):
        _run_ddl_idempotent(
            sync_conn,
            lambda t=table: SQLModel.metadata.create_all(sync_conn, tables=[t]),
            lambda t=table: inspect(sync_conn).has_table(t.name),
        )


def _ensure_team_member_role_column(sync_conn) -> None:
    """Backfill the ``team_member.role`` column on pre-existing DBs.

    ``SQLModel.metadata.create_all`` only creates tables that don't
    exist; it never alters live tables. DB files created before the
    ``role`` column was introduced therefore lack it after upgrading,
    and the next ``INSERT`` against ``team_member`` fails. Probe the
    column list and run a one-shot ``ALTER TABLE ... ADD COLUMN`` with
    a backfill default of ``teammate`` so legacy rows are interpreted
    as ordinary teammates. SQLite / PostgreSQL / MySQL all accept the
    same form; column defaults apply to subsequent reads of pre-existing
    rows too, so no separate UPDATE is required.
    """
    inspector = inspect(sync_conn)
    if "team_member" not in inspector.get_table_names():
        return
    columns = {col["name"] for col in inspector.get_columns("team_member")}
    if "role" in columns:
        return

    # Hard-coded "teammate" to keep this module free of the
    # ``schema.team`` import (which would close a circular dependency).
    # Keep in sync with ``TeamRole.TEAMMATE`` if that enum value ever changes.
    default_role = "teammate"
    # Wrapped in _add_column_idempotent (issue #115 review round 14): the
    # inspect-then-ALTER pattern is not atomic across processes. Two processes
    # concurrently initialising the same PG/MySQL database both see the column
    # missing and both issue ALTER TABLE ADD COLUMN; the DB's own DDL lock
    # serialises the two ALTERs but the second fails with 'duplicate column
    # name' (PG ProgrammingError SQLSTATE 42701; MySQL 1060), crashing
    # initialize().
    _add_column_idempotent(
        sync_conn, "team_member", "role", f"TEXT NOT NULL DEFAULT '{default_role}'"
    )
    team_logger.info(
        "Migrated legacy team_member table: added role column with default %s",
        default_role,
    )


def _ensure_team_member_options_column(sync_conn) -> None:
    """Ensure TeamMember.options exists, backfill it, and drop legacy columns.

    The legacy ``model_ref_json`` column is migrated into the unified
    ``options`` JSON (see ``merge_legacy_member_options``) and then dropped.
    The DROP is wrapped in :func:`_drop_column_idempotent` and the
    migration block is gated by a fresh ``inspect`` re-check: two processes
    concurrently initialising the same PG/MySQL database can both pass the
    top-of-function ``model_ref_json in columns`` check (captured before any
    ALTER) and race the migration. Without the re-check + idempotent DROP,
    the loser of the race crashes — either its ``SELECT ... model_ref_json``
    hits "no such column" (the winner already dropped it), or its own DROP
    hits "no such column" / "does not exist" / "can't drop" (issue #115
    review round 14, sibling of the round-11 ``create_all`` TOCTOU).
    """
    inspector = inspect(sync_conn)
    if "team_member" not in inspector.get_table_names():
        return
    columns = {col["name"] for col in inspector.get_columns("team_member")}
    if "options" not in columns:
        # Wrapped in _add_column_idempotent for cross-process TOCTOU
        # (issue #115 review round 14, same as _ensure_team_member_role_column).
        _add_column_idempotent(sync_conn, "team_member", "options", "TEXT")
        team_logger.info("Migrated legacy team_member table: added options column")

    if "model_ref_json" not in columns:
        return

    from openjiuwen.agent_teams.tools.member_options import merge_legacy_member_options

    # Re-inspect right before the legacy-column migration: another process
    # may have completed this migration (and dropped model_ref_json) between
    # the top-of-function inspect and now. Without this re-check the
    # SELECT below crashes with "no such column: model_ref_json" when two
    # processes race the migration (issue #115 review round 14). The narrow
    # window between this re-check and the SELECT is closed at the DROP
    # below by ``_drop_column_idempotent``.
    if "model_ref_json" not in {
        col["name"] for col in inspect(sync_conn).get_columns("team_member")
    }:
        return

    rows = sync_conn.exec_driver_sql(
        "SELECT member_name, team_name, options, model_ref_json FROM team_member"
    ).mappings()
    for row in rows:
        merged = merge_legacy_member_options(
            options=row.get("options"),
            model_ref_json=row.get("model_ref_json"),
        )
        if merged == row.get("options"):
            continue
        sync_conn.exec_driver_sql(
            "UPDATE team_member SET options = ? WHERE member_name = ? AND team_name = ?",
            (merged, row["member_name"], row["team_name"]),
        )

    # Wrapped in _drop_column_idempotent for cross-process TOCTOU (issue
    # #115 review round 14): two processes that both saw model_ref_json
    # present in the top-of-function inspect both reach this DROP; the DB's
    # DDL lock serialises the two ALTERs but the second fails with
    # "no such column" / "does not exist" / "can't drop" (SQLite / PG /
    # MySQL). The helper swallows the not-found error after re-checking via
    # ``inspect`` that the column is in fact gone.
    _drop_column_idempotent(sync_conn, "team_member", "model_ref_json")
    team_logger.info("Migrated legacy team_member table: dropped model_ref_json column")


def _ensure_team_info_capability_columns(sync_conn) -> None:
    """Backfill the F_62 capability columns on pre-existing ``team_info`` tables.

    ``dispatch_mode`` / ``enable_task_verification`` store the per-instance
    effective values chosen at ``build_team`` time. Legacy rows default to
    the pre-F_62 behavior (autonomous, no verification expectation).
    """
    inspector = inspect(sync_conn)
    if "team_info" not in inspector.get_table_names():
        return
    columns = {col["name"] for col in inspector.get_columns("team_info")}
    if "dispatch_mode" not in columns:
        # Wrapped in _add_column_idempotent for cross-process TOCTOU
        # (issue #115 review round 14).
        _add_column_idempotent(
            sync_conn, "team_info", "dispatch_mode", "TEXT NOT NULL DEFAULT 'autonomous'"
        )
        team_logger.info("Migrated legacy team_info table: added dispatch_mode column")
    if "enable_task_verification" not in columns:
        _add_column_idempotent(
            sync_conn, "team_info", "enable_task_verification", "BOOLEAN NOT NULL DEFAULT 0"
        )
        team_logger.info("Migrated legacy team_info table: added enable_task_verification column")


def _has_table(sync_conn, table_name: str) -> bool:
    """Return whether one table exists (a single has_table call, no PRAGMAs)."""
    return inspect(sync_conn).has_table(table_name)


async def cur_session_table_exists(sessions: DbSessions, table_kind: str) -> bool:
    """Read-only existence check for the bound session's dynamic tables.

    ``table_kind`` is ``"task"`` or ``"message"``: each kind's tables are
    created together per session, so one representative table per kind is
    enough. Read paths use this to answer empty for sessions that never
    ran a team — reads never run DDL.
    """
    session_id = get_session_id()
    if not session_id:
        return False
    model_factory = {"task": _get_task_model, "message": _get_message_model}[table_kind]
    table_name = model_factory().__table__.name
    async with sessions.read() as session:
        conn = await session.connection()
        return await conn.run_sync(_has_table, table_name)


def _ensure_message_protocol_column(sync_conn) -> None:
    """Backfill the ``protocol`` column on pre-existing per-session message tables.

    ``SQLModel.metadata.create_all`` only creates tables that don't exist;
    it never alters live tables.  Per-session message tables created before
    the ``protocol`` column was introduced therefore lack it after upgrading.
    Probe only the current session's message table and run
    ``ALTER TABLE ... ADD COLUMN`` with a default of ``"plain"`` so legacy
    rows retain their original semantics. Other sessions may be dropped
    concurrently and are migrated when they are bound themselves.

    The ALTER is wrapped in :func:`_add_column_idempotent` (mirroring
    ``_create_static_tables``): the inspect-then-ALTER pattern is not atomic
    across processes. Two processes concurrently binding the same
    ``session_id`` (e.g. on a PG/MySQL backend) can both see the column
    missing and both issue ``ALTER TABLE ADD COLUMN``; the DB's own DDL lock
    serialises the two ALTERs but the second one fails with ``duplicate
    column name`` (PG ``ProgrammingError`` SQLSTATE 42701; MySQL 1060). That
    error is not in ``_TRANSIENT_DB_ERRORS`` (PG raises ``ProgrammingError``,
    not ``OperationalError``) nor does it match
    ``_TRANSIENT_OPERATIONAL_MARKERS``, so without this wrapper the second
    bind crashes ``create_cur_session_tables()`` — the same issue-115
    symptom-5 shape closed for ``_create_static_tables`` /
    ``_ensure_session_tables``.
    """
    table_name = next(
        (name for name in _current_session_migration_tables() if name.startswith("team_message_")),
        None,
    )
    if table_name is None:
        return
    inspector = inspect(sync_conn)
    if not inspector.has_table(table_name):
        return
    try:
        columns = {col["name"] for col in inspector.get_columns(table_name)}
    except NoSuchTableError:
        team_logger.info("Message table %s disappeared during protocol migration", table_name)
        return
    if "protocol" in columns:
        return
    _add_column_idempotent(
        sync_conn, table_name, "protocol", "TEXT NOT NULL DEFAULT 'plain'"
    )
    team_logger.info(
        "Migrated legacy message table %s: added protocol column",
        table_name,
    )


def _ensure_message_meta_column(sync_conn) -> None:
    """Backfill the ``meta`` column on pre-existing per-session message tables.

    Same reason as ``_ensure_message_protocol_column``: ``create_all`` never
    alters a live table. Only the current session's message table is probed;
    legacy rows get NULL meta, which the delivery path reads as "plain message,
    render content verbatim" — the pre-F_63 behavior.

    The ALTER is wrapped in :func:`_add_column_idempotent` for the same
    cross-process TOCTOU reason (see ``_ensure_message_protocol_column``).
    """
    table_name = next(
        (name for name in _current_session_migration_tables() if name.startswith("team_message_")),
        None,
    )
    if table_name is None:
        return
    inspector = inspect(sync_conn)
    if not inspector.has_table(table_name):
        return
    try:
        columns = {col["name"] for col in inspector.get_columns(table_name)}
    except NoSuchTableError:
        team_logger.info("Message table %s disappeared during meta migration", table_name)
        return
    if "meta" in columns:
        return
    _add_column_idempotent(sync_conn, table_name, "meta", "TEXT")
    team_logger.info("Migrated legacy message table %s: added meta column", table_name)


def _current_session_migration_tables() -> list[str]:
    """The current session's 3 migration-candidate dynamic table names.

    Only ``team_task_*`` / ``team_task_dependency_*`` / ``team_message_*`` are
    touched by ``_ensure_dynamic_table_indexes``; ``message_read_status_*`` and
    ``team_review_vote_*`` have no legacy-structure gap (they are created on the
    current scheme and never migrated), so they are deliberately excluded.

    Returns an empty list when no session is bound — the migration then becomes
    a no-op, same as before.
    """
    session_id = get_session_id()
    if not session_id:
        return []
    suffix = _sanitize_session_id_for_table(session_id)
    return [
        f"team_task_{suffix}",
        f"team_task_dependency_{suffix}",
        f"team_message_{suffix}",
    ]


def _ensure_dynamic_table_indexes(
    sync_conn,
    candidates: list[str],
    existing_before_create: set[str],
) -> None:
    """Rebuild per-session dynamic-table indexes to the current scheme (A1/A2/D4).

    ``__table__.create(checkfirst=True)`` never alters an existing table, so a
    persistent team's tables keep whatever indexes they were born with. Bring
    them up to the current design:
      - A1: drop the dead ``team_name`` index on every dynamic table — a
        per-session table has ~1 distinct team_name, so the index never helps
        a read and only costs a B-tree write per INSERT.
      - A2 (message tables): drop the four weak single-column indexes
        (``to_member_name`` / ``timestamp`` / ``broadcast`` / ``is_read``) and
        create the two composites the real queries use (``*_inbox`` /
        ``*_bcast_ts``), kept in sync with ``models._get_message_model``.
      - D4 (task tables): drop the standalone ``assignee`` index (folded into
        the composite ``*_assignee_status`` for get_tasks_by_assignee) and the
        dead ``updated_at`` index (no query filters/orders by it). ``status``
        keeps its standalone index.

    Every statement is idempotent (``IF EXISTS`` / ``IF NOT EXISTS``), so a
    table already on the new scheme is a cheap no-op.

    Scope: ``candidates`` are the current session's tables (computed from
    ``get_session_id``). ``existing_before_create`` is the snapshot of which of
    them already existed *before* ``create(checkfirst=True)`` ran; tables not
    in it were just created on the current scheme and are skipped entirely — no
    ``get_indexes`` / ``get_columns`` PRAGMA round-trip at all. This replaces
    the old ``inspector.get_table_names()`` full-DB scan: a fresh bind no
    longer pays a PRAGMA call per historical dynamic table in the file, which
    is what pushed bind-time DDL past the write-lock watchdog under concurrent
    multi-process binds.
    """
    inspector = inspect(sync_conn)
    for table_name in candidates:
        is_message = table_name.startswith("team_message_")
        is_dependency = table_name.startswith("team_task_dependency_")
        is_task = table_name.startswith("team_task_") and not is_dependency
        if not (is_message or is_dependency or is_task):
            continue
        if table_name not in existing_before_create:
            # Created fresh this bind → already on the current scheme; touching
            # it would only burn a ``get_indexes``/``get_columns`` PRAGMA lock.
            continue

        index_names = {idx["name"] for idx in inspector.get_indexes(table_name)}

        team_name_index = f"ix_{table_name}_team_name"
        if team_name_index in index_names:
            sync_conn.exec_driver_sql(f'DROP INDEX IF EXISTS "{team_name_index}"')
            team_logger.info("Migrated %s: dropped dead team_name index", table_name)

        if is_task:
            legacy_task_indexes = [f"ix_{table_name}_assignee", f"ix_{table_name}_updated_at"]
            assignee_status_index = f"ix_{table_name}_assignee_status"
            needs_task_migration = (
                bool(index_names & set(legacy_task_indexes)) or assignee_status_index not in index_names
            )
            if needs_task_migration:
                for legacy in legacy_task_indexes:
                    sync_conn.exec_driver_sql(f'DROP INDEX IF EXISTS "{legacy}"')
                sync_conn.exec_driver_sql(
                    f'CREATE INDEX IF NOT EXISTS "{assignee_status_index}" ON "{table_name}" (assignee, status)'
                )
                team_logger.info(
                    "Migrated task table %s: folded assignee into (assignee, status), dropped dead updated_at index",
                    table_name,
                )

            # F_59: add the verify-gate ``reviewer`` column to pre-existing tables.
            # Wrapped in _add_column_idempotent for cross-process TOCTOU
            # (issue #115 review round 14, same as _ensure_team_member_role_column).
            task_columns = {c["name"] for c in inspector.get_columns(table_name)}
            if "reviewer" not in task_columns:
                _add_column_idempotent(sync_conn, table_name, "reviewer", "TEXT")
                team_logger.info("Migrated task table %s: added reviewer column", table_name)

            # F_62: review voting — round counter + per-task round ceiling.
            if "review_round" not in task_columns:
                _add_column_idempotent(
                    sync_conn, table_name, "review_round", "INTEGER NOT NULL DEFAULT 0"
                )
                team_logger.info("Migrated task table %s: added review_round column", table_name)
            if "max_review_rounds" not in task_columns:
                _add_column_idempotent(sync_conn, table_name, "max_review_rounds", "INTEGER")
                team_logger.info("Migrated task table %s: added max_review_rounds column", table_name)

            # F_118: author's completion note, read by handoff briefs / view_task.
            if "note" not in task_columns:
                _add_column_idempotent(sync_conn, table_name, "note", "TEXT")
                team_logger.info("Migrated task table %s: added note column", table_name)

            # F_119: author's one-line completion summary, read by digests / briefs.
            if "summary" not in task_columns:
                _add_column_idempotent(sync_conn, table_name, "summary", "TEXT")
                team_logger.info("Migrated task table %s: added summary column", table_name)

            # F_128: member subtasks — parent link + creator.
            if "parent_task_id" not in task_columns:
                _add_column_idempotent(sync_conn, table_name, "parent_task_id", "TEXT")
                team_logger.info("Migrated task table %s: added parent_task_id column", table_name)
            if "created_by" not in task_columns:
                _add_column_idempotent(sync_conn, table_name, "created_by", "TEXT")
                team_logger.info("Migrated task table %s: added created_by column", table_name)
            if "resume_pending" not in task_columns:
                _add_column_idempotent(
                    sync_conn, table_name, "resume_pending", "BOOLEAN NOT NULL DEFAULT 0"
                )
                team_logger.info("Migrated task table %s: added resume_pending column", table_name)

            # F_129: task versioning + edit notes.
            for column in ("revision", "observed_revision", "parent_revision"):
                if column not in task_columns:
                    _add_column_idempotent(
                        sync_conn, table_name, column, "INTEGER NOT NULL DEFAULT 0"
                    )
                    team_logger.info("Migrated task table %s: added %s column", table_name, column)
            if "change_notes" not in task_columns:
                _add_column_idempotent(sync_conn, table_name, "change_notes", "TEXT")
                team_logger.info("Migrated task table %s: added change_notes column", table_name)

            # F_59: fold the legacy execution states into the condition-named
            # IN_PROGRESS. claimed / started / plan_approved were all "a member
            # is executing it"; map them uniformly. Distinguishing an unapproved
            # plan-mode claim into PLANNING would need the plan index and is a
            # best-effort refinement deferred (rare, and the row is still
            # completable via the normal flow once re-owned).
            legacy_status_result = sync_conn.exec_driver_sql(
                f"UPDATE \"{table_name}\" SET status='in_progress' "
                "WHERE status IN ('claimed', 'started', 'plan_approved')"
            )
            migrated_rows = legacy_status_result.rowcount or 0
            if migrated_rows > 0:
                team_logger.info(
                    "Migrated task table %s: folded %d legacy execution-status row(s) into in_progress",
                    table_name,
                    migrated_rows,
                )
            continue

        if not is_message:
            continue

        legacy_message_indexes = [
            f"ix_{table_name}_to_member_name",
            f"ix_{table_name}_timestamp",
            f"ix_{table_name}_broadcast",
            f"ix_{table_name}_is_read",
        ]
        inbox_index = f"ix_{table_name}_inbox"
        bcast_index = f"ix_{table_name}_bcast_ts"
        needs_migration = bool(index_names & set(legacy_message_indexes)) or inbox_index not in index_names
        if not needs_migration:
            continue

        for legacy in legacy_message_indexes:
            sync_conn.exec_driver_sql(f'DROP INDEX IF EXISTS "{legacy}"')
        sync_conn.exec_driver_sql(
            f'CREATE INDEX IF NOT EXISTS "{inbox_index}" ON "{table_name}" (to_member_name, is_read, timestamp)'
        )
        sync_conn.exec_driver_sql(
            f'CREATE INDEX IF NOT EXISTS "{bcast_index}" ON "{table_name}" (broadcast, timestamp)'
        )
        team_logger.info("Migrated message table %s: rebuilt to composite indexes", table_name)


class SqlEngines(NamedTuple):
    """Engines + session factories returned by ``initialize_engine``.

    For file-backed SQLite the write and read engines are distinct — a
    dedicated single-writer pool plus a larger reader pool — so a slow write
    never starves reader-connection availability. For ``:memory:`` /
    PostgreSQL / MySQL the read fields alias the write ones (no split).
    """

    write_engine: AsyncEngine
    read_engine: AsyncEngine
    write_session_local: async_sessionmaker
    read_session_local: async_sessionmaker


def _attach_sqlite_pragmas(
    engine: AsyncEngine,
    *,
    in_memory: bool,
    enable_wal: bool,
    cache_size_kb: int,
    mmap_size_bytes: int,
    wal_autocheckpoint: int,
) -> None:
    """Attach the per-connection PRAGMA tuning to a SQLite engine.

    A ``:memory:`` db already lives in RAM, so the page-cache / mmap tuning is
    skipped there. ``cache_size=-<kb>`` is a KiB page cache (negative form);
    ``temp_store=MEMORY`` keeps sorts / transient B-trees off disk;
    ``mmap_size`` maps file bytes for read I/O; ``synchronous=NORMAL`` is the
    safe high-throughput WAL pairing; ``wal_autocheckpoint`` bounds WAL growth
    before a commit triggers a checkpoint. ``journal_mode=WAL`` is a persistent
    database-level setting applied once on first connect.
    """

    @event.listens_for(engine.sync_engine, "connect")
    def _set_pragmas(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        if not in_memory:
            cursor.execute("PRAGMA temp_store=MEMORY")
            cursor.execute(f"PRAGMA cache_size=-{cache_size_kb}")
            cursor.execute(f"PRAGMA mmap_size={mmap_size_bytes}")
            if enable_wal:
                cursor.execute("PRAGMA synchronous=NORMAL")
                cursor.execute(f"PRAGMA wal_autocheckpoint={wal_autocheckpoint}")
        cursor.close()

    if enable_wal and not in_memory:

        @event.listens_for(engine.sync_engine, "first_connect")
        def _set_wal(dbapi_connection, connection_record):
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.close()


def _build_file_sqlite_engine(
    conn_str: str,
    config: DatabaseConfig,
    *,
    pool_size: int,
    cache_size_kb: int,
    wal_autocheckpoint: int,
) -> AsyncEngine:
    """Build one file-backed SQLite engine with pool + PRAGMA tuning.

    Used to build both the single-writer engine and the reader engine over the
    same file. ``max_overflow=0`` keeps checkout timeouts surfacing real
    session leaks instead of masking them; ``pool_pre_ping`` is dropped because
    a local SQLite file connection does not silently die like a network socket.
    ``wal_autocheckpoint`` is passed explicitly (not read from config) so the
    writer engine can disable in-commit checkpointing (0) when a background
    checkpointer is running while the reader keeps the configured value.
    """
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{conn_str}",
        echo=False,
        future=True,
        poolclass=AsyncAdaptedQueuePool,
        pool_size=pool_size,
        max_overflow=0,
        pool_timeout=10,
        connect_args={
            "timeout": config.db_timeout,
            "check_same_thread": False,
        },
    )
    _attach_sqlite_pragmas(
        engine,
        in_memory=False,
        enable_wal=config.db_enable_wal,
        cache_size_kb=cache_size_kb,
        mmap_size_bytes=config.mmap_size_mb * 1024 * 1024,
        wal_autocheckpoint=wal_autocheckpoint,
    )
    return engine


async def run_wal_checkpoint_passive(engine: AsyncEngine) -> None:
    """Run one PASSIVE WAL checkpoint on a dedicated connection.

    ``wal_checkpoint(PASSIVE)`` never waits on readers or the writer — it moves
    whatever committed WAL frames it can into the main database file and
    returns — so it is safe to run off the app write lock on a separate
    connection. The background checkpointer calls this to keep WAL growth (and
    thus the in-commit checkpoint stalls it would otherwise cause) off the
    writer's commit path.
    """
    async with engine.connect() as conn:
        await conn.exec_driver_sql("PRAGMA wal_checkpoint(PASSIVE)")


async def initialize_engine(config: DatabaseConfig) -> SqlEngines:
    """Create and configure the async engine(s) and session factories.

    File-backed SQLite gets a read/write split: writes are serialised by the
    app-level lock onto a small single-writer pool, and reads run on a separate,
    larger reader pool so a slow write never starves reader-connection
    availability. ``:memory:`` (one shared StaticPool connection) and
    PostgreSQL / MySQL use a single engine for both.

    Args:
        config: Database configuration.

    Returns:
        A ``SqlEngines`` bundle. ``read_engine`` / ``read_session_local`` alias
        the write fields when there is no split.
    """
    db_type = config.db_type

    if db_type == DatabaseType.SQLITE:
        conn_str = config.connection_string
        in_memory = conn_str == ":memory:"
        if in_memory:
            # A single shared connection *is* the database — no split possible.
            write_engine = create_async_engine(
                "sqlite+aiosqlite:///:memory:",
                echo=False,
                future=True,
                poolclass=StaticPool,
                connect_args={"check_same_thread": False},
            )
            _attach_sqlite_pragmas(
                write_engine,
                in_memory=True,
                enable_wal=config.db_enable_wal,
                cache_size_kb=config.write_cache_size_kb,
                mmap_size_bytes=config.mmap_size_mb * 1024 * 1024,
                wal_autocheckpoint=config.wal_autocheckpoint,
            )
            read_engine = write_engine
        else:
            db_path = await asyncio.to_thread(Path(conn_str).expanduser)
            conn_str = str(db_path)
            if not db_path.parent.exists():
                db_path.parent.mkdir(parents=True, exist_ok=True)
            # When a background checkpointer runs (wal_checkpoint_interval_s > 0)
            # the writer disables in-commit auto-checkpoint (0) so no commit
            # ever stalls on a checkpoint; the reader never commits, so its
            # value is moot — keep the configured one.
            writer_autocheckpoint = 0 if config.wal_checkpoint_interval_s > 0 else config.wal_autocheckpoint
            write_engine = _build_file_sqlite_engine(
                conn_str,
                config,
                pool_size=config.write_pool_size,
                cache_size_kb=config.write_cache_size_kb,
                wal_autocheckpoint=writer_autocheckpoint,
            )
            read_engine = _build_file_sqlite_engine(
                conn_str,
                config,
                pool_size=config.read_pool_size,
                cache_size_kb=config.read_cache_size_kb,
                wal_autocheckpoint=config.wal_autocheckpoint,
            )

    elif db_type == DatabaseType.POSTGRESQL:
        conn_str = config.connection_string.strip()
        if not conn_str:
            raise ValueError("PostgreSQL requires a non-empty connection_string")
        if conn_str.startswith("postgres://"):
            conn_str = f"postgresql://{conn_str.removeprefix('postgres://')}"
        if conn_str.startswith("postgresql://"):
            conn_str = f"postgresql+asyncpg://{conn_str.removeprefix('postgresql://')}"
        if not conn_str.startswith("postgresql+asyncpg://"):
            raise ValueError("PostgreSQL connection_string must use postgresql+asyncpg:// scheme")

        write_engine = create_async_engine(
            conn_str,
            echo=False,
            future=True,
            pool_size=10,
            max_overflow=20,
            pool_pre_ping=True,
            pool_recycle=1800,
            # READ COMMITTED is the isolation level the DAO layer assumes:
            # read-modify-write sequences (SELECT ... FOR UPDATE + plain
            # SELECT count) must see the latest committed data after acquiring
            # a row lock, otherwise a stale read view (MySQL default RR
            # establishes it at the first consistent read) hides concurrent
            # upstream commits and leaves downstream tasks stuck BLOCKED
            # (issue #115). PostgreSQL defaults to READ COMMITTED; setting it
            # explicitly documents the assumption for both backends.
            isolation_level="READ COMMITTED",
        )
        read_engine = write_engine

    elif db_type == DatabaseType.MYSQL:
        conn_str = config.connection_string.strip()
        if not conn_str:
            raise ValueError("MySQL requires a non-empty connection_string")
        if conn_str.startswith("mysql://"):
            conn_str = f"mysql+aiomysql://{conn_str.removeprefix('mysql://')}"
        elif not conn_str.startswith("mysql+aiomysql://"):
            raise ValueError("MySQL connection_string must use mysql:// or mysql+aiomysql:// scheme")

        write_engine = create_async_engine(
            conn_str,
            echo=False,
            future=True,
            pool_size=10,
            max_overflow=20,
            pool_pre_ping=True,
            pool_recycle=1800,
            # MySQL defaults to REPEATABLE READ, which establishes a read view
            # at the first consistent read in a transaction. That view hides
            # concurrent upstream commits from the plain SELECT count in
            # _refresh_status_in_session, so two upstreams completing
            # concurrently each see "1 unresolved" and both leave the
            # downstream BLOCKED (issue #115). READ COMMITTED gives each
            # statement a fresh snapshot, so the count after the FOR UPDATE
            # lock sees the other upstream's committed dep resolutions.
            isolation_level="READ COMMITTED",
        )
        read_engine = write_engine
    else:
        raise NotImplementedError(f"Database type {config.db_type} not yet implemented")

    write_session_local = async_sessionmaker(
        bind=write_engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
    )
    if read_engine is write_engine:
        read_session_local = write_session_local
    else:
        read_session_local = async_sessionmaker(
            bind=read_engine,
            class_=AsyncSession,
            expire_on_commit=False,
            autoflush=False,
        )

    # DDL + migrations run on the writer engine (they are writes).
    try:
        async with write_engine.begin() as conn:
            await conn.run_sync(_create_static_tables)
            await conn.run_sync(_ensure_team_member_role_column)
            await conn.run_sync(_ensure_team_member_options_column)
            await conn.run_sync(_ensure_team_info_capability_columns)
    except BaseException:
        # The engines never reach the caller, so nobody else can dispose them:
        # a failed DDL (e.g. ``database is locked``) retried by a poller would
        # otherwise leak a writer + reader pool per attempt.
        if read_engine is not write_engine:
            await read_engine.dispose()
        await write_engine.dispose()
        raise

    return SqlEngines(write_engine, read_engine, write_session_local, read_session_local)


async def _ensure_session_tables(session: AsyncSession) -> None:
    """Create / ensure the per-session dynamic tables on an open session.

    The per-table ``CREATE TABLE`` is wrapped in :func:`_run_ddl_idempotent`
    rather than relying on SQLAlchemy's ``checkfirst=True`` alone: ``checkfirst``
    is an in-process ``has_table`` probe → ``CREATE`` pair, not atomic across
    processes. Two teammate processes concurrently binding the same
    ``session_id`` (e.g. on a PG/MySQL backend) can both read "missing" and
    both issue ``CREATE TABLE``; the DB's own DDL lock serialises the two
    ``CREATE``s but the second one fails with ``already exists``
    (PG: ``ProgrammingError`` / SQLSTATE 42P07; MySQL: 1050). ``ddl_lock()``
    is a process-local ``asyncio.Lock`` and does not serialise the two
    processes. ``_run_ddl_idempotent`` swallows the ``already exists`` error
    under a SAVEPOINT (skipped on MySQL — implicit DDL commit destroys it)
    and re-verifies via ``inspect().has_table`` that the table is in fact
    present; if it is somehow still missing the helper re-raises instead of
    silently leaving the schema absent. ``retry_on_transient`` does not
    cover ``already exists`` (it is not in ``_TRANSIENT_OPERATIONAL_MARKERS``
    and PG raises ``ProgrammingError``, not ``OperationalError``), so without
    this wrapper the second bind crashes ``initialize()``.
    """

    def _create_idempotent(sync_conn, table) -> None:
        _run_ddl_idempotent(
            sync_conn,
            lambda: table.create(sync_conn, checkfirst=True),
            lambda: inspect(sync_conn).has_table(table.name),
        )

    task_model = _get_task_model()
    dep_model = _get_task_dependency_model()
    message_model = _get_message_model()
    read_status_model = _get_message_read_status_model()
    review_vote_model = _get_review_vote_model()
    conn = await session.connection()
    # Snapshot which migration-candidate tables already exist *before* create
    # runs. ``create(checkfirst=True)`` leaves a pre-existing table untouched,
    # so only those need migration; freshly-created ones are on the current
    # scheme and can be skipped without a single ``get_indexes`` PRAGMA call.
    candidates = _current_session_migration_tables()
    existing_before_create = await conn.run_sync(_snapshot_existing_tables, candidates)
    for model in (task_model, dep_model, message_model, read_status_model, review_vote_model):
        await conn.run_sync(_create_idempotent, model.__table__)
    await conn.run_sync(_ensure_message_protocol_column)
    await conn.run_sync(_ensure_message_meta_column)
    await conn.run_sync(_ensure_dynamic_table_indexes, candidates, existing_before_create)


# Preserve the shared DDL retry/drop layout while develop updates its write protocol.
# fmt: off
async def create_cur_session_tables(sessions: DbSessions) -> bool:
    """Create dynamic tables for current session under the DDL + write lock.

    Runs inside ``DbSessions.write()`` **and** holds ``sessions.ddl_lock()``.
    On SQLite the per-write lock serialises bind-time DDL with every DAO
    write of this instance; on PostgreSQL / MySQL ``write()`` skips the
    process-level lock (the DB is the write arbiter), so DDL-vs-DAO-write
    is guarded by the DB's own table-level DDL lock
    (AccessExclusiveLock / metadata lock) — a DROP blocks until in-flight
    writes commit, and a write that starts after the DROP completes sees
    "table does not exist". ``ddl_lock()`` adds DDL-vs-DDL protection on
    all backends: two concurrent binds of the same instance's tables
    cannot interleave. The engine-level variant (``engine.begin()``)
    bypassed both ``ddl_lock()`` and ``write()``: two teammates binding
    concurrently could hold BOTH write-pool connections while their DDL
    parked on the SQLite file lock, exhausting the 2-connection pool
    (``QueuePool limit of size 2`` teammate crashes).

    The session_id is obtained from context via get_session_id().

    Retries on transient errors via ``retry_on_transient``: SQLite write
    contention (``OperationalError``) and pool-exhaustion (``SATimeoutError``)
    as ``retry_on_locked`` does, *plus* the ``asyncio.timeout`` watchdog
    ``TimeoutError`` raised by ``DbSessions.write()`` when the driver thread
    is wedged (e.g. multi-process binds racing the file lock at startup; at
    most one watchdog retry). A failed retry budget does not raise here:
    ``create_cur_session_tables`` returns ``True`` when the tables are ready
    and ``False`` when no session is bound or the retry budget is exhausted.
    Callers that start a session runtime must check the result before
    proceeding — ``session_manager`` aborts the bind on ``False``.
    """
    session_id = get_session_id()
    if not session_id:
        team_logger.warning("No session_id in context, cannot create session tables")
        return False

    async def _ddl_once() -> None:
        async with sessions.ddl_lock():
            async with sessions.write() as session:
                await _ensure_session_tables(session)
                await session.commit()

    try:
        await retry_on_transient(_ddl_once, label=f"create_cur_session_tables(session={session_id})")
    except _TRANSIENT_DB_ERRORS:
        # Budget exhausted (already logged by retry_on_transient) — report it
        # via the ``False`` contract: callers that start a session runtime
        # (``session_manager``) abort the bind instead of letting a member run
        # against tables that never got created.
        return False
    team_logger.info("Session tables ready for session %s", session_id)
    return True


async def drop_cur_session_tables(sessions: DbSessions) -> None:
    """Drop dynamic tables for current session under the DDL + write lock.

    Runs inside ``DbSessions.write()`` **and** ``sessions.ddl_lock()``.
    On SQLite the per-write lock serialises the drop with every DAO write
    of this instance; on PostgreSQL / MySQL ``write()`` skips the
    process-level lock, so DDL-vs-DAO-write is guarded by the DB's own
    table-level DDL lock — a DROP blocks until in-flight writes commit,
    and a write that starts after the DROP completes sees
    "table does not exist". ``ddl_lock()`` serialises the drop with
    ``create_cur_session_tables`` and every other DDL helper on all
    backends. A bare ``engine.begin()`` bypassed both and let a drop
    interleave with an in-flight bind or write.

    The session_id is obtained from context via get_session_id().
    """
    if sessions is None:
        return

    session_id = get_session_id()
    if not session_id:
        team_logger.warning("No session_id in context, cannot drop session tables")
        return

    task_model = _get_task_model()
    dep_model = _get_task_dependency_model()
    message_model = _get_message_model()
    read_status_model = _get_message_read_status_model()
    review_vote_model = _get_review_vote_model()

    async with sessions.ddl_lock():
        async with sessions.write() as session:
            conn = await session.connection()
            for model in (task_model, dep_model, message_model, read_status_model, review_vote_model):
                await conn.run_sync(model.__table__.drop, checkfirst=True)
            await session.commit()

    team_logger.info("Dropped dynamic tables for session %s", session_id)


# fmt: on
async def cleanup_all_runtime_state(
    sessions: DbSessions,
) -> tuple[list[str], list[str]]:
    """Delete all dynamic team tables and clear static team tables.

    This cleanup is storage-level and does not depend on an active
    agent instance or the current session context. Runs inside
    ``DbSessions.write()`` **and** ``sessions.ddl_lock()``. On SQLite
    the per-write lock serialises the cleanup with every DAO write of
    this instance; on PostgreSQL / MySQL ``write()`` skips the
    process-level lock, so DDL-vs-DAO-write is guarded by the DB's own
    table-level DDL lock. ``ddl_lock()`` serialises the cleanup with
    every other DDL helper on all backends. Same shape as
    ``drop_cur_session_tables``.
    """
    if sessions is None:
        return [], []

    deleted_tables: list[str] = []
    cleared_tables: list[str] = []
    async with sessions.ddl_lock():
        async with sessions.write() as session:
            conn = await session.connection()
            table_names = await conn.run_sync(_get_table_names)

            for table_name in table_names:
                if not table_name.startswith(TEAM_DYNAMIC_TABLE_PREFIXES):
                    continue
                await conn.run_sync(_drop_table, table_name)
                deleted_tables.append(table_name)

            for table_name in TEAM_STATIC_TABLES_TO_CLEAR:
                if table_name not in table_names:
                    continue
                await conn.run_sync(_clear_table, table_name)
                cleared_tables.append(table_name)
            await session.commit()

    team_logger.info(
        "Cleaned team runtime state: deleted dynamic tables={}, cleared static tables={}",
        deleted_tables,
        cleared_tables,
    )
    return deleted_tables, cleared_tables


async def drop_session_tables_by_id(sessions: DbSessions, session_id: str) -> list[str]:
    """Drop dynamic tables for a specific session_id without context.

    This is used by Runner.release(session_id) to clean up per-session
    tables when the session context is not active (e.g. after the agent
    has finished executing). Runs inside ``DbSessions.write()`` **and**
    ``sessions.ddl_lock()``. On SQLite the per-write lock serialises
    the drop with every DAO write of this instance; on PostgreSQL /
    MySQL ``write()`` skips the process-level lock, so DDL-vs-DAO-write
    is guarded by the DB's own table-level DDL lock. ``ddl_lock()``
    serialises the drop with every other DDL helper on all backends.
    Same shape as ``drop_cur_session_tables``.

    Args:
        sessions: Read/write session provider of the database.
        session_id: Session identifier to clean up.

    Returns:
        List of dropped table names.
    """
    if sessions is None or not session_id:
        return []

    suffix = _sanitize_session_id_for_table(session_id)
    dropped: list[str] = []
    async with sessions.ddl_lock():
        async with sessions.write() as session:
            conn = await session.connection()
            table_names = await conn.run_sync(_get_table_names)
            for prefix in TEAM_DYNAMIC_TABLE_PREFIXES:
                expected_table = f"{prefix}{suffix}"
                if expected_table in table_names:
                    await conn.run_sync(_drop_table, expected_table)
                    dropped.append(expected_table)
            await session.commit()

    if dropped:
        team_logger.info("Dropped session tables for session %s: %s", session_id, dropped)
    return dropped
