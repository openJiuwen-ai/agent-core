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
    List,
    Optional,
)

from pydantic import BaseModel
from sqlalchemy import (
    event,
    func,
    select,
    update,
)
from sqlalchemy.exc import (
    IntegrityError,
    OperationalError,
)
from sqlalchemy.ext.asyncio import (
    async_sessionmaker,
    AsyncEngine,
    AsyncSession,
    create_async_engine,
)
from sqlalchemy.pool import AsyncAdaptedQueuePool, StaticPool
from sqlmodel import SQLModel

from openjiuwen.agent_teams.schema.status import (
    EXECUTION_TRANSITIONS,
    ExecutionStatus,
    is_valid_transition,
    MEMBER_TRANSITIONS,
    MemberMode,
    MemberStatus,
    TASK_TRANSITIONS,
    TaskStatus,
)
from openjiuwen.agent_teams.spawn.context import get_session_id
from openjiuwen.agent_teams.tools.models import (
    _get_message_model,
    _get_message_read_status_model,
    _get_task_dependency_model,
    _get_task_model,
    Team,
    TeamMember,
    TeamMessageBase,
    TeamTaskBase,
    TeamTaskDependencyBase,
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
        if self.config.db_type == DatabaseType.SQLITE:
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
        else:
            raise NotImplementedError(
                f"Database type {self.config.db_type} not yet implemented"
            )

        # Create session factory
        self.session_local = async_sessionmaker(
            bind=self.engine,
            class_=AsyncSession,
            expire_on_commit=False,
            autoflush=False
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

        team_logger.info(f"Dropped dynamic tables for session {session_id}")

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
    async def create_team(self, team_name: str, display_name: str, leader_member_name: str,
                          desc: Optional[str] = None, prompt: Optional[str] = None) -> bool:
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
            result = await session.execute(
                select(Team.updated_at).where(Team.team_name == team_name)
            )
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
        model_config_json: Optional[str] = None,
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
                    model_config_json=model_config_json,
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
                select(TeamMember).where(
                    TeamMember.member_name == member_name,
                    TeamMember.team_name == team_name
                )
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
                select(func.max(TeamMember.updated_at)).where(
                    TeamMember.team_name == team_name
                )
            )
            value = result.scalar_one_or_none()
            return int(value) if value is not None else 0

    async def update_member_status(self, member_name: str, team_name: str, status: str) -> bool:
        """Update member status"""
        await self._ensure_initialized()
        async with self.session_local() as session:
            result = await session.execute(
                select(TeamMember).where(
                    TeamMember.member_name == member_name,
                    TeamMember.team_name == team_name
                )
            )
            member = result.scalar_one_or_none()
            if not member:
                team_logger.error(f"Member {member_name} not found in team {team_name}")
                return False

            # Validate state transition
            if not is_valid_transition(
                MemberStatus(member.status),
                MemberStatus(status),
                MEMBER_TRANSITIONS
            ):
                team_logger.error(
                    f"Invalid state transition for member {member_name}: "
                    f"{member.status} -> {status}"
                )
                return False

            member.status = status
            await session.commit()
            team_logger.info(f"Member {member_name} status updated to {status}")
            return True

    async def update_member_execution_status(self, member_name: str, team_name: str, execution_status: str) -> bool:
        """Update member execution status"""
        await self._ensure_initialized()
        async with self.session_local() as session:
            result = await session.execute(
                select(TeamMember).where(
                    TeamMember.member_name == member_name,
                    TeamMember.team_name == team_name
                )
            )
            member = result.scalar_one_or_none()
            if not member:
                team_logger.error(f"Member {member_name} not found in team {team_name}")
                return False

            # Validate state transition
            if not is_valid_transition(
                ExecutionStatus(member.execution_status),
                ExecutionStatus(execution_status),
                EXECUTION_TRANSITIONS
            ):
                team_logger.error(
                    f"Invalid state transition for member {member_name}: "
                    f"{member.execution_status} -> {execution_status}"
                )
                return False

            member.execution_status = execution_status
            await session.commit()
            team_logger.info(f"Member {member_name} execution status updated to {execution_status}")
            return True

    # ----------------- Task Operations -----------------
    async def create_task(
        self,
        task_id: str,
        team_name: str,
        title: str,
        content: str,
        status: str
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
                    status=status
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
            result = await session.execute(
                select(team_task_model).where(team_task_model.task_id == task_id)
            )
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

    async def get_tasks_by_assignee(self, team_name: str, assignee_id: str, status: Optional[str] = None
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
                team_task_model.team_name == team_name,
                team_task_model.assignee == assignee_id
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
            result = await session.execute(
                select(team_task_model).where(team_task_model.task_id == task_id)
            )
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
                TASK_TRANSITIONS
            ):
                team_logger.error(
                    f"Invalid state transition for task {task_id}: "
                    f"{task.status} -> {TaskStatus.CLAIMED.value}"
                )
                return False
            task.assignee = member_name
            task.status = TaskStatus.CLAIMED.value
            await session.commit()
            team_logger.info(f"Task {task_id} assigned to {member_name} (status=claimed)")
            return True

    async def claim_task(self, task_id: str, member_name: str) -> bool:
        """Claim a task for a member"""
        await self._ensure_initialized()
        team_task_model = _get_task_model()
        async with self.session_local() as session:
            result = await session.execute(
                select(team_task_model).where(team_task_model.task_id == task_id)
            )
            task = result.scalar_one_or_none()
            if not task:
                team_logger.error(f"Task {task_id} not found")
                return False

            # Validate state transition
            if not is_valid_transition(
                TaskStatus(task.status),
                TaskStatus.CLAIMED,
                TASK_TRANSITIONS
            ):
                team_logger.error(
                    f"Invalid state transition for task {task_id}: "
                    f"{task.status} -> {TaskStatus.CLAIMED.value}"
                )
                return False

            if task.assignee:
                team_logger.warning(f"Task {task_id} is already claimed by member {task.assignee}")
                return False

            task.status = TaskStatus.CLAIMED.value
            task.assignee = member_name
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
            result = await session.execute(
                select(team_task_model).where(team_task_model.task_id == task_id)
            )
            task = result.scalar_one_or_none()
            if not task:
                team_logger.error(f"Task {task_id} not found")
                return None

            # Only allow resetting CLAIMED tasks
            if task.status != TaskStatus.CLAIMED.value:
                team_logger.error(
                    f"Cannot reset task {task_id} with status {task.status}, "
                    f"only CLAIMED tasks can be reset"
                )
                return None

            # Validate state transition using state machine
            if not is_valid_transition(
                TaskStatus(task.status),
                TaskStatus.PENDING,
                TASK_TRANSITIONS
            ):
                team_logger.error(
                    f"Invalid state transition for task {task_id}: "
                    f"{task.status} -> {TaskStatus.PENDING.value}"
                )
                return None

            origin_task_status = task.status
            task.status = TaskStatus.PENDING.value
            task.assignee = None
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
            result = await session.execute(
                select(team_task_model).where(team_task_model.task_id == task_id)
            )
            task = result.scalar_one_or_none()
            if not task:
                team_logger.error(f"Task {task_id} not found")
                return None

            # Validate state transition using state machine
            if not is_valid_transition(
                TaskStatus(task.status),
                TaskStatus.PLAN_APPROVED,
                TASK_TRANSITIONS
            ):
                team_logger.error(
                    f"Invalid state transition for task {task_id}: "
                    f"{task.status} -> {TaskStatus.PLAN_APPROVED.value}"
                )
                return None

            task.status = TaskStatus.PLAN_APPROVED.value
            await session.commit()
            team_logger.info(f"Task {task_id} approved from CLAIMED to PLAN_APPROVED")

            return task

    async def update_task_status(self, task_id: str, status: str) -> bool:
        """Update task status"""
        await self._ensure_initialized()
        team_task_model = _get_task_model()
        task_dependency_model = _get_task_dependency_model()
        async with self.session_local() as session:
            result = await session.execute(
                select(team_task_model).where(team_task_model.task_id == task_id)
            )
            task = result.scalar_one_or_none()
            if not task:
                team_logger.error(f"Task {task_id} not found")
                return False

            # Validate state transition
            if not is_valid_transition(
                TaskStatus(task.status),
                TaskStatus(status),
                TASK_TRANSITIONS
            ):
                team_logger.error(
                    f"Invalid state transition for task {task_id}: "
                    f"{task.status} -> {status}"
                )
                return False

            task.status = status

            # When completing a task, set completed_at and resolve dependencies
            # Using the same timestamp for both to maintain consistency
            if status == TaskStatus.COMPLETED.value:
                now = self.get_current_time()
                task.completed_at = now
                team_logger.info(f"Task {task_id} completed at {now}")

                # Resolve all dependencies that depend on this task
                # This unblocks tasks that were waiting for this one
                dep_update_result = await session.execute(
                    update(task_dependency_model).where(
                        task_dependency_model.depends_on_task_id == task_id,
                        task_dependency_model.resolved.is_(False)
                    ).values(resolved=True)
                )
                resolved_count = dep_update_result.rowcount or 0
                if resolved_count > 0:
                    team_logger.info(f"Resolved {resolved_count} dependencies for task {task_id}")

            await session.commit()
            team_logger.info(f"Task {task_id} status updated to {status}")
            return True

    async def update_task(self, task_id: str, title: Optional[str] = None, content: Optional[str] = None) -> bool:
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
            result = await session.execute(
                select(team_task_model).where(team_task_model.task_id == task_id)
            )
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

    async def add_task_dependency(self, task_id: str, depends_on_task_id: str, team_name: str) -> bool:
        """Add a task dependency"""
        await self._ensure_initialized()
        task_dependency_model = _get_task_dependency_model()
        async with self.session_local() as session:
            try:
                dependency = task_dependency_model(
                    task_id=task_id,
                    depends_on_task_id=depends_on_task_id,
                    team_name=team_name
                )
                session.add(dependency)
                await session.commit()
                team_logger.info(f"Task dependency added: {task_id} -> {depends_on_task_id}")
                return True
            except IntegrityError:
                await session.rollback()
                team_logger.debug(f"Dependency {task_id} -> {depends_on_task_id} already exists")
                return True

    async def _check_circular_dependency(
        self,
        session,
        task_id: str,
        target_task_id: str,
        visited: Optional[set] = None
    ) -> bool:
        """Check if adding a dependency from task_id to target_task_id would create a cycle

        This performs a DFS to see if target_task_id transitively depends on task_id.

        Args:
            session: SQLAlchemy session
            task_id: Task that would depend on target_task_id
            target_task_id: Task that task_id would depend on
            visited: Set of visited tasks (for recursion)

        Returns:
            True if cycle would be created, False otherwise
        """
        if visited is None:
            visited = set()

        # Check if we've reached the task_id (cycle detected)
        if target_task_id == task_id:
            return True

        # Prevent infinite recursion
        if target_task_id in visited:
            return False
        visited.add(target_task_id)

        # Get all tasks that target_task_id depends on
        task_dependency_model = _get_task_dependency_model()
        result = await session.execute(
            select(task_dependency_model).where(
                task_dependency_model.task_id == target_task_id
            )
        )
        dependencies = result.scalars().all()

        # Recursively check each dependency
        for dep in dependencies:
            if await self._check_circular_dependency(
                session, task_id, dep.depends_on_task_id, visited
            ):
                return True

        return False

    async def add_task_with_bidirectional_dependencies(
        self,
        task_id: str,
        team_name: str,
        title: str,
        content: str,
        status: str,
        *,
        dependencies: Optional[List[str]] = None,
        dependent_task_ids: Optional[List[str]] = None
    ) -> bool:
        """Create a task with bidirectional dependencies (insert into task dependency chain)

        This operation is performed atomically to ensure concurrent safety.
        The new task can depend on existing tasks and/or have existing tasks depend on it.

        Cycle detection: This method prevents circular dependencies by checking if
        adding specified dependencies would create a cycle in the dependency graph.

        Args:
            task_id: ID of the new task to create
            team_name: Team identifier
            title: Task title
            content: Task content
            status: Task status
            dependencies: List of existing task IDs that are new task depends on
            dependent_task_ids: List of existing task IDs that should depend on the new task

        Returns:
            True if successful, False otherwise (e.g., circular dependency detected)
        """
        await self._ensure_initialized()
        team_task_model = _get_task_model()
        task_dependency_model = _get_task_dependency_model()
        async with self.session_local() as session:
            try:
                # 1. Check for circular dependencies before making any changes
                # New task A depends on B -> check if B transitively depends on A (would be cycle)
                if dependencies:
                    for dep_id in dependencies:
                        # Check if dep_id (or its transitive dependencies) leads back to task_id
                        result = await session.execute(
                            select(task_dependency_model).where(
                                task_dependency_model.task_id == dep_id
                            )
                        )
                        dep_tasks = result.scalars().all()

                        for dep_task in dep_tasks:
                            if await self._check_circular_dependency(
                                session, task_id, dep_task.depends_on_task_id
                            ):
                                team_logger.error(
                                    f"Circular dependency detected: {task_id} -> {dep_id} -> "
                                    f"... -> {dep_task.depends_on_task_id}"
                                )
                                await session.rollback()
                                return False

                # Also check dependents for potential cycles
                if dependent_task_ids and dependencies:
                    for dependent_id in dependent_task_ids:
                        for new_dep in dependencies:
                            if await self._check_circular_dependency(
                                session, dependent_id, new_dep
                            ):
                                team_logger.error(
                                    f"Circular dependency detected via dependent {dependent_id}"
                                )
                                await session.rollback()
                                return False

                # 2. Create new task
                new_task = team_task_model(
                    task_id=task_id,
                    team_name=team_name,
                    title=title,
                    content=content,
                    status=status
                )
                session.add(new_task)
                await session.flush()

                # 3. Add dependencies: new_task depends on existing tasks
                if dependencies:
                    for dep_id in dependencies:
                        dependency = task_dependency_model(
                            task_id=task_id,
                            depends_on_task_id=dep_id,
                            team_name=team_name
                        )
                        session.add(dependency)

                # 4. Add dependents: existing tasks now depend on new_task
                if dependent_task_ids:
                    for dependent_id in dependent_task_ids:
                        # Verify dependent task exists and check its status
                        result = await session.execute(
                            select(team_task_model).where(team_task_model.task_id == dependent_id)
                        )
                        dep_task = result.scalar_one_or_none()

                        if not dep_task:
                            team_logger.error(f"Dependent task {dependent_id} not found")
                            await session.rollback()
                            return False

                        # Check if dependent task is in terminal status (cannot add new dependency)
                        if dep_task.status in [TaskStatus.COMPLETED.value, TaskStatus.CANCELLED.value,
                                               TaskStatus.CLAIMED.value, TaskStatus.PLAN_APPROVED.value]:
                            team_logger.error(
                                f"Cannot add dependency to {dependent_id} in terminal or executing "
                                f"status: {dep_task.status}"
                            )
                            await session.rollback()
                            return False

                        # Create dependency relationship
                        dependency = task_dependency_model(
                            task_id=dependent_id,
                            depends_on_task_id=task_id,
                            team_name=team_name
                        )
                        session.add(dependency)

                        # Update dependent task status from pending to blocked
                        if dep_task.status == TaskStatus.PENDING.value:
                            dep_task.status = TaskStatus.BLOCKED.value

                # 5. Commit transaction - atomic and acquires write lock
                await session.commit()

                deps_info = f" depends on {dependencies}" if dependencies else ""
                dependents_info = f", {len(dependent_task_ids)} dependents" if dependent_task_ids else ""
                team_logger.info(f"Task {task_id} created{deps_info}{dependents_info}")

                return True

            except IntegrityError as e:
                await session.rollback()
                team_logger.error(f"Failed to create task {task_id}: {e}")
                return False
            except Exception as e:
                await session.rollback()
                team_logger.error(f"Unexpected error creating task {task_id}: {e}")
                return False

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
                    task_dependency_model.task_id == task_id,
                    task_dependency_model.resolved.is_(False)
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
                select(task_dependency_model).where(
                    task_dependency_model.depends_on_task_id == depends_on_task_id
                )
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
            result = await session.execute(
                select(team_task_model).where(team_task_model.task_id == task_id)
            )
            task = result.scalar_one_or_none()
            if not task:
                team_logger.debug(f"Task {task_id} not found for deletion")
                return False

            await session.delete(task)
            await session.commit()
            team_logger.info(f"Task {task_id} deleted")
            return True

    async def cancel_task(self, task_id: str) -> Optional[TeamTaskBase]:
        """Cancel a task atomically and return the updated task

        This method performs cancellation in a single transaction to prevent
        race conditions where a task could be claimed between checking
        assignee and cancelling.

        Args:
            task_id: Task identifier

        Returns:
            Task model if cancellation succeeded, None otherwise
            (task not found or invalid state transition)
        """
        await self._ensure_initialized()
        team_task_model = _get_task_model()
        async with self.session_local() as session:
            # Get task with write lock
            result = await session.execute(
                select(team_task_model).where(team_task_model.task_id == task_id)
            )
            task = result.scalar_one_or_none()
            if not task:
                team_logger.error(f"Task {task_id} not found")
                return None

            # Validate state transition
            if not is_valid_transition(
                TaskStatus(task.status),
                TaskStatus.CANCELLED,
                TASK_TRANSITIONS
            ):
                team_logger.error(
                    f"Invalid state transition for task {task_id}: "
                    f"{task.status} -> {TaskStatus.CANCELLED.value}"
                )
                return None

            # Update task status
            task.status = TaskStatus.CANCELLED.value
            await session.commit()
            team_logger.info(f"Task {task_id} cancelled")

            return task

    async def cancel_all_tasks(self, team_name: str) -> List[TeamTaskBase]:
        """Cancel all non-cancelled and non-completed tasks for a team atomically

        This method performs bulk cancellation in a single transaction to prevent
        race conditions. Only tasks that are in pending/claimed/plan_approved/blocked status
        will be cancelled. Tasks already cancelled or completed will be skipped.

        Args:
            team_name: Team identifier

        Returns:
            List of cancelled task models (empty if no tasks to cancel)
        """
        await self._ensure_initialized()
        team_task_model = _get_task_model()
        async with self.session_local() as session:
            # Get all tasks for this team that are NOT cancelled or completed
            skip_statuses = [TaskStatus.CANCELLED.value, TaskStatus.COMPLETED.value]
            result = await session.execute(
                select(team_task_model).where(
                    team_task_model.team_name == team_name,
                    ~team_task_model.status.in_(skip_statuses)
                )
            )
            tasks = result.scalars().all()

            if not tasks:
                team_logger.info(f"No active tasks to cancel for team {team_name}")
                return []

            cancelled_tasks = []

            # Cancel each task (all in same transaction)
            for task in tasks:
                # Validate state transition one more time (defense in depth)
                if not is_valid_transition(
                    TaskStatus(task.status),
                    TaskStatus.CANCELLED,
                    TASK_TRANSITIONS
                ):
                    team_logger.debug(
                        f"Skipping task {task.task_id}: invalid state transition "
                        f"{task.status} -> {TaskStatus.CANCELLED.value}"
                    )
                    continue

                # Update task status
                task.status = TaskStatus.CANCELLED.value
                cancelled_tasks.append(task)

            await session.commit()

            team_logger.info(
                f"Cancelled {len(cancelled_tasks)} tasks for team {team_name}"
            )

            return cancelled_tasks

    async def complete_task(self, task_id: str) -> Optional[Dict]:
        """Complete a task atomically and unblock dependent tasks

        This method performs task completion and dependent task unblocking in a single
        transaction to prevent race conditions:

        1. Validates task state and transitions to COMPLETED atomically
        2. Resolves all dependencies that depend on this task
        3. Unblocks tasks whose all dependencies are now resolved
        4. Uses CAS (Compare-And-Swap) pattern to prevent multiple completions

        Args:
            task_id: Task identifier

        Returns:
            Dictionary with 'task' (TeamTaskBase) and 'unblocked_tasks' (List[TeamTaskBase]) keys,
            or None if task not found or invalid state transition
        """
        await self._ensure_initialized()
        team_task_model = _get_task_model()
        task_dependency_model = _get_task_dependency_model()
        async with self.session_local() as session:
            # Get task with write lock
            result = await session.execute(
                select(team_task_model).where(team_task_model.task_id == task_id)
            )
            task = result.scalar_one_or_none()
            if not task:
                team_logger.error(f"Task {task_id} not found")
                return None

            # Validate state transition - use CAS pattern
            if task.status == TaskStatus.COMPLETED.value:
                # Already completed - no-op (idempotent)
                team_logger.debug(f"Task {task_id} already completed")
                return {"task": task, "unblocked_tasks": []}

            if not is_valid_transition(
                TaskStatus(task.status),
                TaskStatus.COMPLETED,
                TASK_TRANSITIONS
            ):
                team_logger.error(
                    f"Invalid state transition for task {task_id}: "
                    f"{task.status} -> {TaskStatus.COMPLETED.value}"
                )
                return None

            # Complete task and resolve dependencies atomically
            now = self.get_current_time()
            task.status = TaskStatus.COMPLETED.value
            task.completed_at = now
            team_logger.info(f"Task {task_id} completed at {now}")

            # Resolve all dependencies that depend on this task
            dep_update_result = await session.execute(
                update(task_dependency_model).where(
                    task_dependency_model.depends_on_task_id == task_id,
                    task_dependency_model.resolved.is_(False)
                ).values(resolved=True)
            )
            resolved_count = dep_update_result.rowcount or 0
            if resolved_count > 0:
                team_logger.info(f"Resolved {resolved_count} dependencies for task {task_id}")

            # Unblock tasks whose all dependencies are now resolved
            # Get all tasks that depend on the completed task
            dependent_result = await session.execute(
                select(task_dependency_model).where(
                    task_dependency_model.depends_on_task_id == task_id
                )
            )
            dependent_deps = dependent_result.scalars().all()

            unblocked_tasks = []
            for dep in dependent_deps:
                # Get dependent task
                task_result = await session.execute(
                    select(team_task_model).where(team_task_model.task_id == dep.task_id)
                )
                dependent_task = task_result.scalar_one_or_none()
                if not dependent_task:
                    continue

                # Only unblock if currently BLOCKED (CAS pattern)
                if dependent_task.status != TaskStatus.BLOCKED.value:
                    continue

                # Check if all dependencies are resolved
                unresolved_result = await session.execute(
                    select(task_dependency_model).where(
                        task_dependency_model.task_id == dep.task_id,
                        task_dependency_model.resolved.is_(False)
                    )
                )
                unresolved_count = len(unresolved_result.scalars().all())

                # If no unresolved dependencies, unblock atomically
                if unresolved_count == 0:
                    dependent_task.status = TaskStatus.PENDING.value
                    unblocked_tasks.append(dependent_task)
                    team_logger.info(
                        f"Task {dep.task_id} unblocked (from BLOCKED to PENDING)"
                    )

            await session.commit()
            team_logger.info(f"Task {task_id} completion committed, unblocked {len(unblocked_tasks)} tasks")

            # Return completed task with unblocked tasks list
            return {"task": task, "unblocked_tasks": unblocked_tasks}

    async def _verify_and_fix_blocked_tasks(self, team_name: str) -> List[TeamTaskBase]:
        """Internal method to verify and fix blocked tasks whose' dependencies are completed

        This method is used for data consistency checks and recovery scenarios,
        such as after system recovery or manual database intervention.

        This is an internal method (prefixed with _) and should not be used as part of
        normal workflow. The complete() method handles task unblocking automatically.

        Args:
            team_name: Team identifier

        Returns:
            List of task models that were updated from BLOCKED to PENDING
        """
        await self._ensure_initialized()
        team_task_model = _get_task_model()
        task_dependency_model = _get_task_dependency_model()
        async with self.session_local() as session:
            # Get all blocked tasks for the team
            result = await session.execute(
                select(team_task_model).where(
                    team_task_model.team_name == team_name,
                    team_task_model.status == TaskStatus.BLOCKED.value
                )
            )
            blocked_tasks = result.scalars().all()

            updated_tasks = []
            for task in blocked_tasks:
                # Check if all dependencies are resolved
                deps_result = await session.execute(
                    select(task_dependency_model).where(
                        task_dependency_model.task_id == task.task_id,
                        task_dependency_model.resolved.is_(False)
                    )
                )
                unresolved_deps = deps_result.scalars().all()

                # If no unresolved dependencies, update to PENDING
                if len(unresolved_deps) == 0:
                    task.status = TaskStatus.PENDING.value
                    updated_tasks.append(task)
                    team_logger.info(f"Task {task.task_id} fixed from BLOCKED to PENDING")

            await session.commit()
            return updated_tasks

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
            result = await session.execute(
                select(message_model).where(message_model.message_id == message_id)
            )
            return result.scalar_one_or_none()

    async def create_message(
        self,
        message_id: str,
        team_name: str,
        from_member_name: str,
        content: str,
        *,
        to_member_name: Optional[str] = None,
        broadcast: bool = False
    ) -> bool:
        """Create a new team message"""
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
                        broadcast=broadcast
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
                        delay = _DB_RETRY_BASE_DELAY * (2 ** attempt)
                        team_logger.warning(
                            f"Database locked on create_message (attempt {attempt + 1}), retrying in {delay}s")
                        await asyncio.sleep(delay)
                    else:
                        team_logger.error(
                            f"Failed to create message {message_id} after {_DB_RETRY_ATTEMPTS} attempts: {e}")
                        return False
        return False

    async def get_messages(
        self,
        team_name: str,
        to_member_name: str,
        unread_only: bool = False,
        from_member_name: Optional[str] = None
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
                message_model.broadcast.is_(False)
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
        from_member_name: Optional[str] = None
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

            return [
                row for row in rows
                if read_status is None or row.timestamp > read_status.read_at
            ]

    async def get_team_messages(
        self,
        team_name: str,
        broadcast: Optional[bool] = None
    ) -> List[TeamMessageBase]:
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
            result = await session.execute(
                select(message_model).where(message_model.message_id == message_id)
            )
            message = result.scalar_one_or_none()
            if not message:
                team_logger.error(f"Message {message_id} not found")
                return False

            # Verify member exists
            result = await session.execute(
                select(TeamMember).where(TeamMember.member_name == member_name,
                                         TeamMember.team_name == message.team_name)
            )
            member = result.scalar_one_or_none()
            if not member:
                team_logger.error(f"Member {member_name} not found")
                return False

            if message.broadcast:
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
                else:
                    read_status.read_at = message.timestamp
            else:
                message.is_read = True

            await session.commit()

            team_logger.info(f"Message {message_id} marked as read by {member_name}")
            return True
