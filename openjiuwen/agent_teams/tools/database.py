# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Team Database Module

Asynchronous database manager with full CRUD for team data.
Model definitions live in models.py.
"""

import asyncio
import time
from pathlib import Path
from typing import (
    Dict,
    Iterable,
    List,
    Optional,
)

from pydantic import BaseModel
from sqlalchemy import (
    event,
    func,
    inspect,
    select,
    update,
)
from sqlalchemy.exc import (
    IntegrityError,
    OperationalError,
)
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import AsyncAdaptedQueuePool, StaticPool
from sqlmodel import SQLModel

from openjiuwen.agent_teams.schema.status import (
    EXECUTION_TRANSITIONS,
    MEMBER_TRANSITIONS,
    TASK_TRANSITIONS,
    ExecutionStatus,
    MemberMode,
    MemberStatus,
    TaskStatus,
    is_valid_transition,
)
from openjiuwen.agent_teams.schema.task import (
    GraphMutationResult,
    NewTaskSpec,
)
from openjiuwen.agent_teams.spawn.context import get_session_id
from openjiuwen.agent_teams.tools.models import (
    TEAM_DYNAMIC_TABLE_PREFIXES,
    TEAM_STATIC_TABLES_TO_CLEAR,
    Team,
    TeamMember,
    TeamMessageBase,
    TeamTaskBase,
    TeamTaskDependencyBase,
    _clear_session_model_cache,
    _get_message_model,
    _get_message_read_status_model,
    _get_task_dependency_model,
    _get_task_model,
)
from openjiuwen.core.common.logging import team_logger


# ----------------- Database Configuration -----------------
class DatabaseType:
    """Supported database types"""

    SQLITE = "sqlite"
    POSTGRESQL = "postgresql"
    MYSQL = "mysql"


class DatabaseConfig(BaseModel):
    """Database configuration class"""

    db_type: str = DatabaseType.SQLITE
    connection_string: str = ""
    db_timeout: int = 30
    db_enable_wal: bool = True


_DB_RETRY_ATTEMPTS = 3
_DB_RETRY_BASE_DELAY = 0.5


# ----------------- Dependency-graph helpers -----------------
_TASK_TERMINAL_STATUSES = frozenset(
    {TaskStatus.COMPLETED.value, TaskStatus.CANCELLED.value}
)
# Statuses that cannot accept new incoming dependencies — terminal or
# already executing. Adding a dep mid-execution would silently re-block a
# task the assignee is actively working on.
_TASK_DEPENDENCY_REJECT_STATUSES = frozenset(
    {
        TaskStatus.COMPLETED.value,
        TaskStatus.CANCELLED.value,
        TaskStatus.CLAIMED.value,
        TaskStatus.PLAN_APPROVED.value,
    }
)


def detect_cycle_in_adjacency(
    adjacency: Dict[str, List[str]],
) -> Optional[List[str]]:
    """Detect a cycle in a task-dependency adjacency map.

    The map points from a task to the tasks it depends on (``task_id ->
    [depends_on_task_id, ...]``). The walk follows edges in that
    direction; reaching an ancestor node in the current DFS path means
    the dependency chain loops back on itself.

    Args:
        adjacency: Outgoing-edge adjacency map.

    Returns:
        The cycle as a list of task IDs (the repeated node appears at
        both ends, e.g. ``[A, B, C, A]``), or ``None`` if the graph is
        acyclic. Iterative DFS with WHITE/GRAY/BLACK coloring keeps the
        recursion depth bounded for deep dependency chains.
    """
    white, gray, black = 0, 1, 2
    color: Dict[str, int] = {}
    for node, deps in adjacency.items():
        color[node] = white
        for dep in deps:
            color.setdefault(dep, white)

    cycle: Optional[List[str]] = None

    for root in list(color.keys()):
        if color[root] != white:
            continue
        # Iterative DFS: stack frames are (node, iterator-over-children).
        path: List[str] = [root]
        color[root] = gray
        stack: List[tuple[str, Iterable[str]]] = [(root, iter(adjacency.get(root, ())))]
        while stack:
            node, it = stack[-1]
            nxt = next(it, None)
            if nxt is None:
                stack.pop()
                color[node] = black
                path.pop()
                continue
            c = color.get(nxt, white)
            if c == gray:
                idx = path.index(nxt)
                cycle = path[idx:] + [nxt]
                return cycle
            if c == white:
                color[nxt] = gray
                path.append(nxt)
                stack.append((nxt, iter(adjacency.get(nxt, ()))))

    return None


# ----------------- Asynchronous Database Manager -----------------
class TeamDatabase:
    """Asynchronous team database manager with full CRUD"""

    def __init__(self, config: DatabaseConfig):
        """Initialize database manager"""
        self.config = config
        self.engine: Optional[AsyncEngine] = None
        self._initialized = False
        self.session_local: Optional[async_sessionmaker] = None
        # Serialize initialize() across concurrent callers. Leader and
        # in-process teammates all call initialize() at startup; without
        # this lock they race past the ``_initialized`` check, each rebuild
        # ``self.engine`` / ``self.session_local``, and sessions bound to a
        # replaced engine surface as "unable to open database file" or
        # "table already exists" during ``CREATE TABLE``.
        self._init_lock: Optional[asyncio.Lock] = None

    @staticmethod
    def get_current_time() -> int:
        """return current time in milliseconds"""
        return int(round(time.time() * 1000))

    async def initialize(self) -> None:
        """Initialize async engine and create tables"""
        if self._initialized:
            return

        # Lazily create the lock so it binds to the running event loop.
        if self._init_lock is None:
            self._init_lock = asyncio.Lock()

        async with self._init_lock:
            if self._initialized:
                return
            await self._initialize_locked()

    async def _initialize_locked(self) -> None:
        """Actual initialization body; must run under ``_init_lock``."""
        db_type = self.config.db_type
        if db_type == DatabaseType.SQLITE:
            conn_str = self.config.connection_string
            in_memory = conn_str == ":memory:"
            if not in_memory:
                db_path = Path(conn_str).expanduser()
                conn_str = str(db_path)
                if not db_path.parent.exists():
                    db_path.parent.mkdir(parents=True, exist_ok=True)

            if in_memory:
                # StaticPool keeps a single connection alive so all operations
                # share the same in-memory database. NullPool would open a fresh
                # connection each time, giving an empty database after create_all.
                self.engine = create_async_engine(
                    "sqlite+aiosqlite:///:memory:",
                    echo=False,
                    future=True,
                    poolclass=StaticPool,
                    connect_args={"check_same_thread": False},
                )
            else:
                # AsyncAdaptedQueuePool with size=1 keeps a single long-lived
                # DBAPI connection (avoiding the NullPool worker-thread churn
                # that races on the SQLite WAL ``-shm`` mapping and surfaces
                # as ``sqlite3.OperationalError: unable to open database
                # file``), but unlike StaticPool it enforces exclusive
                # checkout. StaticPool hands the same connection to every
                # concurrent session, so two coroutines end up sharing one
                # SQLite transaction — one session's COMMIT/ROLLBACK then
                # commits or discards another session's pending writes,
                # which silently broke ``complete_task``'s "resolve deps then
                # unblock" sequence under concurrent leader/teammate access.
                self.engine = create_async_engine(
                    f"sqlite+aiosqlite:///{conn_str}",
                    echo=False,
                    future=True,
                    poolclass=AsyncAdaptedQueuePool,
                    pool_size=1,
                    max_overflow=0,
                    pool_pre_ping=False,
                    connect_args={
                        "timeout": self.config.db_timeout,
                        "check_same_thread": False,
                    },
                )

            # foreign_keys is a per-connection flag — SQLite defaults to OFF
            # for every new connection, so it must be re-set each time.
            @event.listens_for(self.engine.sync_engine, "connect")
            def set_sqlite_pragma(dbapi_connection, connection_record):
                cursor = dbapi_connection.cursor()
                cursor.execute("PRAGMA foreign_keys=ON")
                cursor.close()

            # journal_mode=WAL is a database-level persistent setting stored
            # in the file header.  With ``pool_size=1, max_overflow=0`` we
            # only ever materialise one DBAPI connection per engine, so
            # first_connect fires exactly once and WAL is set for the
            # lifetime of the engine.
            if self.config.db_enable_wal and not in_memory:

                @event.listens_for(self.engine.sync_engine, "first_connect")
                def set_sqlite_wal(dbapi_connection, connection_record):
                    cursor = dbapi_connection.cursor()
                    cursor.execute("PRAGMA journal_mode=WAL")
                    cursor.close()
        elif db_type == DatabaseType.POSTGRESQL:
            conn_str = self.config.connection_string.strip()
            if not conn_str:
                raise ValueError("PostgreSQL requires a non-empty connection_string")
            if conn_str.startswith("postgres://"):
                conn_str = f"postgresql://{conn_str.removeprefix('postgres://')}"
            if conn_str.startswith("postgresql://"):
                conn_str = f"postgresql+asyncpg://{conn_str.removeprefix('postgresql://')}"
            if not conn_str.startswith("postgresql+asyncpg://"):
                raise ValueError(
                    "PostgreSQL connection_string must use postgresql+asyncpg:// scheme"
                )

            # Use queue pool settings suitable for distributed deployments.
            self.engine = create_async_engine(
                conn_str,
                echo=False,
                future=True,
                pool_size=10,
                max_overflow=20,
                pool_pre_ping=True,
                pool_recycle=1800,
            )
        elif db_type == DatabaseType.MYSQL:
            conn_str = self.config.connection_string.strip()
            if not conn_str:
                raise ValueError("MySQL requires a non-empty connection_string")
            if conn_str.startswith("mysql://"):
                conn_str = f"mysql+aiomysql://{conn_str.removeprefix('mysql://')}"
            elif not conn_str.startswith("mysql+aiomysql://"):
                raise ValueError(
                    "MySQL connection_string must use mysql:// or mysql+aiomysql:// scheme"
                )

            # Use queue pool settings suitable for distributed deployments.
            self.engine = create_async_engine(
                conn_str,
                echo=False,
                future=True,
                pool_size=10,
                max_overflow=20,
                pool_pre_ping=True,
                pool_recycle=1800,
            )
        else:
            raise NotImplementedError(f"Database type {self.config.db_type} not yet implemented")

        # Create session factory
        self.session_local = async_sessionmaker(
            bind=self.engine,
            class_=AsyncSession,
            expire_on_commit=False,
            autoflush=False,
        )

        # Create base tables (only Team and TeamMember)
        async with self.engine.begin() as conn:
            await conn.run_sync(SQLModel.metadata.create_all)

        # Create session-specific dynamic tables for current session
        await self.create_cur_session_tables()

        self._initialized = True
        team_logger.info("Team database initialized")

    async def create_cur_session_tables(self) -> None:
        """Create dynamic tables for current session

        This method can be called for different sessions to create their
        corresponding dynamic tables (task, task_dependency, message, message_read_status).
        The session_id is obtained from context via get_session_id().
        """
        if self.engine is None:
            return

        session_id = get_session_id()
        if not session_id:
            team_logger.warning("No session_id in context, cannot create session tables")
            return

        # Get/create dynamic models (they use get_session_id() internally)
        task_model = _get_task_model()
        dep_model = _get_task_dependency_model()
        message_model = _get_message_model()
        read_status_model = _get_message_read_status_model()

        # Create tables (safe when tables already exist — e.g. teammate
        # processes connecting to the same database.)
        async with self.engine.begin() as conn:
            for model in (task_model, dep_model, message_model, read_status_model):
                await conn.run_sync(model.__table__.create, checkfirst=True)

        team_logger.info(f"Session tables ready for session {session_id}")

    async def drop_cur_session_tables(self) -> None:
        """Drop dynamic tables for current session

        This method drops all dynamic tables (task, task_dependency, message, message_read_status)
        for the current session context.
        The session_id is obtained from context via get_session_id().

        This is symmetric to create_cur_session_tables().
        """
        if self.engine is None:
            return

        session_id = get_session_id()
        if not session_id:
            team_logger.warning("No session_id in context, cannot drop session tables")
            return

        # Get models (creates if not in cache)
        task_model = _get_task_model()
        dep_model = _get_task_dependency_model()
        message_model = _get_message_model()
        read_status_model = _get_message_read_status_model()

        # Drop individual tables, not all tables in metadata
        async with self.engine.begin() as conn:
            for model in (task_model, dep_model, message_model, read_status_model):
                await conn.run_sync(model.__table__.drop, checkfirst=True)

        # Remove table definitions from metadata
        # This ensures we have the table objects to remove
        for model in (task_model, dep_model, message_model, read_status_model):
            SQLModel.metadata.remove(model.__table__)

        # Clear model cache so the next create_cur_session_tables() for the
        # same session_id builds fresh models with new __table__ objects
        # properly registered in metadata.
        _clear_session_model_cache(session_id)

        team_logger.info(f"Dropped dynamic tables for session {session_id}")

    @staticmethod
    def _get_table_names(sync_conn) -> list[str]:
        """Return all table names currently present in the database."""
        return list(inspect(sync_conn).get_table_names())

    @staticmethod
    def _drop_table(sync_conn, table_name: str) -> None:
        """Drop one table with raw SQL to avoid reflection-order issues."""
        quoted_name = table_name.replace('"', '""')
        sync_conn.exec_driver_sql(f'DROP TABLE IF EXISTS "{quoted_name}"')

    @staticmethod
    def _clear_table(sync_conn, table_name: str) -> None:
        """Delete all rows from one reflected table."""
        quoted_name = table_name.replace('"', '""')
        sync_conn.exec_driver_sql(f'DELETE FROM "{quoted_name}"')

    async def cleanup_all_runtime_state(self) -> tuple[list[str], list[str]]:
        """Delete all dynamic team tables and clear static team tables.

        This cleanup is storage-level and does not depend on an active
        agent instance or the current session context. It is intended for
        fallback cleanup during destroy and for restart-time recovery.
        """
        await self._ensure_initialized()
        if self.engine is None:
            return [], []

        deleted_tables: list[str] = []
        cleared_tables: list[str] = []
        async with self.engine.begin() as conn:
            table_names = await conn.run_sync(self._get_table_names)

            for table_name in table_names:
                if not table_name.startswith(TEAM_DYNAMIC_TABLE_PREFIXES):
                    continue
                await conn.run_sync(self._drop_table, table_name)
                deleted_tables.append(table_name)

            for table_name in TEAM_STATIC_TABLES_TO_CLEAR:
                if table_name not in table_names:
                    continue
                await conn.run_sync(self._clear_table, table_name)
                cleared_tables.append(table_name)

        team_logger.info(
            "Cleaned team runtime state: deleted dynamic tables={}, cleared static tables={}",
            deleted_tables,
            cleared_tables,
        )
        return deleted_tables, cleared_tables

    async def close(self) -> None:
        """Close the database engine and release all connections."""
        if self.engine:
            await self.engine.dispose()
            self.engine = None
            self.session_local = None
            self._initialized = False
            team_logger.info("Team database closed")

    async def _ensure_initialized(self):
        """Ensure database is initialized (sync wrapper)"""
        if not self._initialized:
            await self.initialize()

    # ----------------- Team Operations -----------------
    async def create_team(
        self,
        team_name: str,
        display_name: str,
        leader_member_name: str,
        desc: Optional[str] = None,
        prompt: Optional[str] = None,
    ) -> bool:
        """Create a new team"""
        await self._ensure_initialized()
        async with self.session_local() as session:
            try:
                ts = self.get_current_time()
                team = Team(
                    team_name=team_name,
                    display_name=display_name,
                    leader_member_name=leader_member_name,
                    desc=desc,
                    prompt=prompt,
                    created=ts,
                    updated_at=ts,
                )
                session.add(team)
                await session.commit()
                team_logger.info(f"Team {team_name} created")
                return True
            except IntegrityError as e:
                await session.rollback()
                team_logger.error(f"Team {team_name} already exists", e)
                return False

    async def get_team(self, team_name: str) -> Optional[Team]:
        """Get team information by ID"""
        await self._ensure_initialized()
        async with self.session_local() as session:
            result = await session.execute(select(Team).where(Team.team_name == team_name))
            return result.scalar_one_or_none()

    async def delete_team(self, team_name: str) -> bool:
        """Delete a team (cascade delete will remove related records)"""
        await self._ensure_initialized()
        async with self.session_local() as session:
            result = await session.execute(select(Team).where(Team.team_name == team_name))
            team = result.scalar_one_or_none()
            if not team:
                team_logger.debug(f"Team {team_name} not found for deletion")
                return True

            await session.delete(team)
            await session.commit()
            team_logger.info(f"Team {team_name} deleted")
            return True

    async def force_delete_team_session(self, team_name: str) -> bool:
        """Force delete a team's records and current session tables.

        This is intended for session-switch teardown where the caller
        wants to drop the persisted team row and also remove any
        dynamic per-session tables tied to the current session context.
        """
        deleted = await self.delete_team(team_name)

        try:
            await self.drop_cur_session_tables()
        except Exception as e:
            team_logger.error(
                "Failed to drop current session tables for team {}: {}",
                team_name,
                e,
            )
            return False

        team_logger.info("Force deleted team session data for {}", team_name)
        return deleted

    async def get_team_updated_at(self, team_name: str) -> int:
        """Probe ``team_info.updated_at`` for change detection.

        Cheap single-row column probe used by prompt-section caches to
        decide whether to refetch the full team metadata.

        Args:
            team_name: Team identifier.

        Returns:
            Last update timestamp (ms), or ``0`` when the row is
            missing or the column is null.
        """
        await self._ensure_initialized()
        async with self.session_local() as session:
            result = await session.execute(select(Team.updated_at).where(Team.team_name == team_name))
            value = result.scalar_one_or_none()
            return int(value) if value is not None else 0

    # ----------------- Member Operations -----------------
    async def create_member(
        self,
        member_name: str,
        team_name: str,
        display_name: str,
        agent_card: str,
        status: str,
        *,
        desc: Optional[str] = None,
        execution_status: Optional[str] = None,
        mode: str = MemberMode.BUILD_MODE.value,
        prompt: Optional[str] = None,
        model_ref_json: Optional[str] = None,
    ) -> bool:
        """Create a new team member"""
        await self._ensure_initialized()
        async with self.session_local() as session:
            try:
                member = TeamMember(
                    member_name=member_name,
                    team_name=team_name,
                    display_name=display_name,
                    agent_card=agent_card,
                    status=status,
                    desc=desc,
                    execution_status=execution_status,
                    mode=mode,
                    prompt=prompt,
                    model_ref_json=model_ref_json,
                    updated_at=self.get_current_time(),
                )
                session.add(member)
                await session.commit()
                team_logger.info(f"Member {member_name} created")
                return True
            except IntegrityError:
                await session.rollback()
                team_logger.error(f"Member {member_name} already exists")
                return False

    async def get_member(self, member_name: str, team_name: str) -> Optional[TeamMember]:
        """Get member information by ID"""
        await self._ensure_initialized()
        async with self.session_local() as session:
            result = await session.execute(
                select(TeamMember).where(TeamMember.member_name == member_name, TeamMember.team_name == team_name)
            )
            return result.scalar_one_or_none()

    async def get_team_members(self, team_name: str, status: str | None = None) -> List[TeamMember]:
        """Get members for a team, optionally filtered by status.

        Args:
            team_name: Team identifier.
            status: If provided, only return members with this status.
        """
        await self._ensure_initialized()
        async with self.session_local() as session:
            stmt = select(TeamMember).where(TeamMember.team_name == team_name)
            if status is not None:
                stmt = stmt.where(TeamMember.status == status)
            return (await session.execute(stmt)).scalars().all()

    async def get_members_max_updated_at(self, team_name: str) -> int:
        """Probe MAX(``team_member.updated_at``) for the team.

        Cheap aggregate query used by prompt-section caches to detect
        roster changes (member added).  Status / execution_status
        updates intentionally do NOT bump ``updated_at``, so this
        probe stays stable until a new member is created.

        Args:
            team_name: Team identifier.

        Returns:
            Largest member update timestamp (ms), or ``0`` when no
            members exist or all rows have null ``updated_at``.
        """
        await self._ensure_initialized()
        async with self.session_local() as session:
            result = await session.execute(
                select(func.max(TeamMember.updated_at)).where(TeamMember.team_name == team_name)
            )
            value = result.scalar_one_or_none()
            return int(value) if value is not None else 0

    async def update_member_status(
        self,
        member_name: str,
        team_name: str,
        status: str,
    ) -> bool:
        """Update member status"""
        await self._ensure_initialized()
        async with self.session_local() as session:
            result = await session.execute(
                select(TeamMember).where(TeamMember.member_name == member_name, TeamMember.team_name == team_name)
            )
            member = result.scalar_one_or_none()
            if not member:
                team_logger.error(f"Member {member_name} not found in team {team_name}")
                return False

            # Validate state transition
            if not is_valid_transition(
                MemberStatus(member.status),
                MemberStatus(status),
                MEMBER_TRANSITIONS,
            ):
                team_logger.error(f"Invalid state transition for member {member_name}: {member.status} -> {status}")
                return False

            member.status = status
            await session.commit()
            team_logger.debug(f"Member {member_name} status updated to {status}")
            return True

    async def update_member_execution_status(
        self,
        member_name: str,
        team_name: str,
        execution_status: str,
    ) -> bool:
        """Update member execution status"""
        await self._ensure_initialized()
        async with self.session_local() as session:
            result = await session.execute(
                select(TeamMember).where(TeamMember.member_name == member_name, TeamMember.team_name == team_name)
            )
            member = result.scalar_one_or_none()
            if not member:
                team_logger.error(f"Member {member_name} not found in team {team_name}")
                return False

            # Validate state transition
            if not is_valid_transition(
                ExecutionStatus(member.execution_status),
                ExecutionStatus(execution_status),
                EXECUTION_TRANSITIONS,
            ):
                team_logger.error(
                    f"Invalid state transition for member {member_name}: "
                    f"{member.execution_status} -> {execution_status}"
                )
                return False

            member.execution_status = execution_status
            await session.commit()
            team_logger.debug(f"Member {member_name} execution status updated to {execution_status}")
            return True

    # ----------------- Task Operations -----------------
    async def create_task(
        self,
        task_id: str,
        team_name: str,
        title: str,
        content: str,
        status: str,
    ) -> bool:
        """Create a new team task"""
        await self._ensure_initialized()
        team_task_model = _get_task_model()
        async with self.session_local() as session:
            try:
                task = team_task_model(
                    task_id=task_id,
                    team_name=team_name,
                    title=title,
                    content=content,
                    status=status,
                    updated_at=self.get_current_time(),
                )
                session.add(task)
                await session.commit()
                team_logger.info(f"Task {task_id} created")
                return True
            except IntegrityError as e:
                await session.rollback()
                team_logger.error(f"Task {task_id} already exists {e}", e)
                return False

    async def get_task(self, task_id: str) -> Optional[TeamTaskBase]:
        """Get task information by ID"""
        await self._ensure_initialized()
        team_task_model = _get_task_model()
        async with self.session_local() as session:
            result = await session.execute(select(team_task_model).where(team_task_model.task_id == task_id))
            return result.scalar_one_or_none()

    async def get_team_tasks(self, team_name: str, status: Optional[str] = None) -> List[TeamTaskBase]:
        """Get all tasks for a team, optionally filtered by status"""
        await self._ensure_initialized()
        team_task_model = _get_task_model()
        async with self.session_local() as session:
            query = select(team_task_model).where(team_task_model.team_name == team_name)
            if status:
                query = query.where(team_task_model.status == status)
            result = await session.execute(query)
            return result.scalars().all()

    async def get_tasks_by_assignee(
        self, team_name: str, assignee_id: str, status: Optional[str] = None
    ) -> List[TeamTaskBase]:
        """Get tasks assigned to a specific member, optionally filtered by status

        Args:
            team_name: Team identifier
            assignee_id: Member identifier who the tasks are assigned to
            status: Optional status filter

        Returns:
            List of TeamTaskBase objects assigned to the member
        """
        await self._ensure_initialized()
        team_task_model = _get_task_model()
        async with self.session_local() as session:
            query = select(team_task_model).where(
                team_task_model.team_name == team_name, team_task_model.assignee == assignee_id
            )
            if status:
                query = query.where(team_task_model.status == status)
            result = await session.execute(query)
            return result.scalars().all()

    async def assign_task(self, task_id: str, member_name: str) -> bool:
        """Assign a task to a member and mark it as claimed.

        Only succeeds when the task has no current assignee and its current
        status permits a transition to CLAIMED. Atomically sets the
        ``assignee`` and flips ``status`` to ``CLAIMED`` so leader-driven
        assignment matches member-driven self-claim semantics.

        Args:
            task_id: Task identifier.
            member_name: Member ID to assign.

        Returns:
            True if assigned, False if task not found, already assigned, or
            in a status that cannot transition to CLAIMED.
        """
        await self._ensure_initialized()
        team_task_model = _get_task_model()
        async with self.session_local() as session:
            result = await session.execute(select(team_task_model).where(team_task_model.task_id == task_id))
            task = result.scalar_one_or_none()
            if not task:
                team_logger.error(f"Task {task_id} not found")
                return False
            if task.assignee:
                team_logger.warning(f"Task {task_id} already assigned to {task.assignee}")
                return False
            if not is_valid_transition(
                TaskStatus(task.status),
                TaskStatus.CLAIMED,
                TASK_TRANSITIONS,
            ):
                team_logger.error(
                    f"Invalid state transition for task {task_id}: {task.status} -> {TaskStatus.CLAIMED.value}"
                )
                return False
            task.assignee = member_name
            task.status = TaskStatus.CLAIMED.value
            task.updated_at = self.get_current_time()
            await session.commit()
            team_logger.info(f"Task {task_id} assigned to {member_name} (status=claimed)")
            return True

    async def claim_task(self, task_id: str, member_name: str) -> bool:
        """Claim a task for a member"""
        await self._ensure_initialized()
        team_task_model = _get_task_model()
        async with self.session_local() as session:
            result = await session.execute(select(team_task_model).where(team_task_model.task_id == task_id))
            task = result.scalar_one_or_none()
            if not task:
                team_logger.error(f"Task {task_id} not found")
                return False

            # Conflict check first: a task already held by another member would
            # otherwise trip the state-transition check as "claimed → claimed",
            # which hides the real reason.
            if task.assignee:
                team_logger.warning(f"Task {task_id} is already claimed by member {task.assignee}")
                return False

            if not is_valid_transition(
                TaskStatus(task.status),
                TaskStatus.CLAIMED,
                TASK_TRANSITIONS,
            ):
                team_logger.error(
                    f"Invalid state transition for task {task_id}: {task.status} -> {TaskStatus.CLAIMED.value}"
                )
                return False

            task.status = TaskStatus.CLAIMED.value
            task.assignee = member_name
            task.updated_at = self.get_current_time()
            await session.commit()
            team_logger.info(f"Task {task_id} claimed by member {member_name}")
            return True

    async def reset_task(self, task_id: str) -> Optional[TeamTaskBase]:
        """Reset a claimed or plan_approved task back to pending status and clear assignee

        This method resets a task from CLAIMED or PLAN_APPROVED to PENDING and clears the assignee.
        Useful for re-assigning task to other members.
        Only tasks in CLAIMED or PLAN_APPROVED status can be reset.

        Args:
            task_id: Task identifier

        Returns:
            Task model if reset succeeded, None otherwise
        """
        await self._ensure_initialized()
        team_task_model = _get_task_model()
        async with self.session_local() as session:
            result = await session.execute(select(team_task_model).where(team_task_model.task_id == task_id))
            task = result.scalar_one_or_none()
            if not task:
                team_logger.error(f"Task {task_id} not found")
                return None

            # Only allow resetting CLAIMED tasks
            if task.status != TaskStatus.CLAIMED.value:
                team_logger.error(
                    f"Cannot reset task {task_id} with status {task.status}, only CLAIMED tasks can be reset"
                )
                return None

            # Validate state transition using state machine
            if not is_valid_transition(
                TaskStatus(task.status),
                TaskStatus.PENDING,
                TASK_TRANSITIONS,
            ):
                team_logger.error(
                    f"Invalid state transition for task {task_id}: {task.status} -> {TaskStatus.PENDING.value}"
                )
                return None

            origin_task_status = task.status
            task.status = TaskStatus.PENDING.value
            task.assignee = None
            task.updated_at = self.get_current_time()
            await session.commit()
            team_logger.info(f"Task {task_id} reset from {origin_task_status} to PENDING")

            return task

    async def approve_plan_task(self, task_id: str) -> Optional[TeamTaskBase]:
        """Approve a task plan for PLAN_MODE members

        This method transitions a task from CLAIMED to PLAN_APPROVED.
        Only tasks in CLAIMED status can be approved.

        Args:
            task_id: Task identifier

        Returns:
            Task model if approval succeeded, None otherwise
        """
        await self._ensure_initialized()
        team_task_model = _get_task_model()
        async with self.session_local() as session:
            result = await session.execute(select(team_task_model).where(team_task_model.task_id == task_id))
            task = result.scalar_one_or_none()
            if not task:
                team_logger.error(f"Task {task_id} not found")
                return None

            # Validate state transition using state machine
            if not is_valid_transition(
                TaskStatus(task.status),
                TaskStatus.PLAN_APPROVED,
                TASK_TRANSITIONS,
            ):
                team_logger.error(
                    f"Invalid state transition for task {task_id}: {task.status} -> {TaskStatus.PLAN_APPROVED.value}"
                )
                return None

            task.status = TaskStatus.PLAN_APPROVED.value
            task.updated_at = self.get_current_time()
            await session.commit()
            team_logger.info(f"Task {task_id} approved from CLAIMED to PLAN_APPROVED")

            return task

    async def update_task_status(self, task_id: str, status: str) -> bool:
        """Update task status"""
        await self._ensure_initialized()
        team_task_model = _get_task_model()
        task_dependency_model = _get_task_dependency_model()
        async with self.session_local() as session:
            result = await session.execute(select(team_task_model).where(team_task_model.task_id == task_id))
            task = result.scalar_one_or_none()
            if not task:
                team_logger.error(f"Task {task_id} not found")
                return False

            # Validate state transition
            if not is_valid_transition(
                TaskStatus(task.status),
                TaskStatus(status),
                TASK_TRANSITIONS,
            ):
                team_logger.error(f"Invalid state transition for task {task_id}: {task.status} -> {status}")
                return False

            now = self.get_current_time()
            task.status = status
            task.updated_at = now

            # Resolving dependencies is part of the completion handshake —
            # unblock downstream tasks that were waiting on this one.
            if status == TaskStatus.COMPLETED.value:
                team_logger.info(f"Task {task_id} completed at {now}")

                dep_update_result = await session.execute(
                    update(task_dependency_model)
                    .where(
                        task_dependency_model.depends_on_task_id == task_id, task_dependency_model.resolved.is_(False)
                    )
                    .values(resolved=True)
                )
                resolved_count = dep_update_result.rowcount or 0
                if resolved_count > 0:
                    team_logger.info(f"Resolved {resolved_count} dependencies for task {task_id}")

            await session.commit()
            team_logger.info(f"Task {task_id} status updated to {status}")
            return True

    async def update_task(
        self,
        task_id: str,
        title: Optional[str] = None,
        content: Optional[str] = None,
    ) -> bool:
        """Update task content (title, content, etc.)

        Args:
            task_id: Task identifier
            title: Optional new title
            content: Optional new content

        Returns:
            True if successful, False otherwise
        """
        await self._ensure_initialized()
        team_task_model = _get_task_model()
        async with self.session_local() as session:
            result = await session.execute(select(team_task_model).where(team_task_model.task_id == task_id))
            task = result.scalar_one_or_none()
            if not task:
                team_logger.error(f"Task {task_id} not found")
                return False

            if task.status == TaskStatus.CLAIMED.value or task.status == TaskStatus.PLAN_APPROVED.value:
                team_logger.error(f"Cannot update task {task_id} because it is currently {task.status}")
                return False

            # Update fields if provided
            updated = False
            if title is not None and task.title != title:
                task.title = title
                updated = True
            if content is not None and task.content != content:
                task.content = content
                updated = True

            if updated:
                await session.commit()
                team_logger.info(f"Task {task_id} updated")

            return True

    async def _refresh_status_in_session(
        self,
        session: AsyncSession,
        task_ids: Iterable[str],
        now: int,
    ) -> List[TeamTaskBase]:
        """Recompute PENDING/BLOCKED status for tasks based on unresolved deps.

        Rules:
        - ``PENDING`` with unresolved deps > 0 transitions to ``BLOCKED``.
        - ``BLOCKED`` with unresolved deps == 0 transitions to ``PENDING``.
        - All other statuses are left untouched (terminal or executing
          tasks must not be silently re-routed by edge changes).

        Args:
            session: Active SQLAlchemy session (caller manages commit).
            task_ids: Candidate task IDs to evaluate. Duplicates and
                non-existent IDs are tolerated.
            now: Wall-clock timestamp to stamp on changed tasks.

        Returns:
            Tasks whose status actually changed.
        """
        unique_ids = list({tid for tid in task_ids if tid})
        if not unique_ids:
            return []

        team_task_model = _get_task_model()
        task_dependency_model = _get_task_dependency_model()

        tasks_result = await session.execute(
            select(team_task_model).where(team_task_model.task_id.in_(unique_ids))
        )
        candidates = [
            t
            for t in tasks_result.scalars().all()
            if t.status in (TaskStatus.PENDING.value, TaskStatus.BLOCKED.value)
        ]
        if not candidates:
            return []

        candidate_ids = [t.task_id for t in candidates]
        unresolved_result = await session.execute(
            select(
                task_dependency_model.task_id,
                func.count().label("unresolved"),
            )
            .where(
                task_dependency_model.task_id.in_(candidate_ids),
                task_dependency_model.resolved.is_(False),
            )
            .group_by(task_dependency_model.task_id)
        )
        unresolved_by_task: Dict[str, int] = {row[0]: row[1] for row in unresolved_result.all()}

        refreshed: List[TeamTaskBase] = []
        for task in candidates:
            unresolved = unresolved_by_task.get(task.task_id, 0)
            if task.status == TaskStatus.PENDING.value and unresolved > 0:
                task.status = TaskStatus.BLOCKED.value
                task.updated_at = now
                refreshed.append(task)
                team_logger.info(f"Task {task.task_id} blocked ({unresolved} unresolved deps)")
            elif task.status == TaskStatus.BLOCKED.value and unresolved == 0:
                task.status = TaskStatus.PENDING.value
                task.updated_at = now
                refreshed.append(task)
                team_logger.info(f"Task {task.task_id} unblocked (all deps resolved)")
        return refreshed

    async def _terminate_task_in_session(
        self,
        session: AsyncSession,
        task_id: str,
        new_status: TaskStatus,
        now: int,
    ) -> Optional[tuple[TeamTaskBase, List[TeamTaskBase]]]:
        """Terminate a task and propagate dependency resolution downstream.

        Both ``COMPLETED`` and ``CANCELLED`` are "terminal" from the
        dependency graph's point of view: the task no longer produces
        anything new, so every edge ``X -> task_id`` (X waits on this
        task) is now resolved and X may be eligible to unblock.

        Args:
            session: Active SQLAlchemy session (caller manages commit).
            task_id: Task to terminate.
            new_status: ``TaskStatus.COMPLETED`` or ``TaskStatus.CANCELLED``.
            now: Wall-clock timestamp.

        Returns:
            ``(task, refreshed_downstream_tasks)`` on success;
            ``None`` if the task is missing or the transition is invalid.
        """
        if new_status not in (TaskStatus.COMPLETED, TaskStatus.CANCELLED):
            raise ValueError(f"_terminate_task_in_session expects a terminal status, got {new_status}")

        team_task_model = _get_task_model()
        task_dependency_model = _get_task_dependency_model()

        result = await session.execute(select(team_task_model).where(team_task_model.task_id == task_id))
        task = result.scalar_one_or_none()
        if task is None:
            team_logger.error(f"Task {task_id} not found")
            return None

        # Idempotent: a no-op if already in the target state.
        if task.status == new_status.value:
            team_logger.debug(f"Task {task_id} already {new_status.value}")
            return task, []

        if not is_valid_transition(TaskStatus(task.status), new_status, TASK_TRANSITIONS):
            team_logger.error(
                f"Invalid state transition for task {task_id}: {task.status} -> {new_status.value}"
            )
            return None

        task.status = new_status.value
        task.updated_at = now
        team_logger.info(f"Task {task_id} {new_status.value} at {now}")

        # Resolve outgoing edges: every X that was waiting on this task is now released.
        dep_update_result = await session.execute(
            update(task_dependency_model)
            .where(
                task_dependency_model.depends_on_task_id == task_id,
                task_dependency_model.resolved.is_(False),
            )
            .values(resolved=True)
        )
        resolved_count = dep_update_result.rowcount or 0
        if resolved_count > 0:
            team_logger.info(f"Resolved {resolved_count} dependencies for task {task_id}")

        downstream_result = await session.execute(
            select(task_dependency_model.task_id)
            .where(task_dependency_model.depends_on_task_id == task_id)
            .distinct()
        )
        downstream_ids = {row[0] for row in downstream_result.all()}

        refreshed = await self._refresh_status_in_session(session, downstream_ids, now)
        return task, refreshed

    async def mutate_dependency_graph(
        self,
        team_name: str,
        *,
        new_tasks: Optional[List[NewTaskSpec]] = None,
        add_edges: Optional[List[tuple[str, str]]] = None,
    ) -> GraphMutationResult:
        """Atomic dependency-graph mutation: insert nodes and/or edges.

        Single entry point for every structural change to the dependency
        graph. Cycle detection runs against the post-mutation graph (so
        a batch of edges that individually look fine but collectively
        close a loop is rejected as one). After the writes succeed, the
        affected tasks are passed through the status-refresh pass so
        ``BLOCKED``/``PENDING`` always reflects the current edge state.

        Args:
            team_name: Team identifier (applied to all new rows).
            new_tasks: Tasks to create. Their ``initial_status`` is the
                seed; the refresh pass may flip ``PENDING`` to
                ``BLOCKED`` if edges land that gate them.
            add_edges: Edges as ``(task_id, depends_on_task_id)`` tuples.
                Both endpoints must exist after ``new_tasks`` are
                applied. Adding an edge to an executing/terminal task
                (CLAIMED / PLAN_APPROVED / COMPLETED / CANCELLED) is
                rejected — the assignee is mid-flight or the work is
                already settled.

        Returns:
            ``GraphMutationResult`` with the cause on failure and the
            list of tasks whose status changed during refresh on success.
        """
        new_tasks = list(new_tasks or [])
        add_edges = list(add_edges or [])
        if not new_tasks and not add_edges:
            return GraphMutationResult.success()

        await self._ensure_initialized()
        team_task_model = _get_task_model()
        task_dependency_model = _get_task_dependency_model()

        async with self.session_local() as session:
            try:
                now = self.get_current_time()

                # 1. Insert new tasks (so cycle check and endpoint validation see them).
                seen_new_ids: set[str] = set()
                for spec in new_tasks:
                    if spec.task_id in seen_new_ids:
                        await session.rollback()
                        return GraphMutationResult.fail(f"Duplicate task_id {spec.task_id} in new_tasks")
                    seen_new_ids.add(spec.task_id)
                    session.add(
                        team_task_model(
                            task_id=spec.task_id,
                            team_name=team_name,
                            title=spec.title,
                            content=spec.content,
                            status=spec.initial_status,
                            updated_at=now,
                        )
                    )
                if new_tasks:
                    await session.flush()  # surface task_id collisions before edge work

                # 2. Validate edge endpoints (existence + non-terminal source).
                edge_endpoints: set[str] = set()
                for tid, dep_id in add_edges:
                    edge_endpoints.add(tid)
                    edge_endpoints.add(dep_id)

                endpoint_tasks: Dict[str, TeamTaskBase] = {}
                if edge_endpoints:
                    endpoint_result = await session.execute(
                        select(team_task_model).where(team_task_model.task_id.in_(list(edge_endpoints)))
                    )
                    endpoint_tasks = {t.task_id: t for t in endpoint_result.scalars().all()}

                for tid, dep_id in add_edges:
                    if tid not in endpoint_tasks:
                        await session.rollback()
                        return GraphMutationResult.fail(f"Task {tid} not found")
                    if dep_id not in endpoint_tasks:
                        await session.rollback()
                        return GraphMutationResult.fail(f"Dependency target {dep_id} not found")
                    src_status = endpoint_tasks[tid].status
                    if src_status in _TASK_DEPENDENCY_REJECT_STATUSES:
                        await session.rollback()
                        return GraphMutationResult.fail(
                            f"Cannot add dependency to {tid} in terminal or executing status: {src_status}"
                        )

                # 3. Build the post-mutation adjacency and run a single cycle check.
                existing_edges_rows = (
                    await session.execute(
                        select(
                            task_dependency_model.task_id,
                            task_dependency_model.depends_on_task_id,
                        ).where(task_dependency_model.team_name == team_name)
                    )
                ).all()
                existing_edge_set: set[tuple[str, str]] = {(row[0], row[1]) for row in existing_edges_rows}
                adjacency: Dict[str, List[str]] = {}
                for src, dst in existing_edge_set:
                    adjacency.setdefault(src, []).append(dst)

                new_edge_set: set[tuple[str, str]] = set()
                for tid, dep_id in add_edges:
                    edge = (tid, dep_id)
                    if edge in existing_edge_set or edge in new_edge_set:
                        continue  # idempotent — no-op for already-present edges
                    new_edge_set.add(edge)
                    adjacency.setdefault(tid, []).append(dep_id)

                cycle = detect_cycle_in_adjacency(adjacency)
                if cycle is not None:
                    await session.rollback()
                    return GraphMutationResult.fail(f"Circular dependency detected: {' -> '.join(cycle)}")

                # 4. Insert new edges. An edge whose target is already terminal is
                # born resolved — skip the redundant unblock round-trip later.
                for tid, dep_id in new_edge_set:
                    dep_status = endpoint_tasks[dep_id].status
                    initial_resolved = dep_status in _TASK_TERMINAL_STATUSES
                    session.add(
                        task_dependency_model(
                            task_id=tid,
                            depends_on_task_id=dep_id,
                            team_name=team_name,
                            resolved=initial_resolved,
                        )
                    )
                if new_edge_set:
                    await session.flush()

                # 5. Refresh status. Affected = newly created tasks (which start
                # at their seed status) plus any task that gained a new edge.
                affected_ids: set[str] = {spec.task_id for spec in new_tasks}
                affected_ids.update(tid for tid, _ in new_edge_set)
                refreshed = await self._refresh_status_in_session(session, affected_ids, now)

                await session.commit()

                if new_tasks:
                    team_logger.info(
                        f"Created {len(new_tasks)} task(s); "
                        f"added {len(new_edge_set)} edge(s); refreshed {len(refreshed)} task(s)"
                    )
                else:
                    team_logger.info(
                        f"Added {len(new_edge_set)} edge(s); refreshed {len(refreshed)} task(s)"
                    )
                return GraphMutationResult.success(refreshed_tasks=list(refreshed))

            except IntegrityError as e:
                await session.rollback()
                team_logger.error(f"mutate_dependency_graph integrity error: {e}")
                return GraphMutationResult.fail(f"Integrity error: {e}")
            except Exception as e:
                await session.rollback()
                team_logger.error(f"mutate_dependency_graph unexpected error: {e}")
                return GraphMutationResult.fail(f"Unexpected error: {e}")

    async def add_task_with_bidirectional_dependencies(
        self,
        task_id: str,
        team_name: str,
        title: str,
        content: str,
        status: str,
        *,
        dependencies: Optional[List[str]] = None,
        dependent_task_ids: Optional[List[str]] = None,
    ) -> bool:
        """Create a task and wire it into the dependency chain atomically.

        Thin wrapper over ``mutate_dependency_graph`` that translates the
        legacy "new task with upstream/downstream lists" shape into the
        unified ``new_tasks + add_edges`` form.

        Args:
            task_id: ID of the new task to create.
            team_name: Team identifier.
            title: Task title.
            content: Task content.
            status: Initial seed status (refresh pass may adjust).
            dependencies: Existing task IDs the new task should depend on.
            dependent_task_ids: Existing task IDs that should depend on
                the new task.

        Returns:
            True on success; False on any rejection (cycle, missing
            endpoint, terminal/executing dependent, task_id collision).
        """
        edges: List[tuple[str, str]] = []
        for dep_id in dependencies or ():
            edges.append((task_id, dep_id))
        for dependent_id in dependent_task_ids or ():
            edges.append((dependent_id, task_id))

        result = await self.mutate_dependency_graph(
            team_name=team_name,
            new_tasks=[
                NewTaskSpec(
                    task_id=task_id,
                    title=title,
                    content=content,
                    initial_status=status,
                )
            ],
            add_edges=edges,
        )
        if not result.ok:
            team_logger.error(f"Failed to create task {task_id}: {result.reason}")
        return result.ok

    async def get_task_dependencies(self, task_id: str) -> List[TeamTaskDependencyBase]:
        """Get all dependencies for a task"""
        await self._ensure_initialized()
        task_dependency_model = _get_task_dependency_model()
        async with self.session_local() as session:
            result = await session.execute(
                select(task_dependency_model).where(task_dependency_model.task_id == task_id)
            )
            rows = result.scalars().all()
            return rows

    async def get_unresolved_dependencies_count(self, task_id: str) -> int:
        """Get count of unresolved dependencies for a task

        Args:
            task_id: Task identifier

        Returns:
            Number of unresolved dependencies (resolved is False)
        """
        await self._ensure_initialized()
        task_dependency_model = _get_task_dependency_model()
        async with self.session_local() as session:
            result = await session.execute(
                select(task_dependency_model).where(
                    task_dependency_model.task_id == task_id, task_dependency_model.resolved.is_(False)
                )
            )
            return len(result.scalars().all())

    async def get_tasks_depending_on(self, depends_on_task_id: str) -> List[TeamTaskBase]:
        """Get all tasks that depend on a specific task

        Args:
            depends_on_task_id: Task ID that other tasks depend on

        Returns:
            List of tasks that depend on the given task
        """
        await self._ensure_initialized()
        task_dependency_model = _get_task_dependency_model()
        team_task_model = _get_task_model()
        async with self.session_local() as session:
            # Query dependencies where depends_on_task_id matches
            result = await session.execute(
                select(task_dependency_model).where(task_dependency_model.depends_on_task_id == depends_on_task_id)
            )
            deps = result.scalars().all()

            # Get the actual tasks for each dependency
            tasks = []
            for dep in deps:
                task_result = await session.execute(
                    select(team_task_model).where(team_task_model.task_id == dep.task_id)
                )
                task = task_result.scalar_one_or_none()
                if task:
                    tasks.append(task)

            return tasks

    async def delete_task(self, task_id: str) -> bool:
        """Delete a task"""
        await self._ensure_initialized()
        team_task_model = _get_task_model()
        async with self.session_local() as session:
            result = await session.execute(select(team_task_model).where(team_task_model.task_id == task_id))
            task = result.scalar_one_or_none()
            if not task:
                team_logger.debug(f"Task {task_id} not found for deletion")
                return False

            await session.delete(task)
            await session.commit()
            team_logger.info(f"Task {task_id} deleted")
            return True

    async def cancel_task(self, task_id: str) -> Optional[Dict]:
        """Cancel a task atomically and unblock dependent tasks.

        Cancelling is a node-termination operation: the task no longer
        produces work, so every downstream task waiting on it has its
        edge resolved and is re-evaluated for unblocking. ``CANCELLED``
        and ``COMPLETED`` are equivalent from the dependency graph's
        point of view; both flow through ``_terminate_task_in_session``.

        Args:
            task_id: Task identifier.

        Returns:
            ``{"task": TeamTaskBase, "unblocked_tasks": [TeamTaskBase, ...]}``
            on success; ``None`` if the task is missing or the
            transition is invalid. Mirrors ``complete_task``'s shape so
            both terminal paths return the same envelope.
        """
        await self._ensure_initialized()
        async with self.session_local() as session:
            now = self.get_current_time()
            outcome = await self._terminate_task_in_session(
                session,
                task_id=task_id,
                new_status=TaskStatus.CANCELLED,
                now=now,
            )
            if outcome is None:
                return None
            task, unblocked = outcome
            await session.commit()
            return {"task": task, "unblocked_tasks": unblocked}

    async def cancel_all_tasks(
        self,
        team_name: str,
        skip_assignees: Optional[set[str]] = None,
    ) -> Dict:
        """Cancel every active task for a team atomically.

        Bulk cancellation walks the team's non-terminal tasks in a single
        transaction and routes each through ``_terminate_task_in_session``
        so downstream edges are resolved and dependents re-evaluated.
        Tasks already in CANCELLED/COMPLETED are skipped.

        Args:
            team_name: Team identifier.
            skip_assignees: Member names whose claimed tasks must be
                preserved (used to honor the HITT human_agent lock).

        Returns:
            ``{"cancelled_tasks": [...], "unblocked_tasks": [...]}``.
            ``unblocked_tasks`` aggregates every task that flipped from
            BLOCKED to PENDING during the cascade — typically rare
            because most blocked tasks share the same upstream chain
            that's also being cancelled, but possible when a blocked
            task waits on both a cancelled and a non-cancelled chain.
        """
        await self._ensure_initialized()
        team_task_model = _get_task_model()
        skip_assignees = skip_assignees or set()
        async with self.session_local() as session:
            skip_statuses = [TaskStatus.CANCELLED.value, TaskStatus.COMPLETED.value]
            result = await session.execute(
                select(team_task_model.task_id, team_task_model.assignee).where(
                    team_task_model.team_name == team_name,
                    ~team_task_model.status.in_(skip_statuses),
                )
            )
            candidates = [(row[0], row[1]) for row in result.all()]
            if not candidates:
                team_logger.info(f"No active tasks to cancel for team {team_name}")
                return {"cancelled_tasks": [], "unblocked_tasks": []}

            now = self.get_current_time()
            cancelled_tasks: List[TeamTaskBase] = []
            unblocked_by_id: Dict[str, TeamTaskBase] = {}
            for task_id, assignee in candidates:
                if assignee in skip_assignees:
                    team_logger.debug(f"Skipping task {task_id}: assignee '{assignee}' in skip_assignees")
                    continue
                outcome = await self._terminate_task_in_session(
                    session,
                    task_id=task_id,
                    new_status=TaskStatus.CANCELLED,
                    now=now,
                )
                if outcome is None:
                    continue
                cancelled, refreshed = outcome
                cancelled_tasks.append(cancelled)
                # Dedupe: a task could refresh into PENDING after one
                # ancestor cancels and refresh again into something else
                # later — we only want the final state and we want it once.
                for t in refreshed:
                    unblocked_by_id[t.task_id] = t

            await session.commit()
            # Filter out tasks that ended up cancelled themselves — they
            # can't also be reported as "unblocked".
            cancelled_ids = {t.task_id for t in cancelled_tasks}
            unblocked_tasks = [t for tid, t in unblocked_by_id.items() if tid not in cancelled_ids]
            team_logger.info(
                f"Cancelled {len(cancelled_tasks)} tasks for team {team_name}; "
                f"unblocked {len(unblocked_tasks)}"
            )
            return {"cancelled_tasks": cancelled_tasks, "unblocked_tasks": unblocked_tasks}

    async def complete_task(self, task_id: str) -> Optional[Dict]:
        """Complete a task atomically and unblock dependent tasks.

        Thin wrapper over ``_terminate_task_in_session`` with
        ``COMPLETED`` semantics. The result preserves the legacy
        ``{"task": ..., "unblocked_tasks": ...}`` shape so existing
        callers (``TeamTaskManager.complete``) keep working unchanged.

        Args:
            task_id: Task identifier.

        Returns:
            Dictionary with the completed task and any tasks that
            transitioned out of BLOCKED as a result, or ``None`` if the
            task is missing or the transition is invalid.
        """
        await self._ensure_initialized()
        async with self.session_local() as session:
            now = self.get_current_time()
            outcome = await self._terminate_task_in_session(
                session,
                task_id=task_id,
                new_status=TaskStatus.COMPLETED,
                now=now,
            )
            if outcome is None:
                return None
            task, unblocked = outcome
            await session.commit()
            return {"task": task, "unblocked_tasks": unblocked}

    async def _verify_and_fix_blocked_tasks(self, team_name: str) -> List[TeamTaskBase]:
        """Recovery sweep: re-evaluate every BLOCKED task in the team.

        Wraps ``_refresh_status_in_session`` over the team's BLOCKED set
        so the recovery path uses the same status-transition rules as
        normal mutations. Useful after a crash/restart or manual DB
        intervention has left status drifted from the dependency graph.

        Args:
            team_name: Team identifier.

        Returns:
            Tasks transitioned out of BLOCKED.
        """
        await self._ensure_initialized()
        team_task_model = _get_task_model()
        async with self.session_local() as session:
            result = await session.execute(
                select(team_task_model.task_id).where(
                    team_task_model.team_name == team_name,
                    team_task_model.status == TaskStatus.BLOCKED.value,
                )
            )
            blocked_ids = [row[0] for row in result.all()]
            if not blocked_ids:
                return []

            now = self.get_current_time()
            refreshed = await self._refresh_status_in_session(session, blocked_ids, now)
            await session.commit()
            return refreshed

    async def verify_and_fix_task_consistency(self, team_name: str) -> List[TeamTaskBase]:
        """Verify and fix task consistency for a team

        This method checks for data consistency issues and fixes them automatically.
        It is designed for recovery scenarios such as:
        - System crash/restart after task completion
        - Manual database intervention
        - Distributed system reconciliation

        This method is not intended for normal workflow use - task completion
        via complete() handles dependency resolution automatically.

        Args:
            team_name: Team identifier

        Returns:
            List of task models that were updated from BLOCKED to PENDING

        Example:
            # Recovery scenario after system restart
            fixed_tasks = await db.verify_and_fix_task_consistency(team_name="my_team")
            team_logger.info(f"Fixed {len(fixed_tasks)} tasks during recovery")
        """
        return await self._verify_and_fix_blocked_tasks(team_name)

    # ----------------- Message Operations -----------------
    async def get_message(self, message_id: str) -> Optional[TeamMessageBase]:
        """Get message information by ID"""
        await self._ensure_initialized()
        message_model = _get_message_model()
        async with self.session_local() as session:
            result = await session.execute(select(message_model).where(message_model.message_id == message_id))
            return result.scalar_one_or_none()

    async def create_message(
        self,
        message_id: str,
        team_name: str,
        from_member_name: str,
        content: str,
        *,
        to_member_name: Optional[str] = None,
        broadcast: bool = False,
        is_read: bool = False,
    ) -> bool:
        """Create a new team message.

        Args:
            is_read: Initial read flag for direct messages. Used to mark
                messages addressed to members with no live consumer (e.g.
                the HITT human_agent) as already read so mailbox polling
                does not keep re-firing on them. Ignored for broadcasts,
                whose per-member read state lives in MessageReadStatus.
        """
        await self._ensure_initialized()
        message_model = _get_message_model()
        for attempt in range(_DB_RETRY_ATTEMPTS):
            async with self.session_local() as session:
                try:
                    message = message_model(
                        message_id=message_id,
                        team_name=team_name,
                        from_member_name=from_member_name,
                        to_member_name=to_member_name,
                        content=content,
                        timestamp=self.get_current_time(),
                        broadcast=broadcast,
                        # Broadcast rows must leave is_read NULL — per-member
                        # read state lives in MessageReadStatus instead.
                        is_read=None if broadcast else is_read,
                    )
                    session.add(message)
                    await session.commit()
                    team_logger.info(f"Message {message_id} created")
                    return True
                except IntegrityError as e:
                    await session.rollback()
                    team_logger.error(f"Failed to create {message_id}, reason is {e}")
                    return False
                except OperationalError as e:
                    await session.rollback()
                    if attempt < _DB_RETRY_ATTEMPTS - 1:
                        delay = _DB_RETRY_BASE_DELAY * (2**attempt)
                        team_logger.warning(
                            f"Database locked on create_message (attempt {attempt + 1}), retrying in {delay}s"
                        )
                        await asyncio.sleep(delay)
                    else:
                        team_logger.error(
                            f"Failed to create message {message_id} after {_DB_RETRY_ATTEMPTS} attempts: {e}"
                        )
                        return False
        return False

    async def get_messages(
        self,
        team_name: str,
        to_member_name: str,
        unread_only: bool = False,
        from_member_name: Optional[str] = None,
    ) -> List[TeamMessageBase]:
        """Get direct (point-to-point) messages for a specific member

        Args:
            team_name: Team identifier
            to_member_name: Member ID who is recipient of the messages
            unread_only: If True only return unread messages, if False return all
            from_member_name: Optional filter for messages from a specific sender

        Returns:
            List of message models
        """
        await self._ensure_initialized()
        message_model = _get_message_model()
        async with self.session_local() as session:
            # Base query for direct messages to specified member
            query = select(message_model).where(
                message_model.team_name == team_name,
                message_model.to_member_name == to_member_name,
                message_model.broadcast.is_(False),
            )

            if from_member_name is not None:
                query = query.where(message_model.from_member_name == from_member_name)

            if unread_only:
                query = query.where(message_model.is_read.is_(False))

            query = query.order_by(message_model.timestamp)
            result = await session.execute(query)
            rows = result.scalars().all()

            return rows

    async def get_broadcast_messages(
        self,
        team_name: str,
        member_name: str,
        unread_only: bool = False,
        from_member_name: Optional[str] = None,
    ) -> List[TeamMessageBase]:
        """Get broadcast messages for a specific member, with read status

        Args:
            team_name: Team identifier
            member_name: Member ID to check read status for
            unread_only: If True only return unread messages, if False return all
            from_member_name: Optional filter for messages from a specific sender

        Returns:
            List of message models with read status information
        """
        await self._ensure_initialized()
        message_model = _get_message_model()
        read_status_model = _get_message_read_status_model()
        async with self.session_local() as session:
            # Base query for broadcast messages
            query = select(message_model).where(
                message_model.team_name == team_name,
                message_model.broadcast.is_(True),
                message_model.from_member_name != member_name,
            )

            if from_member_name is not None:
                query = query.where(message_model.from_member_name == from_member_name)

            query = query.order_by(message_model.timestamp)
            result = await session.execute(query)
            rows = result.scalars().all()

            # Fetch read status once for this member+team
            read_result = await session.execute(
                select(read_status_model).where(
                    read_status_model.member_name == member_name,
                    read_status_model.team_name == team_name,
                )
            )
            read_status = read_result.scalar_one_or_none()

            if not unread_only:
                return list(rows)

            return [row for row in rows if read_status is None or row.timestamp > read_status.read_at]

    async def get_team_messages(self, team_name: str, broadcast: Optional[bool] = None) -> List[TeamMessageBase]:
        """Get all messages for a team (without read status)

        Args:
            team_name: Team identifier
            broadcast: Optional filter for broadcast (True) or direct (False) messages

        Returns:
            List of message models
        """
        await self._ensure_initialized()
        message_model = _get_message_model()
        async with self.session_local() as session:
            query = select(message_model).where(message_model.team_name == team_name)

            if broadcast is not None:
                query = query.where(message_model.broadcast.is_(broadcast))

            query = query.order_by(message_model.timestamp)
            result = await session.execute(query)
            rows = result.scalars().all()
            return rows

    async def mark_message_read(self, message_id: str, member_name: str) -> bool:
        """Mark a message as read by a member (works for both direct and broadcast messages)

        Args:
            message_id: Message identifier
            member_name: Member ID who is reading the message

        Returns:
            True if successful, False otherwise
        """
        await self._ensure_initialized()
        message_model = _get_message_model()
        read_status_model = _get_message_read_status_model()
        async with self.session_local() as session:
            # Verify message exists
            result = await session.execute(select(message_model).where(message_model.message_id == message_id))
            message = result.scalar_one_or_none()
            if not message:
                team_logger.error(f"Message {message_id} not found")
                return False

            # "user" is the pseudo-member representing the human caller and
            # has no row in TeamMember. Skip the existence check for it on
            # direct messages so the leader can mark a teammate→user reply
            # as read on the user's behalf. Broadcasts to "user" are
            # nonsensical and rejected.
            if member_name == "user":
                if message.broadcast:
                    team_logger.error(f"'user' pseudo-member cannot read broadcast message {message_id}")
                    return False
            else:
                result = await session.execute(
                    select(TeamMember).where(
                        TeamMember.member_name == member_name, TeamMember.team_name == message.team_name
                    )
                )
                member = result.scalar_one_or_none()
                if not member:
                    team_logger.error(f"Member {member_name} not found")
                    return False

            if message.broadcast:
                # ``read_at`` is a high-water mark: get_broadcast_messages
                # treats a row as unread iff ``timestamp > read_at``. The
                # update must be monotonic — overwriting unconditionally
                # would regress the marker if an older broadcast is acked
                # after a newer one, silently re-surfacing already-read
                # broadcasts in the unread queue.
                read_result = await session.execute(
                    select(read_status_model).where(
                        read_status_model.member_name == member_name,
                        read_status_model.team_name == message.team_name,
                    )
                )
                read_status = read_result.scalar_one_or_none()
                if read_status is None:
                    read_status = read_status_model(
                        member_name=member_name,
                        team_name=message.team_name,
                        read_at=message.timestamp,
                    )
                    session.add(read_status)
                elif read_status.read_at is None or message.timestamp > read_status.read_at:
                    read_status.read_at = message.timestamp
            else:
                message.is_read = True

            await session.commit()

            team_logger.info(f"Message {message_id} marked as read by {member_name}")
            return True
