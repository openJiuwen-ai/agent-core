# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Database engine initialization, session management, and table lifecycle."""

import asyncio
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager, nullcontext
from pathlib import Path
from typing import NamedTuple, TypeVar

from sqlalchemy import event, inspect
from sqlalchemy.exc import NoSuchTableError, OperationalError, ProgrammingError
from sqlalchemy.exc import TimeoutError as SATimeoutError
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import AsyncAdaptedQueuePool, StaticPool
from sqlalchemy.sql import quoted_name
from sqlmodel import SQLModel

from openjiuwen.agent_teams.context import get_session_id
from openjiuwen.agent_teams.tools.database.config import DatabaseConfig, DatabaseType
from openjiuwen.agent_teams.tools.models import (
    TEAM_DYNAMIC_TABLE_PREFIXES,
    TEAM_MESSAGE_LONG_TEXT_SPECS,
    TEAM_REVIEW_VOTE_LONG_TEXT_SPECS,
    TEAM_STATIC_LONG_TEXT_SPECS,
    TEAM_STATIC_TABLES_TO_CLEAR,
    TEAM_TASK_LONG_TEXT_SPECS,
    _get_group_notification_model,
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
_DB_SESSION_WATCHDOG_SECONDS = 30.0

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


class DbSessions:
    """Read/write session provider with process-wide write serialisation.

    SQLite permits a single writer at a time (a database-level lock), so a
    process-wide ``asyncio.Lock`` funnels every write through one logical
    writer slot — writers never contend on the SQLite lock. For file-backed
    SQLite the reads and writes run on *separate* engines (a dedicated
    single-writer pool + a larger reader pool), so a slow write never starves
    reader-connection availability. In-memory SQLite shares one connection,
    so readers also take the lock before opening a session. PostgreSQL and
    MySQL use separate pooled connections even with a shared factory.
    """

    def __init__(
        self,
        write_session_local: async_sessionmaker,
        read_session_local: async_sessionmaker | None = None,
        *,
        single_connection: bool = False,
    ) -> None:
        """Bind the write / read session factories and create the write lock.

        Args:
            write_session_local: Session factory bound to the writer engine.
            read_session_local: Session factory bound to the reader engine.
                Defaults to ``write_session_local`` when no split exists
                (``:memory:`` / PostgreSQL / MySQL).
            single_connection: Serialize reads and writes that share one connection.
        """
        self._write_session_local = write_session_local
        self._read_session_local = read_session_local or write_session_local
        self._write_lock = asyncio.Lock()
        self._single_connection = single_connection

    @property
    def write_locked(self) -> bool:
        """Whether a ``write()`` block currently holds the write lock (diagnostics)."""
        return self._write_lock.locked()

    @asynccontextmanager
    async def read(self) -> AsyncIterator[AsyncSession]:
        """Yield a read session, taking the shared lock for in-memory SQLite.

        Closing a reader on the shared SQLite connection can roll back a
        writer's transaction. Hold the lock through session cleanup. The
        watchdog covers the session block, excluding time spent waiting for
        the connection lock.
        """
        connection_guard = self._write_lock if self._single_connection else nullcontext()
        async with connection_guard:
            try:
                async with asyncio.timeout(_DB_SESSION_WATCHDOG_SECONDS):
                    async with self._read_session_local() as session:
                        yield session
            except TimeoutError:
                team_logger.warning(
                    "DbSessions.read() exceeded the %.0fs watchdog; the DB driver may be wedged",
                    _DB_SESSION_WATCHDOG_SECONDS,
                )
                raise

    @asynccontextmanager
    async def write(self) -> AsyncIterator[AsyncSession]:
        """Yield a session while holding the process-wide write lock.

        The lock is non-reentrant: a write must not open a nested
        ``write()`` or it will deadlock. Compose multi-step writes inside a
        single ``write()`` block, and keep the lock on the outermost public
        method when one write helper delegates to another.

        The session block (not the lock wait — writers legitimately queue
        behind each other) runs under ``_DB_SESSION_WATCHDOG_SECONDS`` so a
        wedged driver surfaces as a loud ``TimeoutError``.
        """
        async with self._write_lock:
            try:
                async with asyncio.timeout(_DB_SESSION_WATCHDOG_SECONDS):
                    async with self._write_session_local() as session:
                        yield session
            except TimeoutError:
                team_logger.warning(
                    "DbSessions.write() exceeded the %.0fs watchdog; the DB driver may be wedged",
                    _DB_SESSION_WATCHDOG_SECONDS,
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


def _quote_ident(sync_conn, name: str) -> str:
    """Quote an identifier with the connection's SQL dialect rules.

    SQLite / PostgreSQL use double quotes; MySQL / MariaDB use backticks.
    Always quote via ``quoted_name(..., quote=True)`` so safe names still get
    dialect markers — MySQL rejects hand-written ``"..."`` (error 1064) when
    ANSI_QUOTES is off.
    """
    return sync_conn.dialect.identifier_preparer.quote(quoted_name(name, quote=True))


def _is_mysql_family(sync_conn) -> bool:
    """True for MySQL and MariaDB dialects (same DDL quirks for indexes/text)."""
    return sync_conn.dialect.name in ("mysql", "mariadb")


def _dialect_long_text_ddl(sync_conn) -> str:
    """DDL type for unbounded text on the current dialect."""
    if _is_mysql_family(sync_conn):
        return "LONGTEXT"
    return "TEXT"


def _column_is_bounded_string(col_type) -> bool:
    """True when the live column is a length-capped string (e.g. VARCHAR(255))."""
    return getattr(col_type, "length", None) is not None


def _widen_text_columns(
    sync_conn,
    table_name: str,
    columns: dict,
    specs: tuple[tuple[str, bool], ...],
) -> None:
    """MODIFY length-capped string columns to LONGTEXT/TEXT on MySQL/MariaDB.

    SQLite affinity already accepts long values; no ALTER is required there.
    ``specs`` is ``(column_name, nullable)`` pairs matching the model.
    """
    if not _is_mysql_family(sync_conn):
        return
    q_table = _quote_ident(sync_conn, table_name)
    text_ddl = _dialect_long_text_ddl(sync_conn)
    for column_name, nullable in specs:
        col = columns.get(column_name)
        if col is None or not _column_is_bounded_string(col.get("type")):
            continue
        q_col = _quote_ident(sync_conn, column_name)
        null_sql = "NULL" if nullable else "NOT NULL"
        sync_conn.exec_driver_sql(
            f"ALTER TABLE {q_table} MODIFY {q_col} {text_ddl} {null_sql}"
        )
        team_logger.info(
            "Migrated %s: widened %s to %s",
            table_name,
            column_name,
            text_ddl,
        )


def _mysql_errno(exc: BaseException) -> int | None:
    """Server errno from ``exc.orig.args[0]``, or None when the driver omitted it.

    aiomysql/PyMySQL store the code on the DBAPI exception. The SQLAlchemy
    wrapper's own ``args[0]`` is the SQL text, so it is not read here.
    """
    orig = getattr(exc, "orig", None)
    args = getattr(orig, "args", ())
    if not args or isinstance(args[0], bool) or not isinstance(args[0], int):
        return None
    return args[0]


def _is_benign_mysql_index_error(exc: BaseException, *, missing: bool) -> bool:
    """True when a MySQL/MariaDB index race has already reached the goal state.

    ``missing=True`` is errno 1091: DROP of an index a peer already removed.
    ``missing=False`` is errno 1061: CREATE of a name a peer already created.
    Errno 1553 (index still required by a foreign key) is *not* benign here —
    the index remains; see ``_is_mysql_fk_retained_index_error``. Any other
    code (1062 duplicate row, 1050 table exists, 1146 unknown table) stays
    fatal. Statement text is used only when no errno is present: a DROP
    matches ``check that`` plus ``exists`` (MySQL ``column/key exists`` and
    MariaDB ``check that it exists``); a CREATE matches ``Duplicate key name``.
    """
    errno = _mysql_errno(exc)
    if errno is not None:
        return errno == (1091 if missing else 1061)
    orig = getattr(exc, "orig", None)
    folded = str(orig if orig is not None else exc).casefold()
    if missing:
        return "check that" in folded and "exists" in folded
    return "duplicate key name" in folded


def _is_mysql_fk_retained_index_error(exc: BaseException) -> bool:
    """True when InnoDB refuses DROP because a foreign key still needs the index.

    Errno 1553: ``Cannot drop index ... needed in a foreign key constraint``.
    Without an errno, require both ``foreign key`` and ``needed`` in the
    server text. The index must stay; A1 cannot remove it while the FK remains.
    """
    errno = _mysql_errno(exc)
    if errno is not None:
        return errno == 1553
    orig = getattr(exc, "orig", None)
    folded = str(orig if orig is not None else exc).casefold()
    return "foreign key" in folded and "needed" in folded


def _exec_mysql_index_ddl(sync_conn, sql: str, *, missing: bool) -> bool:
    """Run MySQL-family index DDL; return whether the goal state was reached.

    MySQL 8.0 rejects ``IF [NOT] EXISTS`` on these statements. A concurrent
    binder that already finished the same step raises ``OperationalError``
    (aiomysql) or, on some drivers, ``ProgrammingError``.

    Returns ``True`` when the index is absent after DROP (or present after
    CREATE), including the 1091 / 1061 races. Returns ``False`` only for DROP
    when errno 1553 keeps the index for a foreign key.
    """
    try:
        sync_conn.exec_driver_sql(sql)
        return True
    except (OperationalError, ProgrammingError) as exc:
        if missing and _is_mysql_fk_retained_index_error(exc):
            return False
        if _is_benign_mysql_index_error(exc, missing=missing):
            return True
        raise


def _drop_index_if_present(
    sync_conn,
    table_name: str,
    index_name: str,
    index_names: set[str],
) -> bool:
    """Drop ``index_name`` when it appears in ``index_names``.

    The snapshot skips a useless DROP. MySQL/MariaDB have no
    ``DROP INDEX IF EXISTS`` and use ``DROP INDEX ... ON table``; a peer that
    already dropped it (errno 1091) counts as success. InnoDB errno 1553
    (index still required by a foreign key) leaves the index in place and
    returns ``False`` so callers do not log a successful drop — A1 cannot
    remove that covering index while the FK remains. SQLite / PostgreSQL
    keep ``DROP INDEX IF EXISTS``. Returns whether the index is gone afterward
    (dropped here or already absent via race).
    """
    if index_name not in index_names:
        return False
    q_idx = _quote_ident(sync_conn, index_name)
    if _is_mysql_family(sync_conn):
        q_table = _quote_ident(sync_conn, table_name)
        dropped = _exec_mysql_index_ddl(
            sync_conn, f"DROP INDEX {q_idx} ON {q_table}", missing=True
        )
        if not dropped:
            team_logger.info(
                "Retained index %s on %s: still required by a foreign key",
                index_name,
                table_name,
            )
        return dropped
    sync_conn.exec_driver_sql(f"DROP INDEX IF EXISTS {q_idx}")
    return True


def _create_index_if_absent(
    sync_conn,
    table_name: str,
    index_name: str,
    columns_sql: str,
    index_names: set[str],
) -> bool:
    """Create ``index_name`` when it is missing from ``index_names``.

    The snapshot skips a useless CREATE. MySQL/MariaDB have no
    ``CREATE INDEX IF NOT EXISTS``; a peer that already created the name
    (errno 1061) counts as success. SQLite / PostgreSQL keep statement-level
    ``IF NOT EXISTS``. Returns whether a CREATE was issued (or the name
    already exists after a race).
    """
    if index_name in index_names:
        return False
    q_idx = _quote_ident(sync_conn, index_name)
    q_table = _quote_ident(sync_conn, table_name)
    if _is_mysql_family(sync_conn):
        return _exec_mysql_index_ddl(
            sync_conn,
            f"CREATE INDEX {q_idx} ON {q_table} ({columns_sql})",
            missing=False,
        )
    sync_conn.exec_driver_sql(
        f"CREATE INDEX IF NOT EXISTS {q_idx} ON {q_table} ({columns_sql})"
    )
    return True


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
    q_table = _quote_ident(sync_conn, table_name)
    sync_conn.exec_driver_sql(f"DROP TABLE IF EXISTS {q_table}")


def _clear_table(sync_conn, table_name: str) -> None:
    """Delete all rows from one reflected table."""
    q_table = _quote_ident(sync_conn, table_name)
    sync_conn.exec_driver_sql(f"DELETE FROM {q_table}")


def _create_static_tables(sync_conn) -> None:
    """Create the static team-scoped schema, and only that.

    Scoped to ``static_tables()`` rather than the whole ``SQLModel.metadata``:
    the metadata registry also holds the per-session tables of every session
    the process has bound so far, which belong to
    ``create_cur_session_tables()`` and must not leak into an unrelated
    database. Creating them here would also make schema setup cost grow with
    the process's session history instead of with the schema itself.
    """
    SQLModel.metadata.create_all(sync_conn, tables=static_tables())


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
    q_table = _quote_ident(sync_conn, "team_member")
    q_col = _quote_ident(sync_conn, "role")
    sync_conn.exec_driver_sql(
        f"ALTER TABLE {q_table} ADD COLUMN {q_col} TEXT NOT NULL DEFAULT '{default_role}'"
    )
    team_logger.info(
        "Migrated legacy team_member table: added role column with default %s",
        default_role,
    )


def _ensure_team_member_options_column(sync_conn) -> None:
    """Ensure TeamMember.options exists, backfill it, and drop legacy columns."""
    inspector = inspect(sync_conn)
    if "team_member" not in inspector.get_table_names():
        return
    columns = {col["name"] for col in inspector.get_columns("team_member")}
    q_table = _quote_ident(sync_conn, "team_member")
    if "options" not in columns:
        q_col = _quote_ident(sync_conn, "options")
        text_ddl = _dialect_long_text_ddl(sync_conn)
        sync_conn.exec_driver_sql(f"ALTER TABLE {q_table} ADD COLUMN {q_col} {text_ddl}")
        team_logger.info("Migrated legacy team_member table: added options column")

    if "model_ref_json" not in columns:
        return

    from openjiuwen.agent_teams.tools.member_options import merge_legacy_member_options

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

    q_legacy = _quote_ident(sync_conn, "model_ref_json")
    sync_conn.exec_driver_sql(f"ALTER TABLE {q_table} DROP COLUMN {q_legacy}")
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
    q_table = _quote_ident(sync_conn, "team_info")
    if "dispatch_mode" not in columns:
        q_col = _quote_ident(sync_conn, "dispatch_mode")
        sync_conn.exec_driver_sql(
            f"ALTER TABLE {q_table} ADD COLUMN {q_col} TEXT NOT NULL DEFAULT 'autonomous'"
        )
        team_logger.info("Migrated legacy team_info table: added dispatch_mode column")
    if "enable_task_verification" not in columns:
        q_col = _quote_ident(sync_conn, "enable_task_verification")
        sync_conn.exec_driver_sql(
            f"ALTER TABLE {q_table} ADD COLUMN {q_col} BOOLEAN NOT NULL DEFAULT 0"
        )
        team_logger.info("Migrated legacy team_info table: added enable_task_verification column")


def _ensure_team_static_long_text_columns(sync_conn) -> None:
    """Widen static-table body columns that used to be VARCHAR(255).

    Specs live in ``TEAM_STATIC_LONG_TEXT_SPECS`` (models.py). Same widening
    path as per-session task / message long fields.
    """
    inspector = inspect(sync_conn)
    table_names = set(inspector.get_table_names())
    for table_name, specs in TEAM_STATIC_LONG_TEXT_SPECS.items():
        if table_name not in table_names:
            continue
        _widen_text_columns(
            sync_conn,
            table_name,
            {c["name"]: c for c in inspector.get_columns(table_name)},
            specs,
        )


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
    q_table = _quote_ident(sync_conn, table_name)
    q_col = _quote_ident(sync_conn, "protocol")
    sync_conn.exec_driver_sql(
        f"ALTER TABLE {q_table} ADD COLUMN {q_col} TEXT NOT NULL DEFAULT 'plain'"
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
    q_table = _quote_ident(sync_conn, table_name)
    q_col = _quote_ident(sync_conn, "meta")
    text_ddl = _dialect_long_text_ddl(sync_conn)
    sync_conn.exec_driver_sql(f"ALTER TABLE {q_table} ADD COLUMN {q_col} {text_ddl}")
    team_logger.info("Migrated legacy message table %s: added meta column", table_name)


def _current_session_migration_tables() -> list[str]:
    """The current session's migration-candidate dynamic table names.

    ``team_task_*`` / ``team_task_dependency_*`` / ``team_message_*`` get index
    rebuilds + long-text widen; ``team_review_vote_*`` only widens ``feedback``.
    ``message_read_status_*`` has no legacy-structure gap and stays excluded.

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
        f"team_review_vote_{suffix}",
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

    Index DROP/CREATE probes ``inspector.get_indexes`` so an up-to-date table
    skips DDL. MySQL/MariaDB have no ``IF [NOT] EXISTS`` on these statements;
    a concurrent peer's 1091/1061 is treated as success. InnoDB errno 1553
    (``team_name`` index still required by the FK) retains the index and does
    not fail the bind — A1 cannot drop that covering index while the FK stays.
    SQLite / PostgreSQL keep ``IF [NOT] EXISTS``. Identifiers are dialect-quoted
    so MySQL gets backticks instead of hard-coded double quotes.

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
        is_review_vote = table_name.startswith("team_review_vote_")
        if not (is_message or is_dependency or is_task or is_review_vote):
            continue
        if table_name not in existing_before_create:
            # Created fresh this bind → already on the current scheme; touching
            # it would only burn a ``get_indexes``/``get_columns`` PRAGMA lock.
            continue

        if is_review_vote:
            # Widen only — review-vote tables have no legacy index rebuild.
            # Skip get_columns on non-MySQL: widen is a no-op there.
            if _is_mysql_family(sync_conn):
                vote_column_defs = {
                    c["name"]: c for c in inspector.get_columns(table_name)
                }
                _widen_text_columns(
                    sync_conn,
                    table_name,
                    vote_column_defs,
                    TEAM_REVIEW_VOTE_LONG_TEXT_SPECS,
                )
            continue

        q_table = _quote_ident(sync_conn, table_name)
        index_names = {idx["name"] for idx in inspector.get_indexes(table_name)}

        team_name_index = f"ix_{table_name}_team_name"
        if _drop_index_if_present(sync_conn, table_name, team_name_index, index_names):
            team_logger.info("Migrated %s: dropped dead team_name index", table_name)

        if is_task:
            legacy_task_indexes = [f"ix_{table_name}_assignee", f"ix_{table_name}_updated_at"]
            assignee_status_index = f"ix_{table_name}_assignee_status"
            needs_task_migration = (
                bool(index_names & set(legacy_task_indexes)) or assignee_status_index not in index_names
            )
            if needs_task_migration:
                for legacy in legacy_task_indexes:
                    _drop_index_if_present(sync_conn, table_name, legacy, index_names)
                _create_index_if_absent(
                    sync_conn,
                    table_name,
                    assignee_status_index,
                    "assignee, status",
                    index_names,
                )
                team_logger.info(
                    "Migrated task table %s: folded assignee into (assignee, status), dropped dead updated_at index",
                    table_name,
                )

            # One get_columns for ADD probes and (on MySQL) widen — avoid a
            # second reflection solely for LONGTEXT MODIFY.
            task_column_defs = {c["name"]: c for c in inspector.get_columns(table_name)}
            task_columns = set(task_column_defs)
            text_ddl = _dialect_long_text_ddl(sync_conn)

            # F_59: add the verify-gate ``reviewer`` column to pre-existing tables.
            if "reviewer" not in task_columns:
                q_col = _quote_ident(sync_conn, "reviewer")
                sync_conn.exec_driver_sql(
                    f"ALTER TABLE {q_table} ADD COLUMN {q_col} {text_ddl}"
                )
                team_logger.info("Migrated task table %s: added reviewer column", table_name)

            # F_62: review voting — round counter + per-task round ceiling.
            if "review_round" not in task_columns:
                sync_conn.exec_driver_sql(
                    f"ALTER TABLE {q_table} ADD COLUMN review_round INTEGER NOT NULL DEFAULT 0"
                )
                team_logger.info("Migrated task table %s: added review_round column", table_name)
            if "max_review_rounds" not in task_columns:
                sync_conn.exec_driver_sql(f"ALTER TABLE {q_table} ADD COLUMN max_review_rounds INTEGER")
                team_logger.info("Migrated task table %s: added max_review_rounds column", table_name)

            # F_118: author's completion note, read by handoff briefs / view_task.
            if "note" not in task_columns:
                sync_conn.exec_driver_sql(f"ALTER TABLE {q_table} ADD COLUMN note {text_ddl}")
                team_logger.info("Migrated task table %s: added note column", table_name)

            # F_119: author's one-line completion summary, read by digests / briefs.
            if "summary" not in task_columns:
                sync_conn.exec_driver_sql(f"ALTER TABLE {q_table} ADD COLUMN summary {text_ddl}")
                team_logger.info("Migrated task table %s: added summary column", table_name)

            # F_128: member subtasks — parent link + creator.
            if "parent_task_id" not in task_columns:
                sync_conn.exec_driver_sql(f"ALTER TABLE {q_table} ADD COLUMN parent_task_id TEXT")
                team_logger.info("Migrated task table %s: added parent_task_id column", table_name)
            if "created_by" not in task_columns:
                sync_conn.exec_driver_sql(f"ALTER TABLE {q_table} ADD COLUMN created_by TEXT")
                team_logger.info("Migrated task table %s: added created_by column", table_name)
            if "resume_pending" not in task_columns:
                sync_conn.exec_driver_sql(
                    f"ALTER TABLE {q_table} ADD COLUMN resume_pending BOOLEAN NOT NULL DEFAULT 0"
                )
                team_logger.info("Migrated task table %s: added resume_pending column", table_name)

            # F_129: task versioning + edit notes.
            for column in ("revision", "observed_revision", "parent_revision"):
                if column not in task_columns:
                    sync_conn.exec_driver_sql(
                        f"ALTER TABLE {q_table} ADD COLUMN {column} INTEGER NOT NULL DEFAULT 0"
                    )
                    team_logger.info("Migrated task table %s: added %s column", table_name, column)
            if "change_notes" not in task_columns:
                sync_conn.exec_driver_sql(
                    f"ALTER TABLE {q_table} ADD COLUMN change_notes {text_ddl}"
                )
                team_logger.info("Migrated task table %s: added change_notes column", table_name)

            # MySQL/MariaDB create() used to emit VARCHAR(255) for bare str
            # Fields. Widen existing short columns so long notes/content land.
            _widen_text_columns(
                sync_conn,
                table_name,
                task_column_defs,
                TEAM_TASK_LONG_TEXT_SPECS,
            )

            # F_59: fold the legacy execution states into the condition-named
            # IN_PROGRESS. claimed / started / plan_approved were all "a member
            # is executing it"; map them uniformly. Distinguishing an unapproved
            # plan-mode claim into PLANNING would need the plan index and is a
            # best-effort refinement deferred (rare, and the row is still
            # completable via the normal flow once re-owned).
            legacy_status_result = sync_conn.exec_driver_sql(
                f"UPDATE {q_table} SET status='in_progress' "
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

        if _is_mysql_family(sync_conn):
            message_column_defs = {
                c["name"]: c for c in inspector.get_columns(table_name)
            }
            _widen_text_columns(
                sync_conn,
                table_name,
                message_column_defs,
                TEAM_MESSAGE_LONG_TEXT_SPECS,
            )

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
            _drop_index_if_present(sync_conn, table_name, legacy, index_names)
        _create_index_if_absent(
            sync_conn,
            table_name,
            inbox_index,
            "to_member_name, is_read, timestamp",
            index_names,
        )
        _create_index_if_absent(
            sync_conn,
            table_name,
            bcast_index,
            "broadcast, timestamp",
            index_names,
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
            await conn.run_sync(_ensure_team_static_long_text_columns)
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
    """Create / ensure the per-session dynamic tables on an open session."""
    task_model = _get_task_model()
    dep_model = _get_task_dependency_model()
    message_model = _get_message_model()
    read_status_model = _get_message_read_status_model()
    review_vote_model = _get_review_vote_model()
    notification_model = _get_group_notification_model()
    conn = await session.connection()
    # Snapshot which migration-candidate tables already exist *before* create
    # runs. ``create(checkfirst=True)`` leaves a pre-existing table untouched,
    # so only those need migration; freshly-created ones are on the current
    # scheme and can be skipped without a single ``get_indexes`` PRAGMA call.
    candidates = _current_session_migration_tables()
    existing_before_create = await conn.run_sync(_snapshot_existing_tables, candidates)
    for model in (
        task_model, dep_model, message_model, read_status_model, review_vote_model, notification_model,
    ):
        await conn.run_sync(model.__table__.create, checkfirst=True)
    await conn.run_sync(_ensure_message_protocol_column)
    await conn.run_sync(_ensure_message_meta_column)
    await conn.run_sync(
        _ensure_dynamic_table_indexes, candidates, existing_before_create
    )


# Preserve the shared DDL retry/drop layout while develop updates its write protocol.
# fmt: off
async def create_cur_session_tables(sessions: DbSessions) -> bool:
    """Create dynamic tables for current session under the process-wide write lock.

    Runs inside ``DbSessions.write()`` so bind-time DDL serialises with every
    DAO write of this instance. The engine-level variant (``engine.begin()``)
    bypassed the lock: two teammates binding concurrently could hold BOTH
    write-pool connections while their DDL parked on the SQLite file lock,
    exhausting the 2-connection pool (``QueuePool limit of size 2`` teammate
    crashes).

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
    """Drop dynamic tables for current session under the process-wide write lock.

    Runs inside ``DbSessions.write()`` so the drop serialises with
    ``create_cur_session_tables`` and every DAO write of this instance; a bare
    ``engine.begin()`` let a drop interleave with an in-flight bind or write.

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
    notification_model = _get_group_notification_model()

    async with sessions.write() as session:
        conn = await session.connection()
        for model in (
            task_model, dep_model, message_model, read_status_model, review_vote_model, notification_model,
        ):
            await conn.run_sync(model.__table__.drop, checkfirst=True)
        await session.commit()

    team_logger.info("Dropped dynamic tables for session %s", session_id)


# fmt: on
async def cleanup_all_runtime_state(
    sessions: DbSessions,
) -> tuple[list[str], list[str]]:
    """Delete all dynamic team tables and clear static team tables.

    This cleanup is storage-level and does not depend on an active
    agent instance or the current session context. Runs under the
    ``DbSessions`` write lock, same as ``drop_cur_session_tables``.
    """
    if sessions is None:
        return [], []

    deleted_tables: list[str] = []
    cleared_tables: list[str] = []
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
    has finished executing). Runs under the ``DbSessions`` write lock,
    same as ``drop_cur_session_tables``.

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
