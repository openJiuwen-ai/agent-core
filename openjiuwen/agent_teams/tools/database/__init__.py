# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Team Database Module.

``TeamDatabase`` owns the engine lifecycle and cross-table transactions
that span more than one DAO. Single-table operations live on the DAO
instances exposed as attributes (``team`` / ``member`` / ``task`` /
``message``) — call them directly instead of going through the facade.
Model definitions live in models.py.
"""

import asyncio
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from openjiuwen.agent_teams.context import get_session_id
from openjiuwen.agent_teams.tools.database.config import (
    DatabaseConfig as DatabaseConfig,
)
from openjiuwen.agent_teams.tools.database.config import (
    DatabaseType as DatabaseType,
)
from openjiuwen.agent_teams.tools.database.engine import (
    DbSessions,
)
from openjiuwen.agent_teams.tools.database.engine import (
    cleanup_all_runtime_state as _cleanup_all_runtime_state,
)
from openjiuwen.agent_teams.tools.database.engine import (
    create_cur_session_tables as _create_cur_session_tables,
)
from openjiuwen.agent_teams.tools.database.engine import (
    drop_cur_session_tables as _drop_cur_session_tables,
)
from openjiuwen.agent_teams.tools.database.engine import (
    drop_session_tables_by_id as _drop_session_tables_by_id,
)
from openjiuwen.agent_teams.tools.database.engine import (
    get_current_time as _get_current_time,
)
from openjiuwen.agent_teams.tools.database.engine import (
    initialize_engine as _initialize_engine,
)
from openjiuwen.agent_teams.tools.database.engine import (
    run_wal_checkpoint_passive as _run_wal_checkpoint_passive,
)
from openjiuwen.agent_teams.tools.database.graph import (
    TASK_DEPENDENCY_REJECT_STATUSES as TASK_DEPENDENCY_REJECT_STATUSES,
)
from openjiuwen.agent_teams.tools.database.graph import (
    TASK_TERMINAL_STATUSES as TASK_TERMINAL_STATUSES,
)
from openjiuwen.agent_teams.tools.database.graph import (
    detect_cycle_in_adjacency as detect_cycle_in_adjacency,
)
from openjiuwen.agent_teams.tools.database.member_dao import MemberDao as MemberDao
from openjiuwen.agent_teams.tools.database.message_dao import MessageDao as MessageDao
from openjiuwen.agent_teams.tools.database.task_dao import TaskDao as TaskDao
from openjiuwen.agent_teams.tools.database.team_dao import TeamDao as TeamDao
from openjiuwen.agent_teams.tools.models import (
    Team as Team,
)
from openjiuwen.agent_teams.tools.models import (
    TeamMember as TeamMember,
)
from openjiuwen.agent_teams.tools.models import (
    TeamMessageBase as TeamMessageBase,
)
from openjiuwen.agent_teams.tools.models import (
    TeamTaskBase as TeamTaskBase,
)
from openjiuwen.agent_teams.tools.models import (
    TeamTaskDependencyBase as TeamTaskDependencyBase,
)
from openjiuwen.core.common.logging import team_logger


class TeamDatabase:
    """Asynchronous team database manager.

    Owns the engine lifecycle and cross-table transactions. Single-table
    operations live on the DAO attributes (``team`` / ``member`` /
    ``task`` / ``message``) — call them directly.
    """

    def __init__(self, config: DatabaseConfig):
        """Initialize database manager."""
        self.config = config
        # ``engine`` / ``session_local`` are the WRITER engine + factory (also
        # used by the DDL helpers). ``read_engine`` / ``read_session_local``
        # are the separate reader pool for file-backed SQLite; they alias the
        # writer fields when there is no split (:memory: / PostgreSQL / MySQL).
        self.engine: Optional[AsyncEngine] = None
        self.session_local: Optional[async_sessionmaker] = None
        self.read_engine: Optional[AsyncEngine] = None
        self.read_session_local: Optional[async_sessionmaker] = None
        self._initialized = False
        self._init_lock = asyncio.Lock()
        # Background WAL checkpointer task (file-backed SQLite +
        # wal_checkpoint_interval_s > 0 only); None otherwise.
        self._checkpoint_task: Optional[asyncio.Task] = None
        self.team: Optional[TeamDao] = None
        self.member: Optional[MemberDao] = None
        self.task: Optional[TaskDao] = None
        self.message: Optional[MessageDao] = None

    @staticmethod
    def get_current_time() -> int:
        """Return current time in milliseconds."""
        return _get_current_time()

    async def initialize(self) -> None:
        """Initialize async engine, create static schema, and wire up DAOs.

        Engine setup creates the static schema (the team-scoped tables) that
        every caller needs. Per-session dynamic tables are a separate concern:
        they are created here only when a session is already bound in the
        contextvar. Session-less callers — dispatch inspection, delete /
        release by explicit session id — only touch the static schema, so
        skipping the dynamic step keeps them from targeting a session that is
        not theirs. The bound interact path creates the dynamic tables via
        ``create_cur_session_tables()`` at session-bind time.
        """
        if self._initialized:
            return
        async with self._init_lock:
            if self._initialized:
                return
            engines = await _initialize_engine(self.config)
            self.engine = engines.write_engine
            self.read_engine = engines.read_engine
            self.session_local = engines.write_session_local
            self.read_session_local = engines.read_session_local
            if get_session_id():
                await _create_cur_session_tables(self.engine)

            # One DbSessions (one write lock) shared by every DAO: SQLite's
            # write lock is database-wide, so all four tables must serialise
            # writes through the same lock. Reads go to the reader factory
            # (a separate pool for file-backed SQLite) — see DbSessions.
            sessions = DbSessions(self.session_local, self.read_session_local)
            self.team = TeamDao(sessions)
            self.member = MemberDao(sessions)
            self.task = TaskDao(sessions)
            self.message = MessageDao(sessions)

            self._maybe_start_checkpointer()

            self._initialized = True
            team_logger.info("Team database initialized")

    def _maybe_start_checkpointer(self) -> None:
        """Start the background WAL checkpointer when configured.

        Only for file-backed SQLite with WAL and a positive
        ``wal_checkpoint_interval_s``. A file-backed engine is the one case
        where ``read_engine`` is a distinct pool from the writer, which also
        confirms this is not ``:memory:`` / PostgreSQL / MySQL.
        """
        interval = self.config.wal_checkpoint_interval_s
        is_file_sqlite_split = self.read_engine is not None and self.read_engine is not self.engine
        if interval <= 0 or not is_file_sqlite_split or not self.config.db_enable_wal:
            return
        self._checkpoint_task = asyncio.create_task(self._checkpoint_loop(interval))
        team_logger.info("Started background WAL checkpointer (interval=%.1fs)", interval)

    async def _checkpoint_loop(self, interval_s: float) -> None:
        """Run a PASSIVE WAL checkpoint every ``interval_s`` seconds.

        Runs on the writer engine but off the app write lock (a separate
        connection); PASSIVE never blocks the writer. A failed checkpoint is
        logged and the loop continues — it must never crash the app.
        """
        while True:
            await asyncio.sleep(interval_s)
            if self.engine is None:
                return
            try:
                await _run_wal_checkpoint_passive(self.engine)
            except asyncio.CancelledError:
                # Let cancellation propagate so the loop exits on shutdown.
                # Redundant today (CancelledError is a BaseException, not caught
                # by the broad `except Exception` below), but kept as an explicit
                # guard in case that handler is ever widened to BaseException.
                team_logger.debug("WAL checkpoint loop cancelled; exiting")
                raise
            except Exception as e:
                team_logger.warning("Background WAL checkpoint failed: %s", e)

    async def create_cur_session_tables(self) -> None:
        """Create dynamic tables for current session."""
        if self.engine is None:
            return
        await _create_cur_session_tables(self.engine)

    async def drop_cur_session_tables(self) -> None:
        """Drop dynamic tables for current session."""
        if self.engine is None:
            return
        await _drop_cur_session_tables(self.engine)

    async def cleanup_all_runtime_state(self) -> tuple[list[str], list[str]]:
        """Delete all dynamic team tables and clear static team tables."""
        await self._ensure_initialized()
        if self.engine is None:
            return [], []
        return await _cleanup_all_runtime_state(self.engine)

    async def drop_session_tables_by_id(self, session_id: str) -> list[str]:
        """Drop dynamic tables for a specific session without active context.

        Used by Runner.release(session_id) to clean up per-session tables
        after the agent has finished executing.

        Args:
            session_id: Session identifier to clean up.

        Returns:
            List of dropped table names.
        """
        await self._ensure_initialized()
        if self.engine is None:
            return []
        return await _drop_session_tables_by_id(self.engine, session_id)

    async def force_delete_team_session(self, team_name: str) -> bool:
        """Delete a team's persisted row and drop current session tables.

        Cross-table teardown used during session-switch: removes the
        ``team_info`` row through the team DAO and drops dynamic
        per-session tables tied to the current session context. Returns
        ``True`` when the session-table drop succeeds; absence of the
        team row also counts as success here since the goal is "no
        trace left".
        """
        await self._ensure_initialized()
        cleanup_success = True
        from openjiuwen.agent_teams.worktree.session_cleanup import remove_session_worktrees

        session_id = get_session_id()
        if session_id:
            cleanup_success = await remove_session_worktrees(team_name, session_id)

        await self.team.delete_team(team_name)

        try:
            await _drop_cur_session_tables(self.engine)
        except Exception as e:
            team_logger.error(
                "Failed to drop current session tables for team %s: %s",
                team_name,
                e,
            )
            return False

        team_logger.info("Force deleted team session data for %s", team_name)
        return cleanup_success

    async def close(self) -> None:
        """Close the database engine(s) and release all connections."""
        if self._checkpoint_task is not None:
            self._checkpoint_task.cancel()
            try:
                await self._checkpoint_task
            except asyncio.CancelledError:
                pass
            self._checkpoint_task = None
        if self.engine:
            # Dispose the reader engine first when it is a distinct pool
            # (file-backed SQLite); it aliases the writer otherwise.
            if self.read_engine is not None and self.read_engine is not self.engine:
                await self.read_engine.dispose()
            await self.engine.dispose()
            self.engine = None
            self.read_engine = None
            self.session_local = None
            self.read_session_local = None
            self._initialized = False
            self.team = None
            self.member = None
            self.task = None
            self.message = None
            team_logger.info("Team database closed")

    async def _ensure_initialized(self) -> None:
        """Initialize on first call; idempotent thereafter."""
        if not self._initialized:
            await self.initialize()
