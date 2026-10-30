# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Database engine initialization, session management, and table lifecycle."""

import asyncio
import functools
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, NamedTuple, Optional, TypeVar

from sqlalchemy import event, inspect, text
from sqlalchemy.schema import CreateIndex, CreateTable
from sqlalchemy.exc import OperationalError, TimeoutError as SATimeoutError
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
    _clear_session_model_cache,
    _get_message_model,
    _get_message_read_status_model,
    _get_review_vote_model,
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

# Watchdog for one DbSessions block (connection checkout + every statement
# inside the ``async with``). The pool guards checkout with its own
# ``pool_timeout``, but a statement submitted to a wedged driver thread
# awaits its result with no timeout at all — a hang there is invisible and
# permanent. Team DB transactions are small (single-row reads / batched
# mailbox flushes), so 30s is far beyond any legitimate block; hitting it
# means the driver is stuck, and a loud TimeoutError beats an eternal hang.
_DB_SESSION_WATCHDOG_SECONDS = 30.0

# Slow-DDL warning threshold for bind-time session table creation (ms). The
# DDL runs under the process-wide write lock, so a slow run stalls every DAO
# write of the instance; healthy binds are tens of ms (fresh tables skip the
# migration scan entirely), so 2s is far past legitimate work and indicates
# lock-queue pile-up or an oversized legacy-table migration.
_DDL_SLOW_WARNING_MS = 2000.0


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
        # Per-session memo of completed bind-time DDL, scoped to THIS
        # DbSessions instance (i.e. this database): members bind 1+N times per
        # team spawn/recovery and the snapshot DB-fallback also calls
        # ``create_cur_session_tables`` — all but the first successful run per
        # session are pure write-lock overhead. ``_tables_inflight`` collapses
        # a concurrent first-bind burst onto a single leader run (followers
        # await its completion Event). Entries are dropped by every
        # table-drop path (via ``TeamDatabase`` methods); a degraded
        # (budget-exhausted) run is deliberately NOT memoised so the next
        # bind retries. Instance-scoped (not module-global) so a second
        # database in the same process can never inherit a stale "ready".
        # The memo value is the DB's ``schema_version`` at build time: SQLite
        # bumps that header cookie on ANY schema change from ANY process, so
        # a fast-path compare detects tables dropped by a *foreign* process
        # (the in-process drop hooks cannot see those). On backends without a
        # cheap schema cookie (PostgreSQL / MySQL) the fast path disables
        # itself: ``current_schema_version`` returns None and every bind
        # re-runs the idempotent DDL (pre-memo behaviour).
        # All access goes through the methods below — module-level helpers in
        # this file must not touch ``_tables_ready`` / ``_tables_inflight``
        # directly (protected-member discipline).
        self._tables_ready: dict[str, int] = {}
        self._tables_inflight: dict[str, asyncio.Event] = {}
        self._schema_version_unsupported = False
        # Drop epoch: bumped by every memo invalidation (forget/clear). The
        # bind-DDL leader captures it before its DDL run and re-checks before
        # memoising — a drop that landed mid-DDL (drop paths run on bare
        # ``engine.begin()``, outside the write lock) must not be overwritten
        # by a stale memo write, or the cache would record a post-drop
        # schema_version while the tables are gone and every later bind would
        # skip re-creation ("no such table" in-band errors). Deliberately one
        # global counter rather than per-session: a cross-session invalidation
        # only costs the next bind one idempotent DDL re-run, while a missed
        # invalidation is a persistent poisoned cache.
        self._tables_drop_epoch = 0

    async def current_schema_version(self) -> Optional[int]:
        """Return the DB's ``PRAGMA schema_version`` (read pool, no write lock).

        Sub-millisecond header read; the cross-process invalidation signal
        for the bind-DDL memo. Returns None when the backend has no such
        cookie (``PRAGMA`` is SQLite-only; on PostgreSQL / MySQL it is a
        syntax error) — callers treat None as "unknown" (memo miss), and the
        probe disables itself after the first detection so those backends
        never pay a doomed query per bind.
        """
        if self._schema_version_unsupported:
            return None
        async with self.read() as session:
            conn = await session.connection()
            if conn.dialect.name != "sqlite":
                self._schema_version_unsupported = True
                team_logger.warning(
                    "schema_version probe unsupported on dialect %s; bind-DDL "
                    "memo fast path disabled (idempotent DDL re-runs per bind)",
                    conn.dialect.name,
                )
                return None
            row = (await session.execute(text("PRAGMA schema_version"))).first()
            return int(row[0]) if row else 0

    def get_session_tables_ready_version(self, session_id: str) -> Optional[int]:
        """Return the memoised build-time schema_version, None when not memoised."""
        return self._tables_ready.get(session_id)

    def memoize_session_tables_ready(self, session_id: str, version: Optional[int]) -> None:
        """Memoise a successful bind-time DDL run.

        A None version (backend without a schema cookie) is deliberately NOT
        memoised — the next bind re-runs the idempotent DDL instead of
        trusting a version it cannot verify.
        """
        if version is None:
            self._tables_ready.pop(session_id, None)
            return
        self._tables_ready[session_id] = version

    def begin_session_tables_inflight(self, session_id: str) -> tuple[asyncio.Event, bool]:
        """Register/join the in-flight bind-DDL run for a session.

        Returns ``(event, is_leader)``: the leader (first caller) runs the
        DDL; followers await the event, then verify the memo — a leader that
        finished without memoising (degraded/cancelled) sends followers back
        here to re-contend for leadership.
        """
        inflight = self._tables_inflight.get(session_id)
        if inflight is not None:
            return inflight, False
        inflight = asyncio.Event()
        self._tables_inflight[session_id] = inflight
        return inflight, True

    def end_session_tables_inflight(self, session_id: str, inflight_event: asyncio.Event) -> None:
        """Close the leader's in-flight run and release followers."""
        self._tables_inflight.pop(session_id, None)
        inflight_event.set()

    def forget_session_tables_ready(self, session_id: Optional[str]) -> None:
        """Drop a session's bind-DDL memo after its dynamic tables are dropped.

        Called by ``TeamDatabase`` drop/cleanup methods — the only legitimate
        writers outside this module. Without it a re-created session would
        skip table creation entirely (stale "ready") and fail in-band.
        Bumps the drop epoch so an in-flight bind-DDL leader does not
        overwrite this invalidation with a stale memo write.
        """
        self._tables_drop_epoch += 1
        if session_id:
            self._tables_ready.pop(session_id, None)

    def clear_session_tables_ready(self) -> None:
        """Void the whole bind-DDL memo (all dynamic tables were deleted)."""
        self._tables_drop_epoch += 1
        self._tables_ready.clear()

    def tables_drop_epoch(self) -> int:
        """Current drop epoch; compared by the bind-DDL leader across its run."""
        return self._tables_drop_epoch

    def is_session_tables_ready(self, session_id: str) -> bool:
        """Return whether this session's bind-time DDL is memoised as done."""
        return session_id in self._tables_ready

    @asynccontextmanager
    async def read(self) -> AsyncIterator[AsyncSession]:
        """Yield a session for read-only work; the write lock is not held.

        The whole block runs under ``_DB_SESSION_WATCHDOG_SECONDS`` so a
        wedged driver surfaces as a loud ``TimeoutError`` instead of hanging
        the caller forever.
        """
        try:
            async with asyncio.timeout(_DB_SESSION_WATCHDOG_SECONDS):
                async with self._read_session_local() as session:
                    yield session
        except TimeoutError:
            team_logger.warning(
                "DbSessions.read() exceeded the %.0fs watchdog; "
                "the DB driver may be wedged",
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
                    "DbSessions.write() exceeded the %.0fs watchdog; "
                    "the DB driver may be wedged",
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


def with_db_retry(on_locked_result: Any) -> Callable[..., Callable[..., Awaitable[Any]]]:
    """Decorate a DAO write method with transient-lock retry.

    Thin wrapper over :func:`retry_on_locked` for whole-method retry: the
    decorated coroutine is re-invoked from scratch on each attempt, so it
    must be safe to re-run after a *lock-acquisition* failure — which is the
    only failure mode retried (``OperationalError`` "database is locked" /
    pool ``TimeoutError``): the lock error fires before the failed attempt's
    DML executes (or rolls its transaction back at commit), so no partial
    write survives into the retry. DAO write methods are single-statement
    CAS / INSERT shells over one ``write()`` block and satisfy this.

    The label is derived from the method's ``__qualname__`` (e.g.
    ``MemberDao.update_member_status``) so retry/exhaustion logs name the
    failing operation directly.

    ``on_locked_result`` is returned after the retry budget is exhausted
    (e.g. ``False`` for bool-returning CAS methods, ``None`` for optional
    fetches); the final failure is always logged at ERROR by
    :func:`retry_on_locked`, so a persistently locked DB (foreign process
    holding the file) stays diagnosable even though the caller sees the
    benign fallback value. A *callable* fallback is treated as a factory and
    invoked per call (``list`` / ``dict`` / ``lambda``), so mutable results
    are never shared between callers.
    """

    def decorator(fn: Callable[..., Awaitable[Any]]) -> Callable[..., Awaitable[Any]]:
        @functools.wraps(fn)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            fallback = on_locked_result() if callable(on_locked_result) else on_locked_result
            return await retry_on_locked(
                lambda: fn(*args, **kwargs),
                on_locked_result=fallback,
                label=fn.__qualname__,
            )

        return wrapper

    return decorator


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


def _create_table_with_indexes(sync_conn, table) -> None:
    """Atomically create one dynamic table *with* its model-declared indexes.

    The raw ``CreateTable`` DDL construct emits ONLY the CREATE TABLE
    statement — unlike ``Table.create()`` it does NOT emit CREATE INDEX for
    the table's ``Index`` objects (verified on sqlalchemy 2.0.51). Tables
    created through this path alone are born index-less, and the migration
    detector (``_ensure_dynamic_table_indexes``) then misreads them as
    legacy on the next bind — plus ``team_review_vote_*`` is not a migration
    candidate at all, so its ``task_round`` index would never appear.

    Running the index ensure on EVERY call (not just fresh creates) also
    heals tables born during the no-index window: ``CREATE INDEX IF NOT
    EXISTS`` is a sub-millisecond no-op when the index already exists.
    """
    sync_conn.execute(CreateTable(table, if_not_exists=True))
    if sync_conn.dialect.name == "mysql":
        # MySQL has no CREATE INDEX IF NOT EXISTS (MariaDB-only syntax; MySQL
        # 8.x rejects it with error 1064) — probe the live schema instead and
        # emit a plain CREATE INDEX only for indexes that are missing. The
        # table was just ensured above, so the reflection target exists.
        existing = {
            idx["name"] for idx in inspect(sync_conn).get_indexes(table.name)
        }
        for index in table.indexes:
            if index.name not in existing:
                try:
                    sync_conn.execute(CreateIndex(index))
                except Exception as exc:
                    # 并发首绑竞态：另一会话已建同名索引（MySQL 1061
                    # "Duplicate key name"）——幂等语义下同义成功，容忍；
                    # 其余错误原样上抛。SQLite 无此问题（单写者串行 +
                    # IF NOT EXISTS），仅 mysql 分支需要。
                    orig = getattr(exc, "orig", None)
                    errno = orig.args[0] if orig is not None and orig.args else None
                    if errno != 1061:
                        raise
        return
    for index in table.indexes:
        sync_conn.execute(CreateIndex(index, if_not_exists=True))


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
        sync_conn.exec_driver_sql(
            "ALTER TABLE team_info ADD COLUMN dispatch_mode TEXT NOT NULL DEFAULT 'autonomous'"
        )
        team_logger.info("Migrated legacy team_info table: added dispatch_mode column")
    if "enable_task_verification" not in columns:
        sync_conn.exec_driver_sql(
            "ALTER TABLE team_info ADD COLUMN enable_task_verification BOOLEAN NOT NULL DEFAULT 0"
        )
        team_logger.info("Migrated legacy team_info table: added enable_task_verification column")


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


def _ensure_message_meta_column(sync_conn) -> None:
    """Backfill the ``meta`` column on pre-existing per-session message tables.

    Same reason as ``_ensure_message_protocol_column``: ``create_all`` never
    alters a live table. Legacy rows get NULL meta, which the delivery path
    reads as "plain message, render content verbatim" — the pre-F_63 behavior.
    """
    inspector = inspect(sync_conn)
    for table_name in inspector.get_table_names():
        if not table_name.startswith("team_message_"):
            continue
        columns = {col["name"] for col in inspector.get_columns(table_name)}
        if "meta" in columns:
            continue
        sync_conn.exec_driver_sql(f"ALTER TABLE {table_name} ADD COLUMN meta TEXT")
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

    ``CREATE TABLE IF NOT EXISTS`` never alters an existing table, so a
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
    them already existed *before* the create step ran; tables not in it were
    just created on the current scheme and are skipped entirely — no
    ``get_indexes`` / ``get_columns`` PRAGMA round-trip at all. This replaces
    the old ``inspector.get_table_names()`` full-DB scan: a fresh bind no
    longer pays a PRAGMA call per historical dynamic table in the file, which
    is what stretched bind-time DDL connection holds past the write-pool
    checkout timeout under multi-team concurrent binds. Legacy tables
    of *other* sessions are migrated when those sessions next bind.
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
            # it would only burn a ``get_indexes``/``get_columns`` PRAGMA call.
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
            task_columns = {c["name"] for c in inspector.get_columns(table_name)}
            if "reviewer" not in task_columns:
                sync_conn.exec_driver_sql(f'ALTER TABLE "{table_name}" ADD COLUMN reviewer TEXT')
                team_logger.info("Migrated task table %s: added reviewer column", table_name)

            # F_62: review voting — round counter + per-task round ceiling.
            if "review_round" not in task_columns:
                sync_conn.exec_driver_sql(
                    f'ALTER TABLE "{table_name}" ADD COLUMN review_round INTEGER NOT NULL DEFAULT 0'
                )
                team_logger.info("Migrated task table %s: added review_round column", table_name)
            if "max_review_rounds" not in task_columns:
                sync_conn.exec_driver_sql(f'ALTER TABLE "{table_name}" ADD COLUMN max_review_rounds INTEGER')
                team_logger.info("Migrated task table %s: added max_review_rounds column", table_name)

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
            db_path = Path(conn_str).expanduser()
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
    async with write_engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
        await conn.run_sync(_ensure_team_member_role_column)
        await conn.run_sync(_ensure_team_member_options_column)
        await conn.run_sync(_ensure_team_info_capability_columns)

    return SqlEngines(write_engine, read_engine, write_session_local, read_session_local)


async def _ensure_session_tables(session: AsyncSession) -> None:
    """Create / ensure the per-session dynamic tables on an open session."""
    task_model = _get_task_model()
    dep_model = _get_task_dependency_model()
    message_model = _get_message_model()
    read_status_model = _get_message_read_status_model()
    review_vote_model = _get_review_vote_model()
    conn = await session.connection()
    # Snapshot which migration-candidate tables already exist *before* create
    # runs. ``CREATE TABLE IF NOT EXISTS`` leaves a pre-existing table
    # untouched, so only those need migration; freshly-created ones are on
    # the current scheme and can be skipped without a single ``get_indexes``
    # PRAGMA call.
    candidates = _current_session_migration_tables()
    existing_before_create = await conn.run_sync(_snapshot_existing_tables, candidates)
    for model in (task_model, dep_model, message_model, read_status_model, review_vote_model):
        # 原子建表：checkfirst=True 是"先查再建"两步，并发/多进程下有窗口
        # （成员惰性拉起并发初始化同会话动态表时炸 already exists）；
        # CREATE TABLE IF NOT EXISTS 由 SQLite 单写者锁保证原子性。
        # 注意必须走 _create_table_with_indexes：裸 CreateTable 不建索引
        # （Table.create 才带），漏建会让迁移检测把新表误读成 legacy。
        await conn.run_sync(_create_table_with_indexes, model.__table__)
    await conn.run_sync(_ensure_message_protocol_column)
    await conn.run_sync(_ensure_message_meta_column)
    await conn.run_sync(
        _ensure_dynamic_table_indexes, candidates, existing_before_create
    )


async def create_cur_session_tables(sessions: DbSessions) -> None:
    """Create dynamic tables for current session under the process-wide write lock.

    Runs inside ``DbSessions.write()`` so bind-time DDL serialises with every
    DAO write of this instance. The engine-level variant (``engine.begin()``)
    bypassed the lock: two teammates binding concurrently could hold BOTH
    write-pool connections while their DDL parked on the SQLite file lock,
    exhausting the 2-connection pool (``QueuePool limit of size 2`` teammate
    crashes).

    The session_id is obtained from context via get_session_id().

    Per-session memoisation: a session whose tables have been built once in
    this process is skipped outright (members bind 1+N times per team spawn /
    recovery, and the snapshot DB-fallback calls this too — all but the first
    are pure write-lock overhead). Concurrent first binds collapse onto one
    leader run via an in-flight Event. The memo is dropped on every
    table-drop path and never set by a degraded (budget-exhausted) run.

    Retries on transient errors: SQLite write contention
    (``OperationalError``) and pool-exhaustion (``SATimeoutError``) as
    ``retry_on_locked`` does, *plus* the ``asyncio.timeout`` watchdog
    ``TimeoutError`` raised by ``DbSessions.write()`` when the driver thread
    is wedged. A failed retry budget does not raise:
    ``create_cur_session_tables`` returns ``None`` on success-or-give-up and
    ``bind_session`` does not inspect the return, so a bind-time DDL hiccup
    degrades to a logged error rather than crashing the member — the
    session's tables either got created/migrated on a retry, or the next DAO
    write surfaces the real error in-band.
    """
    session_id = get_session_id()
    if not session_id:
        team_logger.warning("No session_id in context, cannot create session tables")
        return

    # Per-session memo (scoped to this DbSessions = this database). The cached
    # value is the schema_version at build time; compare it against the live
    # header cookie so a schema change by a *foreign* process (drop/create
    # outside our drop-path hooks) invalidates the entry and re-runs the DDL.
    # A version read failure is treated as a miss — re-running idempotent DDL
    # is safe, wrongly skipping it is not.
    cached_version = sessions.get_session_tables_ready_version(session_id)
    if cached_version is not None:
        try:
            live_version = await sessions.current_schema_version()
        except Exception as exc:
            # A version read failure is treated as a miss — re-running
            # idempotent DDL is safe, wrongly skipping it is not. The failure
            # itself must stay visible: swallowing it silently would leave a
            # permanently-failing memo probe indistinguishable from a healthy
            # cache in the logs.
            team_logger.warning(
                "schema_version read failed for session %s; treating bind-DDL "
                "memo as miss: %s",
                session_id,
                exc,
            )
            live_version = None
        if live_version is not None and live_version == cached_version:
            return
        sessions.forget_session_tables_ready(session_id)
    # Concurrent first-bind burst (members spawn in parallel): followers await
    # the leader's completion instead of each paying a write-lock DDL run.
    # A follower MUST verify the outcome after the wait: a degraded
    # (budget-exhausted) or cancelled leader releases the Event WITHOUT
    # memoising — returning unconditionally would be a silent fake success
    # (bind looks done, tables may not exist). Not-ready followers re-contend
    # for leadership and pay their own retry budget; the burst stays collapsed
    # in the healthy case (leader memoises → followers return on the first
    # post-wait check).
    while True:
        inflight, is_leader = sessions.begin_session_tables_inflight(session_id)
        if is_leader:
            try:
                await _create_cur_session_tables_locked(sessions, session_id)
            finally:
                sessions.end_session_tables_inflight(session_id, inflight)
            return
        await inflight.wait()
        if sessions.is_session_tables_ready(session_id):
            return
        team_logger.warning(
            "Bind-DDL leader run for session %s ended without memoising "
            "(degraded or cancelled); follower re-contending as leader",
            session_id,
        )


async def _create_cur_session_tables_locked(sessions: DbSessions, session_id: str) -> None:
    """Retry-loop body of ``create_cur_session_tables`` (single leader run).

    Retries on transient errors: SQLite write contention
    (``OperationalError``) and pool-exhaustion (``SATimeoutError``) as
    ``retry_on_locked`` does, *plus* the ``asyncio.timeout`` watchdog
    ``TimeoutError`` raised by ``DbSessions.write()`` when the driver thread
    is wedged. A failed retry budget does not raise:
    ``create_cur_session_tables`` returns ``None`` on success-or-give-up and
    ``bind_session`` does not inspect the return, so a bind-time DDL hiccup
    degrades to a logged error rather than crashing the member — the
    session's tables either got created/migrated on a retry, or the next DAO
    write surfaces the real error in-band. Only a genuinely successful run
    memoises the session (with the post-build schema_version) into
    ``_tables_ready``.
    """

    async def _ddl_once() -> None:
        async with sessions.write() as session:
            await _ensure_session_tables(session)
            await session.commit()

    # Observability: bind-time DDL now holds the process-wide write lock, so a
    # slow run stalls every DAO write of the instance. Always log the elapsed
    # time; flag runs slow enough to matter (DDL 耗时打点).
    started_at = time.monotonic()
    # Drop epoch captured before the DDL run: a drop path that landed mid-run
    # (drop goes through bare ``engine.begin()``, not the write lock) already
    # invalidated the memo via forget/clear — re-checking at memoise time
    # keeps this leader from overwriting that invalidation with a stale write.
    drop_epoch_at_start = sessions.tables_drop_epoch()
    last_err: BaseException | None = None
    for attempt in range(_DB_RETRY_ATTEMPTS):
        try:
            await _ddl_once()
            elapsed_ms = (time.monotonic() - started_at) * 1000
            team_logger.info(
                "Session tables ready for session %s (%.0fms)",
                session_id,
                elapsed_ms,
            )
            if elapsed_ms > _DDL_SLOW_WARNING_MS:
                team_logger.warning(
                    "Bind-time session DDL for session %s took %.0fms under the "
                    "write lock; all DAO writes stalled for the duration",
                    session_id,
                    elapsed_ms,
                )
            # Record the post-build schema_version — must be read AFTER
            # our own DDL (which itself bumps the cookie), or the next
            # bind would instantly miss. A None version (backend without
            # a schema cookie) is not memoised — the next bind re-runs
            # the idempotent DDL.
            try:
                version = await sessions.current_schema_version()
            except Exception as exc:
                # Version read failed → do not memoise; the next bind re-runs
                # the idempotent DDL. Never let memo bookkeeping mask success,
                # and never hide the read failure itself.
                team_logger.warning(
                    "schema_version read after bind DDL failed for session %s; "
                    "not memoising: %s",
                    session_id,
                    exc,
                )
                sessions.forget_session_tables_ready(session_id)
                return
            if sessions.tables_drop_epoch() != drop_epoch_at_start:
                # 表在 DDL 或版本读取期间被 drop 路径删过（forget/clear 已递增
                # 代数）——此刻读到的 schema_version 是删除后的版本，写回 memo
                # 就是「版本齐但表不在」的脏缓存（后续 bind 全部跳过建表）。
                # 不写，让下一次 bind 重跑幂等 DDL。
                team_logger.warning(
                    "Session tables dropped concurrently with bind DDL for "
                    "session %s; not memoising bind-DDL cache",
                    session_id,
                )
                sessions.forget_session_tables_ready(session_id)
                return
            # 复检与写回之间不得有任何 await——asyncio 单线程不抢占，同步
            # 区段内 drop 路径插不进来，竞态窗口在此闭合。
            sessions.memoize_session_tables_ready(session_id, version)
            return
        except (OperationalError, SATimeoutError, TimeoutError) as e:
            last_err = e
            if attempt >= _DB_RETRY_ATTEMPTS - 1:
                break
            delay = _DB_RETRY_BASE_DELAY * (2**attempt)
            team_logger.warning(
                "create_cur_session_tables hit a transient DB error (%s); "
                "retrying in %ss (attempt %d)",
                type(e).__name__,
                delay,
                attempt + 1,
            )
            await asyncio.sleep(delay)

    # Budget exhausted — do not crash the member. A subsequent DAO write will
    # either succeed on its own (tables are idempotent under IF NOT EXISTS) or
    # raise the real error in-band where the caller can handle it.
    team_logger.error(
        "create_cur_session_tables failed after %d attempts for session %s: %s",
        _DB_RETRY_ATTEMPTS,
        session_id,
        last_err,
    )
    return


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
    review_vote_model = _get_review_vote_model()

    async with engine.begin() as conn:
        for model in (task_model, dep_model, message_model, read_status_model, review_vote_model):
            await conn.run_sync(model.__table__.drop, checkfirst=True)

    # The tables are gone for good; drop the cached classes (and their
    # SQLModel.metadata registrations) so they don't outlive the session.
    _clear_session_model_cache(session_id)

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

    Also clears the process-wide dynamic-model caches for the session
    (``_clear_session_model_cache``): the cached classes and their
    ``SQLModel.metadata`` table registrations are per-session state and
    would otherwise accumulate for the lifetime of the process.

    Args:
        engine: Database engine.
        session_id: Session identifier to clean up.

    Returns:
        List of dropped table names.
    """
    if not session_id:
        return []

    _clear_session_model_cache(session_id)

    if engine is None:
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
