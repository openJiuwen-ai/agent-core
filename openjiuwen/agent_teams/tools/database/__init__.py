# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Team Database Module

Asynchronous database manager with full CRUD for team data.
Model definitions live in models.py.

This package re-exports all public API symbols so that existing
``from openjiuwen.agent_teams.tools.database import ...`` statements
continue to work unchanged after the internal refactor.
"""

import asyncio
from typing import Dict, Iterable, List, Optional

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
    _TASK_DEPENDENCY_REJECT_STATUSES as _TASK_DEPENDENCY_REJECT_STATUSES,
    _TASK_TERMINAL_STATUSES as _TASK_TERMINAL_STATUSES,
    detect_cycle_in_adjacency as detect_cycle_in_adjacency,
)
from openjiuwen.agent_teams.tools.database.member_dao import MemberDao
from openjiuwen.agent_teams.tools.database.message_dao import MessageDao
from openjiuwen.agent_teams.tools.database.task_dao import TaskDao
from openjiuwen.agent_teams.tools.database.team_dao import TeamDao
from openjiuwen.agent_teams.tools.models import (
    Team as Team,
    TeamMember as TeamMember,
    TeamMessageBase as TeamMessageBase,
    TeamTaskBase as TeamTaskBase,
    TeamTaskDependencyBase as TeamTaskDependencyBase,
    _get_task_dependency_model as _get_task_dependency_model,
)
from openjiuwen.core.common.logging import team_logger


class TeamDatabase:
    """Asynchronous team database manager with full CRUD.

    Facade class that delegates to specialized DAO instances while
    preserving the original public interface.
    """

    def __init__(self, config: DatabaseConfig):
        """Initialize database manager."""
        self.config = config
        self.engine: Optional[AsyncEngine] = None
        self._initialized = False
        self.session_local: Optional[async_sessionmaker] = None
        self._init_lock: Optional[asyncio.Lock] = None
        self._team_dao: Optional[TeamDao] = None
        self._member_dao: Optional[MemberDao] = None
        self._task_dao: Optional[TaskDao] = None
        self._message_dao: Optional[MessageDao] = None

    @staticmethod
    def get_current_time() -> int:
        """Return current time in milliseconds."""
        return _get_current_time()

    async def initialize(self) -> None:
        """Initialize async engine and create tables."""
        if self._initialized:
            return

        if self._init_lock is None:
            self._init_lock = asyncio.Lock()

        async with self._init_lock:
            if self._initialized:
                return
            await self._initialize_locked()

    async def _initialize_locked(self) -> None:
        """Actual initialization body; must run under ``_init_lock``."""
        self.engine, self.session_local = await _initialize_engine(self.config)

        await _create_cur_session_tables(self.engine)

        self._team_dao = TeamDao(self.session_local, self.engine)
        self._member_dao = MemberDao(self.session_local)
        self._task_dao = TaskDao(self.session_local)
        self._message_dao = MessageDao(self.session_local)

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

    async def close(self) -> None:
        """Close the database engine and release all connections."""
        if self.engine:
            await self.engine.dispose()
            self.engine = None
            self.session_local = None
            self._initialized = False
            self._team_dao = None
            self._member_dao = None
            self._task_dao = None
            self._message_dao = None
            team_logger.info("Team database closed")

    async def _ensure_initialized(self):
        """Ensure database is initialized (sync wrapper)."""
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
        """Create a new team."""
        await self._ensure_initialized()
        return await self._team_dao.create_team(team_name, display_name, leader_member_name, desc, prompt)

    async def get_team(self, team_name: str) -> Optional[Team]:
        """Get team information by ID."""
        await self._ensure_initialized()
        return await self._team_dao.get_team(team_name)

    async def delete_team(self, team_name: str) -> bool:
        """Delete a team (cascade delete will remove related records)."""
        await self._ensure_initialized()
        return await self._team_dao.delete_team(team_name)

    async def force_delete_team_session(self, team_name: str) -> bool:
        """Force delete a team's records and current session tables."""
        await self._ensure_initialized()
        return await self._team_dao.force_delete_team_session(team_name)

    async def get_team_updated_at(self, team_name: str) -> int:
        """Probe ``team_info.updated_at`` for change detection."""
        await self._ensure_initialized()
        return await self._team_dao.get_team_updated_at(team_name)

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
        mode: str = "build_mode",
        prompt: Optional[str] = None,
        model_ref_json: Optional[str] = None,
    ) -> bool:
        """Create a new team member."""
        await self._ensure_initialized()
        return await self._member_dao.create_member(
            member_name,
            team_name,
            display_name,
            agent_card,
            status,
            desc=desc,
            execution_status=execution_status,
            mode=mode,
            prompt=prompt,
            model_ref_json=model_ref_json,
        )

    async def get_member(self, member_name: str, team_name: str) -> Optional[TeamMember]:
        """Get member information by ID."""
        await self._ensure_initialized()
        return await self._member_dao.get_member(member_name, team_name)

    async def get_team_members(self, team_name: str, status: str | None = None) -> List[TeamMember]:
        """Get members for a team, optionally filtered by status."""
        await self._ensure_initialized()
        return await self._member_dao.get_team_members(team_name, status)

    async def get_members_max_updated_at(self, team_name: str) -> int:
        """Probe MAX(``team_member.updated_at``) for the team."""
        await self._ensure_initialized()
        return await self._member_dao.get_members_max_updated_at(team_name)

    async def update_member_status(
        self,
        member_name: str,
        team_name: str,
        status: str,
    ) -> bool:
        """Update member status."""
        await self._ensure_initialized()
        return await self._member_dao.update_member_status(member_name, team_name, status)

    async def update_member_execution_status(
        self,
        member_name: str,
        team_name: str,
        execution_status: str,
    ) -> bool:
        """Update member execution status."""
        await self._ensure_initialized()
        return await self._member_dao.update_member_execution_status(member_name, team_name, execution_status)

    # ----------------- Task Operations -----------------
    async def create_task(
        self,
        task_id: str,
        team_name: str,
        title: str,
        content: str,
        status: str,
    ) -> bool:
        """Create a new team task."""
        await self._ensure_initialized()
        return await self._task_dao.create_task(task_id, team_name, title, content, status)

    async def get_task(self, task_id: str) -> Optional[TeamTaskBase]:
        """Get task information by ID."""
        await self._ensure_initialized()
        return await self._task_dao.get_task(task_id)

    async def get_team_tasks(self, team_name: str, status: Optional[str] = None) -> List[TeamTaskBase]:
        """Get all tasks for a team, optionally filtered by status."""
        await self._ensure_initialized()
        return await self._task_dao.get_team_tasks(team_name, status)

    async def get_tasks_by_assignee(
        self, team_name: str, assignee_id: str, status: Optional[str] = None
    ) -> List[TeamTaskBase]:
        """Get tasks assigned to a specific member, optionally filtered by status."""
        await self._ensure_initialized()
        return await self._task_dao.get_tasks_by_assignee(team_name, assignee_id, status)

    async def assign_task(self, task_id: str, member_name: str) -> bool:
        """Assign a task to a member and mark it as claimed."""
        await self._ensure_initialized()
        return await self._task_dao.assign_task(task_id, member_name)

    async def claim_task(self, task_id: str, member_name: str) -> bool:
        """Claim a task for a member."""
        await self._ensure_initialized()
        return await self._task_dao.claim_task(task_id, member_name)

    async def reset_task(self, task_id: str) -> Optional[TeamTaskBase]:
        """Reset a claimed or plan_approved task back to pending status and clear assignee."""
        await self._ensure_initialized()
        return await self._task_dao.reset_task(task_id)

    async def approve_plan_task(self, task_id: str) -> Optional[TeamTaskBase]:
        """Approve a task plan for PLAN_MODE members."""
        await self._ensure_initialized()
        return await self._task_dao.approve_plan_task(task_id)

    async def update_task_status(self, task_id: str, status: str) -> bool:
        """Update task status."""
        await self._ensure_initialized()
        return await self._task_dao.update_task_status(task_id, status)

    async def update_task(
        self,
        task_id: str,
        title: Optional[str] = None,
        content: Optional[str] = None,
    ) -> bool:
        """Update task content (title, content, etc.)."""
        await self._ensure_initialized()
        return await self._task_dao.update_task(task_id, title=title, content=content)

    async def _refresh_status_in_session(
        self,
        session,
        task_ids: Iterable[str],
        now: int,
    ) -> List[TeamTaskBase]:
        """Recompute PENDING/BLOCKED status for tasks based on unresolved deps."""
        return await self._task_dao._refresh_status_in_session(session, task_ids, now)

    async def _terminate_task_in_session(
        self,
        session,
        task_id: str,
        new_status,
        now: int,
    ) -> Optional[tuple]:
        """Terminate a task and propagate dependency resolution downstream."""
        return await self._task_dao._terminate_task_in_session(session, task_id, new_status, now)

    async def mutate_dependency_graph(
        self,
        team_name: str,
        *,
        new_tasks: Optional[List] = None,
        add_edges: Optional[List[tuple[str, str]]] = None,
    ) -> object:
        """Atomic dependency-graph mutation: insert nodes and/or edges."""
        await self._ensure_initialized()
        return await self._task_dao.mutate_dependency_graph(team_name, new_tasks=new_tasks, add_edges=add_edges)

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
        """Create a task and wire it into the dependency chain atomically."""
        await self._ensure_initialized()
        return await self._task_dao.add_task_with_bidirectional_dependencies(
            task_id,
            team_name,
            title,
            content,
            status,
            dependencies=dependencies,
            dependent_task_ids=dependent_task_ids,
        )

    async def get_task_dependencies(self, task_id: str) -> List[TeamTaskDependencyBase]:
        """Get all dependencies for a task."""
        await self._ensure_initialized()
        return await self._task_dao.get_task_dependencies(task_id)

    async def get_unresolved_dependencies_count(self, task_id: str) -> int:
        """Get count of unresolved dependencies for a task."""
        await self._ensure_initialized()
        return await self._task_dao.get_unresolved_dependencies_count(task_id)

    async def get_tasks_depending_on(self, depends_on_task_id: str) -> List[TeamTaskBase]:
        """Get all tasks that depend on a specific task."""
        await self._ensure_initialized()
        return await self._task_dao.get_tasks_depending_on(depends_on_task_id)

    async def delete_task(self, task_id: str) -> bool:
        """Delete a task."""
        await self._ensure_initialized()
        return await self._task_dao.delete_task(task_id)

    async def cancel_task(self, task_id: str) -> Optional[Dict]:
        """Cancel a task atomically and unblock dependent tasks."""
        await self._ensure_initialized()
        return await self._task_dao.cancel_task(task_id)

    async def cancel_all_tasks(
        self,
        team_name: str,
        skip_assignees: Optional[set[str]] = None,
    ) -> Dict:
        """Cancel every active task for a team atomically."""
        await self._ensure_initialized()
        return await self._task_dao.cancel_all_tasks(team_name, skip_assignees=skip_assignees)

    async def complete_task(self, task_id: str) -> Optional[Dict]:
        """Complete a task atomically and unblock dependent tasks."""
        await self._ensure_initialized()
        return await self._task_dao.complete_task(task_id)

    async def _verify_and_fix_blocked_tasks(self, team_name: str) -> List[TeamTaskBase]:
        """Recovery sweep: re-evaluate every BLOCKED task in the team."""
        await self._ensure_initialized()
        return await self._task_dao._verify_and_fix_blocked_tasks(team_name)

    async def verify_and_fix_task_consistency(self, team_name: str) -> List[TeamTaskBase]:
        """Verify and fix task consistency for a team."""
        await self._ensure_initialized()
        return await self._task_dao.verify_and_fix_task_consistency(team_name)

    # ----------------- Message Operations -----------------
    async def get_message(self, message_id: str) -> Optional[TeamMessageBase]:
        """Get message information by ID."""
        await self._ensure_initialized()
        return await self._message_dao.get_message(message_id)

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
        """Create a new team message."""
        await self._ensure_initialized()
        return await self._message_dao.create_message(
            message_id,
            team_name,
            from_member_name,
            content,
            to_member_name=to_member_name,
            broadcast=broadcast,
            is_read=is_read,
        )

    async def get_messages(
        self,
        team_name: str,
        to_member_name: str,
        unread_only: bool = False,
        from_member_name: Optional[str] = None,
    ) -> List[TeamMessageBase]:
        """Get direct (point-to-point) messages for a specific member."""
        await self._ensure_initialized()
        return await self._message_dao.get_messages(team_name, to_member_name, unread_only, from_member_name)

    async def get_broadcast_messages(
        self,
        team_name: str,
        member_name: str,
        unread_only: bool = False,
        from_member_name: Optional[str] = None,
    ) -> List[TeamMessageBase]:
        """Get broadcast messages for a specific member, with read status."""
        await self._ensure_initialized()
        return await self._message_dao.get_broadcast_messages(team_name, member_name, unread_only, from_member_name)

    async def get_team_messages(self, team_name: str, broadcast: Optional[bool] = None) -> List[TeamMessageBase]:
        """Get all messages for a team (without read status)."""
        await self._ensure_initialized()
        return await self._message_dao.get_team_messages(team_name, broadcast)

    async def mark_message_read(self, message_id: str, member_name: str) -> bool:
        """Mark a message as read by a member (works for both direct and broadcast messages)."""
        await self._ensure_initialized()
        return await self._message_dao.mark_message_read(message_id, member_name)
