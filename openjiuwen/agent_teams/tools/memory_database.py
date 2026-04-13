# coding: utf-8
"""In-memory team database that replaces SQLite for single-process mode.

Implements the same public async interface as TeamDatabase so that
TeamBackend, TeamTaskManager, TeamMessageManager and TeamMember can
use it transparently via duck-typing.
"""

from __future__ import annotations

import asyncio
import time
from typing import (
    Any,
    Dict,
    List,
    Optional,
)

from pydantic import BaseModel

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
from openjiuwen.agent_teams.tools.models import (
    Team,
    TeamMember,
)
from openjiuwen.core.common.logging import team_logger


# ---------------------------------------------------------------------------
# Config model (registered in StorageSpec registry as "memory")
# ---------------------------------------------------------------------------

class MemoryDatabaseConfig(BaseModel):
    """Minimal config to select the in-memory storage backend."""

    db_type: str = "memory"
    connection_string: str = ""


# ---------------------------------------------------------------------------
# Lightweight data containers for dynamic (per-session) records.
#
# SQLModel base classes (TeamTaskBase, etc.) are abstract and carry
# SQLAlchemy table metadata.  We create plain subclasses stripped of
# table bindings so they can be instantiated as pure data objects.
# ---------------------------------------------------------------------------

class _MemTask:
    """Plain task record."""

    def __init__(
        self,
        *,
        task_id: str,
        team_name: str,
        title: str,
        content: str,
        status: str,
        assignee: Optional[str] = None,
        completed_at: Optional[int] = None,
    ) -> None:
        self.task_id = task_id
        self.team_name = team_name
        self.title = title
        self.content = content
        self.status = status
        self.assignee = assignee
        self.completed_at = completed_at

    def brief(self) -> dict:
        return {"task_id": self.task_id, "title": self.title, "status": self.status}


class _MemTaskDep:
    """Plain task-dependency record."""

    def __init__(
        self,
        *,
        task_id: str,
        depends_on_task_id: str,
        team_name: str,
        resolved: bool = False,
    ) -> None:
        self.task_id = task_id
        self.depends_on_task_id = depends_on_task_id
        self.team_name = team_name
        self.resolved = resolved


class _MemMessage:
    """Plain message record."""

    def __init__(
        self,
        *,
        message_id: str,
        team_name: str,
        from_member_name: str,
        content: str,
        timestamp: int,
        broadcast: bool,
        to_member_name: Optional[str] = None,
        is_read: bool = False,
    ) -> None:
        self.message_id = message_id
        self.team_name = team_name
        self.from_member_name = from_member_name
        self.content = content
        self.timestamp = timestamp
        self.broadcast = broadcast
        self.to_member_name = to_member_name
        self.is_read = is_read


class _MemReadStatus:
    """Plain broadcast-read-status record."""

    def __init__(self, *, member_name: str, team_name: str, read_at: Optional[int] = None) -> None:
        self.member_name = member_name
        self.team_name = team_name
        self.read_at = read_at


# ---------------------------------------------------------------------------
# InMemoryTeamDatabase
# ---------------------------------------------------------------------------

