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
from sqlalchemy.exc import OperationalError
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
    _get_task_dependency_model,
    _get_task_model,
    _sanitize_session_id_for_table,
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


class DbSessions:
    """Read/write session provider with process-wide write serialisation.

    SQLite permits a single writer at a time (a database-level lock), so a
    process-wide ``asyncio.Lock`` funnels every write through one logical
    writer slot — writers never contend on the SQLite lock. For file-backed
    SQLite the reads and writes run on *separate* engines (a dedicated
    single-writer pool + a larger reader pool), so a slow write never starves
    reader-connection availability. For ``:memory:`` and PostgreSQL / MySQL the
    two factories are the same object (no split), and ``read()`` / ``write()``
    still differ only by whether the write lock is held.
    """

    def __init__(
        self,
        write_session_local: async_sessionmaker,
        read_session_local: async_sessionmaker | None = None,
    ) -> None:
        """Bind the write / read session factories and create the write lock.

        Args:
            write_session_local: Session factory bound to the writer engine.
            read_session_local: Session factory bound to the reader engine.
                Defaults to ``write_session_local`` when no split exists
                (``:memory:`` / PostgreSQL / MySQL).
        """
        self._write_session_local = write_session_local
        self._read_session_local = read_session_local or write_session_local
        self._write_lock = asyncio.Lock()

    @asynccontextmanager
    async def read(self) -> AsyncIterator[AsyncSession]:
        """Yield a session for read-only work; the write lock is not held."""
        async with self._read_session_local() as session:
            yield session

    @asynccontextmanager
    async def write(self) -> AsyncIterator[AsyncSession]:
        """Yield a session while holding the process-wide write lock.

        The lock is non-reentrant: a write must not open a nested
        ``write()`` or it will deadlock. Compose multi-step writes inside a
        single ``write()`` block, and keep the lock on the outermost public
        method when one write helper delegates to another.
        """
        async with self._write_lock:
            async with self._write_session_local() as session:
                yield session


async def retry_on_locked(
    op: Callable[[], Awaitable[_T]],
    *,
    on_locked_result: _T,
    label: str,
) -> _T:
    """Run a write ``op`` with exponential back-off on ``database is locked``.

    The back-off sleep runs *between* ``op`` calls, never inside the session
    ``op`` opens: ``op`` must fully exit its ``async with write()`` block
    (releasing the connection) before returning, so a retry wait never pins
    a checked-out connection and starves the pool.

    Args:
        op: Zero-arg coroutine factory performing one write attempt.
        on_locked_result: Value returned after the final failed attempt.
        label: Human-readable operation name for logs.

    Returns:
        The ``op`` result, or ``on_locked_result`` when every attempt hit a
        locked database.
    """
    for attempt in range(_DB_RETRY_ATTEMPTS):
        try:
            return await op()
        except OperationalError as e:
            if attempt >= _DB_RETRY_ATTEMPTS - 1:
                team_logger.error("%s failed after %d attempts: %s", label, _DB_RETRY_ATTEMPTS, e)
                return on_locked_result
            delay = _DB_RETRY_BASE_DELAY * (2**attempt)
            team_logger.warning("%s hit a locked DB (attempt %d), retrying in %ss", label, attempt + 1, delay)
            await asyncio.sleep(delay)
    return on_locked_result


def _get_table_names(sync_conn) -> list[str]:
    """Return all table names currently present in the database."""
    return list(inspect(sync_conn).get_table_names())


def _drop_table(sync_conn, table_name: str) -> None:
    """Drop one table with raw SQL to avoid reflection-order issues."""
    quoted_name = table_name.replace('"', '""')
    sync_conn.exec_driver_sql(f'DROP TABLE IF EXISTS "{quoted_name}"')


def _clear_table(sync_conn, table_name: str) -> None:
    """Delete all rows from one reflected table."""
    quoted_name = table_name.replace('"', '""')
    sync_conn.exec_driver_sql(f'DELETE FROM "{quoted_name}"')


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
    sync_conn.exec_driver_sql(f"ALTER TABLE team_member ADD COLUMN role TEXT NOT NULL DEFAULT '{default_role}'")
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
    if "options" not in columns:
        sync_conn.exec_driver_sql("ALTER TABLE team_member ADD COLUMN options TEXT")
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

    sync_conn.exec_driver_sql("ALTER TABLE team_member DROP COLUMN model_ref_json")
    team_logger.info("Migrated legacy team_member table: dropped model_ref_json column")


