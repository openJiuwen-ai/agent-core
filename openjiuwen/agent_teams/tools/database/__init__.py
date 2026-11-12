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

from sqlalchemy import func, select
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
    retry_on_locked as _retry_on_locked,
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
from openjiuwen.agent_teams.tools.models import (
    _get_message_model,
    _get_task_model,
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
        # One DbSessions (one process-wide write lock) shared by every DAO;
        # bind-time session-table DDL also runs through it so DDL serialises
        # with DAO writes instead of bypassing the lock (engine.begin()).
        self._sessions: Optional[DbSessions] = None
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

            # One DbSessions (one write lock) shared by every DAO: SQLite's
            # write lock is database-wide, so all four tables must serialise
            # writes through the same lock. Reads go to the reader factory
            # (a separate pool for file-backed SQLite) — see DbSessions.
            sessions = DbSessions(self.session_local, self.read_session_local)
            self._sessions = sessions
            # Bind-time session-table DDL runs under the same write lock: the
            # engine-level variant (``engine.begin()``) bypassed it and could
            # hold both write-pool connections during concurrent binds.
            if get_session_id():
                await _create_cur_session_tables(sessions)

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
        """Create dynamic tables for current session.

        Runs under the ``DbSessions`` write lock (serialised with DAO writes
        of this instance) rather than on a bare ``engine.begin()`` — see
        ``engine.create_cur_session_tables``.
        """
        if self._sessions is None:
            return
        await _create_cur_session_tables(self._sessions)

    async def drop_cur_session_tables(self) -> None:
        """Drop dynamic tables for current session."""
        if self.engine is None:
            return
        await _drop_cur_session_tables(self.engine)
        # 表已删 → 本库实例上的 bind-DDL 缓存同步失效（否则重建被跳过）
        if self._sessions is not None:
            self._sessions.forget_session_tables_ready(get_session_id())

    async def cleanup_all_runtime_state(self) -> tuple[list[str], list[str]]:
        """Delete all dynamic team tables and clear static team tables."""
        await self._ensure_initialized()
        if self.engine is None:
            return [], []
        result = await _cleanup_all_runtime_state(self.engine)
        # 全量删除 → 本库实例的整个 bind-DDL 缓存作废
        if self._sessions is not None:
            self._sessions.clear_session_tables_ready()
        return result

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
        result = await _drop_session_tables_by_id(self.engine, session_id)
        if self._sessions is not None:
            self._sessions.forget_session_tables_ready(session_id)
        return result

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
            if self._sessions is not None:
                self._sessions.forget_session_tables_ready(session_id)
        except Exception as e:
            team_logger.error(
                "Failed to drop current session tables for team %s: %s",
                team_name,
                e,
            )
            return False

        team_logger.info("Force deleted team session data for %s", team_name)
        return cleanup_success

    async def fetch_stale_sweep_probe(
            self,
            team_name: str,
            *,
            task_status: str,
            exclude_assignee: str | None = None,
    ) -> tuple[list[TeamTaskBase], dict[str, tuple[int, int]]] | None:
        """沉默巡检探针：单连接取 IN_PROGRESS 任务行 + 消息见证水位。

        跨表读收在 TeamDatabase（引擎属主）而非散到调用方：任务表与消息表
        两条 SELECT 放进同一个 ``DbSessions.read()`` 块，一次 checkout 覆盖
        原来 ``list_tasks`` + ``latest_timestamps_by_sender`` 的两次，读连接持有期减半。
        ``since_timestamp`` 下界在块内由取回的任务行现算（最老 watched 任务
        的 ``updated_at``），与 ``MessageDao.get_latest_timestamps_by_sender``
        的下界语义逐字对齐——只是从"调用方两次读"变成"块内先算后查"。

        瞬时失败（``database is locked`` / 读池 checkout 超时）经
        ``retry_on_locked`` 退避重试；预算耗尽返回 ``None``——调用方
        （stale-inprogress sweep）据此冻结本 tick 的沉默钟。

        Args:
            team_name: 团队标识。
            task_status: 任务状态过滤（调用方固定传 IN_PROGRESS）。
            exclude_assignee: 从结果剔除该 assignee 的任务（leader 自身任务
                不参与催收，与调用方原有过滤一致）。

        Returns:
            ``(任务行列表, {sender: (latest_ms, count)})``；重试耗尽返回 None。
        """
        await self._ensure_initialized()
        team_task_model = _get_task_model()
        message_model = _get_message_model()

        async def _op() -> tuple[list[TeamTaskBase], dict[str, tuple[int, int]]]:
            async with self._sessions.read() as session:
                task_query = select(team_task_model).where(
                    team_task_model.team_name == team_name,
                    team_task_model.status == task_status,
                    team_task_model.assignee.isnot(None),
                )
                if exclude_assignee is not None:
                    task_query = task_query.where(team_task_model.assignee != exclude_assignee)
                tasks = list((await session.execute(task_query)).scalars().all())
                if not tasks:
                    # 空板：无 watched 任务则无见证需求，跳过 GROUP BY 扫描
                    return [], {}
                now_ms = _get_current_time()
                since_ms = min((tk.updated_at or now_ms) for tk in tasks)
                witness_rows = (await session.execute(
                    select(
                        message_model.from_member_name,
                        func.max(message_model.timestamp),
                        func.count(),
                    )
                    .where(
                        message_model.team_name == team_name,
                        message_model.timestamp >= since_ms,
                    )
                    .group_by(message_model.from_member_name)
                )).all()
            witness = {
                sender: (latest, count)
                for sender, latest, count in witness_rows
                if sender and latest is not None
            }
            return tasks, witness

        return await _retry_on_locked(
            _op, on_locked_result=None, label=f"fetch_stale_sweep_probe {team_name}"
        )

    async def get_team_activity_probe(self, team_name: str) -> tuple[int, int] | None:
        """团队活动探针：单连接取 ``team_info.updated_at`` 与成员最大 ``updated_at``。

        team-context 提示词段缓存的两个 mtime 探针（每成员每轮各一次）合并
        进同一个 ``DbSessions.read()`` 块——原来两次独立 checkout（
        ``TeamDao.get_team_updated_at`` + ``MemberDao.get_members_max_updated_at``）
        变一次，读连接持有期减半。

        瞬时失败经 ``retry_on_locked`` 退避；耗尽返回 ``None``——调用方
        （TeamContextTracker）按 fail-open 跳过本轮刷新、下轮重试，不让
        一次瞬时读失败炸掉成员回合。

        Returns:
            ``(team_updated_at_ms, members_max_updated_at_ms)``；行缺失按 0
            （与两个单查方法的既有语义一致）；重试耗尽返回 None。
        """
        await self._ensure_initialized()

        async def _op() -> tuple[int, int]:
            async with self._sessions.read() as session:
                team_value = (await session.execute(
                    select(Team.updated_at).where(Team.team_name == team_name)
                )).scalar_one_or_none()
                member_value = (await session.execute(
                    select(func.max(TeamMember.updated_at)).where(TeamMember.team_name == team_name)
                )).scalar_one_or_none()
            return (
                int(team_value) if team_value is not None else 0,
                int(member_value) if member_value is not None else 0,
            )

        return await _retry_on_locked(
            _op, on_locked_result=None, label=f"get_team_activity_probe {team_name}"
        )

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
            self._sessions = None
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