class InMemoryTeamDatabase:
    """Drop-in replacement for TeamDatabase backed by plain dicts/lists.

    All public methods are ``async`` to match TeamDatabase's interface.
    An ``asyncio.Lock`` serialises write operations so concurrent
    coroutines (leader + teammates in the same event loop) stay safe.
    """

    def __init__(self) -> None:
        self._teams: dict[str, Team] = {}
        self._members: dict[str, TeamMember] = {}
        # Per-session dynamic data
        self._tasks: dict[str, _MemTask] = {}
        self._task_deps: list[_MemTaskDep] = []
        self._messages: list[_MemMessage] = []
        self._read_status: dict[tuple[str, str], _MemReadStatus] = {}
        self._lock = asyncio.Lock()
        self._initialized = True

    # ---- helpers ---------------------------------------------------------

    @staticmethod
    def get_current_time() -> int:
        return int(round(time.time() * 1000))

    async def initialize(self) -> None:
        self._initialized = True

    async def create_cur_session_tables(self) -> None:
        """No-op — in-memory collections need no schema creation."""

    async def drop_cur_session_tables(self) -> None:
        """Clear per-session data."""
        async with self._lock:
            self._tasks.clear()
            self._task_deps.clear()
            self._messages.clear()
            self._read_status.clear()

    async def close(self) -> None:
        self._initialized = False

    async def _ensure_initialized(self) -> None:
        pass

    # =====================================================================
    # Team Operations
    # =====================================================================

    async def create_team(
        self,
        team_name: str,
        display_name: str,
        leader_member_name: str,
        desc: Optional[str] = None,
        prompt: Optional[str] = None,
    ) -> bool:
        async with self._lock:
            if team_name in self._teams:
                team_logger.error(f"Team {team_name} already exists")
                return False
            self._teams[team_name] = Team(
                team_name=team_name,
                display_name=display_name,
                leader_member_name=leader_member_name,
                desc=desc,
                prompt=prompt,
                created=self.get_current_time(),
            )
            team_logger.info(f"Team {team_name} created")
            return True

    async def get_team(self, team_name: str) -> Optional[Team]:
        return self._teams.get(team_name)

    async def delete_team(self, team_name: str) -> bool:
        async with self._lock:
            if team_name not in self._teams:
                return True
            del self._teams[team_name]
            # Cascade
            self._members = {k: v for k, v in self._members.items() if v.team_name != team_name}
            self._tasks = {k: v for k, v in self._tasks.items() if v.team_name != team_name}
            self._task_deps = [d for d in self._task_deps if d.team_name != team_name]
            self._messages = [m for m in self._messages if m.team_name != team_name]
            self._read_status = {
                k: v for k, v in self._read_status.items() if v.team_name != team_name
            }
            team_logger.info(f"Team {team_name} deleted")
            return True

    # =====================================================================
    # Member Operations
    # =====================================================================

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
    ) -> bool:
        async with self._lock:
            if member_name in self._members:
                team_logger.error(f"Member {member_name} already exists")
                return False
            self._members[member_name] = TeamMember(
                member_name=member_name,
                team_name=team_name,
                display_name=display_name,
                agent_card=agent_card,
                status=status,
                desc=desc,
                execution_status=execution_status,
                mode=mode,
                prompt=prompt,
            )
            team_logger.info(f"Member {member_name} created")
            return True

    async def get_member(self, member_name: str, team_name: str) -> Optional[TeamMember]:
        member = self._members.get(member_name)
        if member and member.team_name == team_name:
            return member
        return None

    async def get_team_members(self, team_name: str, status: Optional[str] = None) -> List[TeamMember]:
        members = [m for m in self._members.values() if m.team_name == team_name]
        if status is not None:
            members = [m for m in members if m.status == status]
        return members

    async def update_member_status(self, member_name: str, team_name: str, status: str) -> bool:
        async with self._lock:
            member = self._members.get(member_name)
            if not member or member.team_name != team_name:
                team_logger.error(f"Member {member_name} not found in team {team_name}")
                return False
            if not is_valid_transition(MemberStatus(member.status), MemberStatus(status), MEMBER_TRANSITIONS):
                team_logger.error(f"Invalid state transition for member {member_name}: {member.status} -> {status}")
                return False
            member.status = status
            team_logger.info(f"Member {member_name} status updated to {status}")
            return True

    async def update_member_execution_status(self, member_name: str, team_name: str, execution_status: str) -> bool:
        async with self._lock:
            member = self._members.get(member_name)
            if not member or member.team_name != team_name:
                team_logger.error(f"Member {member_name} not found in team {team_name}")
                return False
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
            team_logger.info(f"Member {member_name} execution status updated to {execution_status}")
            return True

    # =====================================================================
    # Task Operations
    # =====================================================================

    async def create_task(
        self, task_id: str, team_name: str, title: str, content: str, status: str
    ) -> bool:
        async with self._lock:
            if task_id in self._tasks:
                team_logger.error(f"Task {task_id} already exists")
                return False
            self._tasks[task_id] = _MemTask(
                task_id=task_id, team_name=team_name, title=title, content=content, status=status,
            )
            team_logger.info(f"Task {task_id} created")
            return True

    async def get_task(self, task_id: str) -> Optional[_MemTask]:
        return self._tasks.get(task_id)

    async def get_team_tasks(self, team_name: str, status: Optional[str] = None) -> List[_MemTask]:
        tasks = [t for t in self._tasks.values() if t.team_name == team_name]
        if status:
            tasks = [t for t in tasks if t.status == status]
        return tasks

    async def get_tasks_by_assignee(
        self, team_name: str, assignee_id: str, status: Optional[str] = None
    ) -> List[_MemTask]:
        tasks = [t for t in self._tasks.values() if t.team_name == team_name and t.assignee == assignee_id]
        if status:
            tasks = [t for t in tasks if t.status == status]
        return tasks

    async def claim_task(self, task_id: str, member_name: str) -> bool:
        async with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                team_logger.error(f"Task {task_id} not found")
                return False
            if not is_valid_transition(TaskStatus(task.status), TaskStatus.CLAIMED, TASK_TRANSITIONS):
                team_logger.error(
                    f"Invalid state transition for task {task_id}: {task.status} -> {TaskStatus.CLAIMED.value}")
                return False
            if task.assignee:
                team_logger.warning(f"Task {task_id} is already claimed by member {task.assignee}")
                return False
            task.status = TaskStatus.CLAIMED.value
            task.assignee = member_name
            team_logger.info(f"Task {task_id} claimed by member {member_name}")
            return True

    async def reset_task(self, task_id: str) -> Optional[_MemTask]:
        async with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                team_logger.error(f"Task {task_id} not found")
                return None
            if task.status != TaskStatus.CLAIMED.value:
                team_logger.error(
                    f"Cannot reset task {task_id} with status {task.status}, only CLAIMED tasks can be reset"
                )
                return None
            if not is_valid_transition(TaskStatus(task.status), TaskStatus.PENDING, TASK_TRANSITIONS):
                team_logger.error(
                    f"Invalid state transition for task {task_id}: {task.status} -> {TaskStatus.PENDING.value}")
                return None
            task.status = TaskStatus.PENDING.value
            task.assignee = None
            team_logger.info(f"Task {task_id} reset to PENDING")
            return task

    async def approve_plan_task(self, task_id: str) -> Optional[_MemTask]:
        async with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                team_logger.error(f"Task {task_id} not found")
                return None
            if not is_valid_transition(TaskStatus(task.status), TaskStatus.PLAN_APPROVED, TASK_TRANSITIONS):
                team_logger.error(
                    f"Invalid state transition for task {task_id}: {task.status} -> {TaskStatus.PLAN_APPROVED.value}"
                )
                return None
            task.status = TaskStatus.PLAN_APPROVED.value
            team_logger.info(f"Task {task_id} approved from CLAIMED to PLAN_APPROVED")
            return task

    async def update_task_status(self, task_id: str, status: str) -> bool:
        async with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                team_logger.error(f"Task {task_id} not found")
                return False
            if not is_valid_transition(TaskStatus(task.status), TaskStatus(status), TASK_TRANSITIONS):
                team_logger.error(f"Invalid state transition for task {task_id}: {task.status} -> {status}")
                return False
            task.status = status
            if status == TaskStatus.COMPLETED.value:
                now = self.get_current_time()
                task.completed_at = now
                for dep in self._task_deps:
                    if dep.depends_on_task_id == task_id and not dep.resolved:
                        dep.resolved = True
            team_logger.info(f"Task {task_id} status updated to {status}")
            return True

    async def update_task(self, task_id: str, title: Optional[str] = None, content: Optional[str] = None) -> bool:
        async with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                team_logger.error(f"Task {task_id} not found")
                return False
            if task.status in (TaskStatus.CLAIMED.value, TaskStatus.PLAN_APPROVED.value):
                team_logger.error(f"Cannot update task {task_id} because it is currently {task.status}")
                return False
            if title is not None:
                task.title = title
            if content is not None:
                task.content = content
            return True

    async def add_task_dependency(self, task_id: str, depends_on_task_id: str, team_name: str) -> bool:
        async with self._lock:
            # Idempotent — skip if already exists
            for d in self._task_deps:
                if d.task_id == task_id and d.depends_on_task_id == depends_on_task_id:
                    return True
            self._task_deps.append(
                _MemTaskDep(task_id=task_id, depends_on_task_id=depends_on_task_id, team_name=team_name)
            )
            team_logger.info(f"Task dependency added: {task_id} -> {depends_on_task_id}")
            return True

    def _check_circular_dependency_sync(
        self, task_id: str, target_task_id: str, visited: Optional[set] = None
    ) -> bool:
        """Synchronous DFS cycle check (called under lock)."""
        if visited is None:
            visited = set()
        if target_task_id == task_id:
            return True
        if target_task_id in visited:
            return False
        visited.add(target_task_id)
        for dep in self._task_deps:
            if dep.task_id == target_task_id:
                if self._check_circular_dependency_sync(task_id, dep.depends_on_task_id, visited):
                    return True
        return False

    async def _check_circular_dependency(
        self, session: Any, task_id: str, target_task_id: str, visited: Optional[set] = None
    ) -> bool:
        """API-compatible wrapper; *session* is ignored for in-memory."""
        return self._check_circular_dependency_sync(task_id, target_task_id, visited)

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
        async with self._lock:
            # 1. Circular dependency check
            if dependencies:
                for dep_id in dependencies:
                    for dep in self._task_deps:
                        if dep.task_id == dep_id:
                            if self._check_circular_dependency_sync(task_id, dep.depends_on_task_id):
                                team_logger.error(f"Circular dependency detected: {task_id} -> {dep_id}")
                                return False

            if dependent_task_ids and dependencies:
                for dependent_id in dependent_task_ids:
                    for new_dep in dependencies:
                        if self._check_circular_dependency_sync(dependent_id, new_dep):
                            team_logger.error(f"Circular dependency detected via dependent {dependent_id}")
                            return False

            # 2. Create task
            if task_id in self._tasks:
                team_logger.error(f"Task {task_id} already exists")
                return False
            self._tasks[task_id] = _MemTask(
                task_id=task_id, team_name=team_name, title=title, content=content, status=status,
            )

            # 3. New task depends on existing tasks
            if dependencies:
                for dep_id in dependencies:
                    self._task_deps.append(
                        _MemTaskDep(task_id=task_id, depends_on_task_id=dep_id, team_name=team_name)
                    )

            # 4. Existing tasks depend on new task
            if dependent_task_ids:
                for dependent_id in dependent_task_ids:
                    dep_task = self._tasks.get(dependent_id)
                    if not dep_task:
                        team_logger.error(f"Dependent task {dependent_id} not found")
                        # Rollback: remove created task and deps
                        del self._tasks[task_id]
                        self._task_deps = [
                            d for d in self._task_deps
                            if not (d.depends_on_task_id == task_id and d.task_id in (dependent_task_ids or []))
                        ]
                        return False
                    if dep_task.status in (
                            TaskStatus.COMPLETED.value,
                            TaskStatus.CANCELLED.value,
                            TaskStatus.CLAIMED.value,
                            TaskStatus.PLAN_APPROVED.value,
                    ):
                        team_logger.error(
                            f"Cannot add dependency to {dependent_id} in terminal "
                            f"or executing status: {dep_task.status}"
                        )
                        del self._tasks[task_id]
                        return False
                    self._task_deps.append(
                        _MemTaskDep(task_id=dependent_id, depends_on_task_id=task_id, team_name=team_name)
                    )
                    if dep_task.status == TaskStatus.PENDING.value:
                        dep_task.status = TaskStatus.BLOCKED.value

            team_logger.info(f"Task {task_id} created with bidirectional dependencies")
            return True

    async def get_task_dependencies(self, task_id: str) -> List[_MemTaskDep]:
        return [d for d in self._task_deps if d.task_id == task_id]

    async def get_unresolved_dependencies_count(self, task_id: str) -> int:
        return sum(1 for d in self._task_deps if d.task_id == task_id and not d.resolved)

    async def get_tasks_depending_on(self, depends_on_task_id: str) -> List[_MemTask]:
        dep_task_ids = [d.task_id for d in self._task_deps if d.depends_on_task_id == depends_on_task_id]
        return [self._tasks[tid] for tid in dep_task_ids if tid in self._tasks]

    async def delete_task(self, task_id: str) -> bool:
        async with self._lock:
            if task_id not in self._tasks:
                team_logger.debug(f"Task {task_id} not found for deletion")
                return False
            del self._tasks[task_id]
            self._task_deps = [d for d in self._task_deps if d.task_id != task_id and d.depends_on_task_id != task_id]
            team_logger.info(f"Task {task_id} deleted")
            return True

    async def cancel_task(self, task_id: str) -> Optional[_MemTask]:
        async with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                team_logger.error(f"Task {task_id} not found")
                return None
            if not is_valid_transition(TaskStatus(task.status), TaskStatus.CANCELLED, TASK_TRANSITIONS):
                team_logger.error(
                    f"Invalid state transition for task {task_id}: {task.status} -> {TaskStatus.CANCELLED.value}")
                return None
            task.status = TaskStatus.CANCELLED.value
            team_logger.info(f"Task {task_id} cancelled")
            return task

    async def cancel_all_tasks(self, team_name: str) -> List[_MemTask]:
        async with self._lock:
            skip = {TaskStatus.CANCELLED.value, TaskStatus.COMPLETED.value}
            cancelled = []
            for task in self._tasks.values():
                if task.team_name != team_name or task.status in skip:
                    continue
                if not is_valid_transition(TaskStatus(task.status), TaskStatus.CANCELLED, TASK_TRANSITIONS):
                    continue
                task.status = TaskStatus.CANCELLED.value
                cancelled.append(task)
            team_logger.info(f"Cancelled {len(cancelled)} tasks for team {team_name}")
            return cancelled

    async def complete_task(self, task_id: str) -> Optional[Dict]:
        async with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                team_logger.error(f"Task {task_id} not found")
                return None
            if task.status == TaskStatus.COMPLETED.value:
                return {"task": task, "unblocked_tasks": []}
            if not is_valid_transition(TaskStatus(task.status), TaskStatus.COMPLETED, TASK_TRANSITIONS):
                team_logger.error(
                    f"Invalid state transition for task {task_id}: {task.status} -> {TaskStatus.COMPLETED.value}")
                return None

            now = self.get_current_time()
            task.status = TaskStatus.COMPLETED.value
            task.completed_at = now

            # Resolve deps
            for dep in self._task_deps:
                if dep.depends_on_task_id == task_id and not dep.resolved:
                    dep.resolved = True

            # Unblock tasks
            dependent_task_ids = {d.task_id for d in self._task_deps if d.depends_on_task_id == task_id}
            unblocked = []
            for dtid in dependent_task_ids:
                dt = self._tasks.get(dtid)
                if not dt or dt.status != TaskStatus.BLOCKED.value:
                    continue
                unresolved = sum(1 for d in self._task_deps if d.task_id == dtid and not d.resolved)
                if unresolved == 0:
                    dt.status = TaskStatus.PENDING.value
                    unblocked.append(dt)
                    team_logger.info(f"Task {dtid} unblocked (from BLOCKED to PENDING)")

            team_logger.info(f"Task {task_id} completed, unblocked {len(unblocked)} tasks")
            return {"task": task, "unblocked_tasks": unblocked}

    async def _verify_and_fix_blocked_tasks(self, team_name: str) -> List[_MemTask]:
        async with self._lock:
            fixed = []
            for task in self._tasks.values():
                if task.team_name != team_name or task.status != TaskStatus.BLOCKED.value:
                    continue
                unresolved = sum(1 for d in self._task_deps if d.task_id == task.task_id and not d.resolved)
                if unresolved == 0:
                    task.status = TaskStatus.PENDING.value
                    fixed.append(task)
                    team_logger.info(f"Task {task.task_id} fixed from BLOCKED to PENDING")
            return fixed

    async def verify_and_fix_task_consistency(self, team_name: str) -> List[_MemTask]:
        return await self._verify_and_fix_blocked_tasks(team_name)

    # =====================================================================
    # Message Operations
    # =====================================================================

    async def get_message(self, message_id: str) -> Optional[_MemMessage]:
        for m in self._messages:
            if m.message_id == message_id:
                return m
        return None

    async def create_message(
        self,
        message_id: str,
        team_name: str,
        from_member_name: str,
        content: str,
        *,
        to_member_name: Optional[str] = None,
        broadcast: bool = False,
    ) -> bool:
        async with self._lock:
            for m in self._messages:
                if m.message_id == message_id:
                    team_logger.error(f"Message {message_id} already exists")
                    return False
            self._messages.append(
                _MemMessage(
                    message_id=message_id,
                    team_name=team_name,
                    from_member_name=from_member_name,
                    content=content,
                    to_member_name=to_member_name,
                    timestamp=self.get_current_time(),
                    broadcast=broadcast,
                )
            )
            team_logger.info(f"Message {message_id} created")
            return True

    async def get_messages(
        self,
        team_name: str,
        to_member_name: str,
        unread_only: bool = False,
        from_member_name: Optional[str] = None,
    ) -> List[_MemMessage]:
        result = [
            m for m in self._messages
            if m.team_name == team_name and m.to_member_name == to_member_name and not m.broadcast
        ]
        if from_member_name is not None:
            result = [m for m in result if m.from_member_name == from_member_name]
        if unread_only:
            result = [m for m in result if not m.is_read]
        result.sort(key=lambda m: m.timestamp)
        return result

    async def get_broadcast_messages(
        self,
        team_name: str,
        member_name: str,
        unread_only: bool = False,
        from_member_name: Optional[str] = None,
    ) -> List[_MemMessage]:
        result = [
            m for m in self._messages
            if m.team_name == team_name and m.broadcast and m.from_member_name != member_name
        ]
        if from_member_name is not None:
            result = [m for m in result if m.from_member_name == from_member_name]
        result.sort(key=lambda m: m.timestamp)

        if not unread_only:
            return result

        rs = self._read_status.get((member_name, team_name))
        if rs is None:
            return result
        return [m for m in result if m.timestamp > rs.read_at]

    async def get_team_messages(
        self, team_name: str, broadcast: Optional[bool] = None
    ) -> List[_MemMessage]:
        result = [m for m in self._messages if m.team_name == team_name]
        if broadcast is not None:
            result = [m for m in result if m.broadcast == broadcast]
        result.sort(key=lambda m: m.timestamp)
        return result

    async def mark_message_read(self, message_id: str, member_name: str) -> bool:
        async with self._lock:
            msg = None
            for m in self._messages:
                if m.message_id == message_id:
                    msg = m
                    break
            if not msg:
                team_logger.error(f"Message {message_id} not found")
                return False
            if member_name not in self._members:
                team_logger.error(f"Member {member_name} not found")
                return False

            if msg.broadcast:
                key = (member_name, msg.team_name)
                rs = self._read_status.get(key)
                if rs is None:
                    self._read_status[key] = _MemReadStatus(
                        member_name=member_name, team_name=msg.team_name, read_at=msg.timestamp,
                    )
                else:
                    rs.read_at = msg.timestamp
            else:
                msg.is_read = True
            team_logger.info(f"Message {message_id} marked as read by {member_name}")
            return True