def _ensure_message_protocol_column(sync_conn) -> None:
    """Backfill the ``protocol`` column on pre-existing per-session message tables.

    ``SQLModel.metadata.create_all`` only creates tables that don't exist;
    it never alters live tables.  Per-session message tables created before
    the ``protocol`` column was introduced therefore lack it after upgrading.
    Probe each message table and run ``ALTER TABLE ... ADD COLUMN`` with a
    default of ``"plain"`` so legacy rows retain their original semantics.
    """
    inspector = inspect(sync_conn)
    for table_name in inspector.get_table_names():
        if not table_name.startswith("team_message_"):
            continue
        columns = {col["name"] for col in inspector.get_columns(table_name)}
        if "protocol" in columns:
            continue
        sync_conn.exec_driver_sql(
            f"ALTER TABLE {table_name} ADD COLUMN protocol TEXT NOT NULL DEFAULT 'plain'"
        )
        team_logger.info(
            "Migrated legacy message table %s: added protocol column",
            table_name,
        )




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
) -> AsyncEngine:
    """Build one file-backed SQLite engine with pool + PRAGMA tuning.

    Used to build both the single-writer engine and the reader engine over the
    same file. ``max_overflow=0`` keeps checkout timeouts surfacing real
    session leaks instead of masking them; ``pool_pre_ping`` is dropped because
    a local SQLite file connection does not silently die like a network socket.
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
        wal_autocheckpoint=config.wal_autocheckpoint,
    )
    return engine


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
            db_path = Path(conn_str).expanduser()
            conn_str = str(db_path)
            if not db_path.parent.exists():
                db_path.parent.mkdir(parents=True, exist_ok=True)
            write_engine = _build_file_sqlite_engine(
                conn_str, config, pool_size=config.write_pool_size, cache_size_kb=config.write_cache_size_kb
            )
            read_engine = _build_file_sqlite_engine(
                conn_str, config, pool_size=config.read_pool_size, cache_size_kb=config.read_cache_size_kb
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
    async with write_engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
        await conn.run_sync(_ensure_team_member_role_column)
        await conn.run_sync(_ensure_team_member_options_column)

    return SqlEngines(write_engine, read_engine, write_session_local, read_session_local)


async def create_cur_session_tables(engine: AsyncEngine) -> None:
    """Create dynamic tables for current session.

    The session_id is obtained from context via get_session_id().
    """
    if engine is None:
        return

    session_id = get_session_id()
    if not session_id:
        team_logger.warning("No session_id in context, cannot create session tables")
        return

    task_model = _get_task_model()
    dep_model = _get_task_dependency_model()
    message_model = _get_message_model()
    read_status_model = _get_message_read_status_model()

    async with engine.begin() as conn:
        for model in (task_model, dep_model, message_model, read_status_model):
            await conn.run_sync(model.__table__.create, checkfirst=True)
        await conn.run_sync(_ensure_message_protocol_column)

    team_logger.info("Session tables ready for session %s", session_id)


async def drop_cur_session_tables(engine: AsyncEngine) -> None:
    """Drop dynamic tables for current session.

    The session_id is obtained from context via get_session_id().
    """
    if engine is None:
        return

    session_id = get_session_id()
    if not session_id:
        team_logger.warning("No session_id in context, cannot drop session tables")
        return

    task_model = _get_task_model()
    dep_model = _get_task_dependency_model()
    message_model = _get_message_model()
    read_status_model = _get_message_read_status_model()

    async with engine.begin() as conn:
        for model in (task_model, dep_model, message_model, read_status_model):
            await conn.run_sync(model.__table__.drop, checkfirst=True)

    team_logger.info("Dropped dynamic tables for session %s", session_id)


async def cleanup_all_runtime_state(
    engine: AsyncEngine,
) -> tuple[list[str], list[str]]:
    """Delete all dynamic team tables and clear static team tables.

    This cleanup is storage-level and does not depend on an active
    agent instance or the current session context.
    """
    if engine is None:
        return [], []

    deleted_tables: list[str] = []
    cleared_tables: list[str] = []
    async with engine.begin() as conn:
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

    team_logger.info(
        "Cleaned team runtime state: deleted dynamic tables={}, cleared static tables={}",
        deleted_tables,
        cleared_tables,
    )
    return deleted_tables, cleared_tables


async def drop_session_tables_by_id(engine: AsyncEngine, session_id: str) -> list[str]:
    """Drop dynamic tables for a specific session_id without context.

    This is used by Runner.release(session_id) to clean up per-session
    tables when the session context is not active (e.g. after the agent
    has finished executing).

    Args:
        engine: Database engine.
        session_id: Session identifier to clean up.

    Returns:
        List of dropped table names.
    """
    if engine is None or not session_id:
        return []

    suffix = _sanitize_session_id_for_table(session_id)
    dropped: list[str] = []
    async with engine.begin() as conn:
        table_names = await conn.run_sync(_get_table_names)
        for prefix in TEAM_DYNAMIC_TABLE_PREFIXES:
            expected_table = f"{prefix}{suffix}"
            if expected_table in table_names:
                await conn.run_sync(_drop_table, expected_table)
                dropped.append(expected_table)

    if dropped:
        team_logger.info("Dropped session tables for session %s: %s", session_id, dropped)
    return dropped
