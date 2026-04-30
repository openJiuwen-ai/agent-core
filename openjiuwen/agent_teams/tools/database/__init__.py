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

from openjiuwen.agent_teams.tools.database.config import (
    DatabaseConfig as DatabaseConfig,
    DatabaseType as DatabaseType,
)
from openjiuwen.agent_teams.tools.database.engine import (
    cleanup_all_runtime_state as _cleanup_all_runtime_state,
    create_cur_session_tables as _create_cur_session_tables,
    drop_cur_session_tables as _drop_cur_session_tables,
    get_current_time as _get_current_time,
    initialize_engine as _initialize_engine,
)
from openjiuwen.agent_teams.tools.database.graph import (
    TASK_DEPENDENCY_REJECT_STATUSES as TASK_DEPENDENCY_REJECT_STATUSES,
    TASK_TERMINAL_STATUSES as TASK_TERMINAL_STATUSES,
    detect_cycle_in_adjacency as detect_cycle_in_adjacency,
)
from openjiuwen.agent_teams.tools.database.member_dao import MemberDao as MemberDao
from openjiuwen.agent_teams.tools.database.message_dao import MessageDao as MessageDao
from openjiuwen.agent_teams.tools.database.task_dao import TaskDao as TaskDao
from openjiuwen.agent_teams.tools.database.team_dao import TeamDao as TeamDao
from openjiuwen.agent_teams.tools.models import (
    Team as Team,
    TeamMember as TeamMember,
    TeamMessageBase as TeamMessageBase,
    TeamTaskBase as TeamTaskBase,
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
        self.engine: Optional[AsyncEngine] = None
        self.session_local: Optional[async_sessionmaker] = None
        self._initialized = False
        self._init_lock = asyncio.Lock()
        self.team: Optional[TeamDao] = None
        self.member: Optional[MemberDao] = None
        self.task: Optional[TaskDao] = None
        self.message: Optional[MessageDao] = None

    @staticmethod
    def get_current_time() -> int:
        """Return current time in milliseconds."""
        return _get_current_time()

    async def initialize(self) -> None:
        """Initialize async engine, create tables, and wire up DAOs."""
        if self._initialized:
            return
        async with self._init_lock:
            if self._initialized:
                return
            self.engine, self.session_local = await _initialize_engine(self.config)
            await _create_cur_session_tables(self.engine)

            self.team = TeamDao(self.session_local)
            self.member = MemberDao(self.session_local)
            self.task = TaskDao(self.session_local)
            self.message = MessageDao(self.session_local)

            self._initialized = True
            team_logger.info("Team database initialized")

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
        return True

    async def close(self) -> None:
        """Close the database engine and release all connections."""
        if self.engine:
            await self.engine.dispose()
            self.engine = None
            self.session_local = None
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
