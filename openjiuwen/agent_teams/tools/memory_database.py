# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""In-memory team database that replaces SQLite for single-process mode.

Implements the same public async interface as TeamDatabase so that
TeamBackend, TeamTaskManager, TeamMessageManager and TeamMember can
use it transparently via duck-typing.
"""

from __future__ import annotations

import asyncio
import time
from typing import (
    Dict,
    Iterable,
    List,
    Optional,
)

from pydantic import BaseModel

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
from openjiuwen.agent_teams.tools.database import (
    TASK_DEPENDENCY_REJECT_STATUSES,
    TASK_TERMINAL_STATUSES,
    detect_cycle_in_adjacency,
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
    """Plain task record.

    ``updated_at`` carries the millisecond timestamp of the most recent
    status transition. Its interpretation depends on the current status
    (claimed → claim time, completed → completion time, …). Title/content
    edits do not bump this field.
    """

    def __init__(
        self,
        *,
        task_id: str,
        team_name: str,
        title: str,
        content: str,
        status: str,
        assignee: Optional[str] = None,
        updated_at: Optional[int] = None,
    ) -> None:
        self.task_id = task_id
        self.team_name = team_name
        self.title = title
        self.content = content
        self.status = status
        self.assignee = assignee
        self.updated_at = updated_at

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
    """Plain message record.

    ``is_read`` only applies to direct (non-broadcast) messages. Broadcast
    rows leave it as ``None``; per-recipient read state lives in
    ``_MemReadStatus`` (high-water mark by timestamp).
    """

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
        is_read: Optional[bool] = False,
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

    Mirrors ``TeamDatabase``'s DAO-attribute API by exposing ``team`` /
    ``member`` / ``task`` / ``message`` as self-references — every
    operation already lives on this class, so ``db.team.create_team(...)``
    and ``db.create_team(...)`` call the same method.
    """

    def __init__(self) -> None:
        """Initialize the in-memory store and DAO-shaped self-references."""
        self._teams: dict[str, Team] = {}
        self._members: dict[str, TeamMember] = {}
        # Per-session dynamic data
        self._tasks: dict[str, _MemTask] = {}
        self._task_deps: list[_MemTaskDep] = []
        self._messages: list[_MemMessage] = []
        self._read_status: dict[tuple[str, str], _MemReadStatus] = {}
        self._lock = asyncio.Lock()
        self._initialized = True
        # DAO-shaped facade: every operation lives on this class, so the
        # attribute is just a self-reference. Callers see the same surface
        # as the SQL-backed TeamDatabase (db.team.create_team, etc.).
        self.team = self
        self.member = self
        self.task = self
        self.message = self

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

    async def cleanup_all_runtime_state(self) -> tuple[list[str], list[str]]:
        """Clear all in-memory team runtime state.

        Mirrors ``TeamDatabase.cleanup_all_runtime_state()`` so callers can
        invoke the same API without caring about the storage backend.
        """
        async with self._lock:
            had_dynamic_state = any((self._tasks, self._task_deps, self._messages, self._read_status))
            had_static_state = any((self._teams, self._members))
            self._tasks.clear()
            self._task_deps.clear()
            self._messages.clear()
            self._read_status.clear()
            self._teams.clear()
            self._members.clear()

        deleted_tables = ["memory_dynamic_state"] if had_dynamic_state else []
        cleared_tables = ["team_info", "team_member"] if had_static_state else []
        return deleted_tables, cleared_tables

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
                team_logger.error("Team %s already exists", team_name)
                return False
            now = self.get_current_time()
            self._teams[team_name] = Team(
                team_name=team_name,
                display_name=display_name,
                leader_member_name=leader_member_name,
                desc=desc,
                prompt=prompt,
                created=now,
                updated_at=now,
            )
            team_logger.info("Team %s created", team_name)
            return True

    async def get_team(self, team_name: str) -> Optional[Team]:
        return self._teams.get(team_name)

    async def delete_team(self, team_name: str) -> bool:
        """Delete a team and cascade-purge its members, tasks, and messages."""
        async with self._lock:
            if team_name not in self._teams:
                team_logger.debug("Team %s not found for deletion", team_name)
                return False
            del self._teams[team_name]
            # Cascade
            self._members = {k: v for k, v in self._members.items() if v.team_name != team_name}
            self._tasks = {k: v for k, v in self._tasks.items() if v.team_name != team_name}
            self._task_deps = [d for d in self._task_deps if d.team_name != team_name]
            self._messages = [m for m in self._messages if m.team_name != team_name]
            self._read_status = {k: v for k, v in self._read_status.items() if v.team_name != team_name}
            team_logger.info("Team %s deleted", team_name)
            return True

    async def get_team_updated_at(self, team_name: str) -> int:
        """Probe team.updated_at for change detection.

        Returns the team's updated_at timestamp (ms), or 0 if not found.
        """
        team = self._teams.get(team_name)
        if team is None or team.updated_at is None:
            return 0
        return int(team.updated_at)

    async def force_delete_team_session(self, team_name: str) -> bool:
        """Force delete a team's records and clear in-memory session data."""
        deleted = await self.delete_team(team_name)
        await self.drop_cur_session_tables()
        team_logger.info("Force deleted team session data for {}", team_name)
        return deleted

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
        model_ref_json: Optional[str] = None,
    ) -> bool:
        async with self._lock:
            if member_name in self._members:
                team_logger.error("Member %s already exists", member_name)
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
                model_ref_json=model_ref_json,
                updated_at=self.get_current_time(),
            )
            team_logger.info("Member %s created", member_name)
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

    async def get_members_max_updated_at(self, team_name: str) -> int:
        """Probe MAX(member.updated_at) for the team.

        Returns the largest member update timestamp (ms), or 0 if no members.
        """
        members = [m for m in self._members.values() if m.team_name == team_name]
        if not members:
            return 0
        timestamps = [m.updated_at for m in members if m.updated_at is not None]
        return max(timestamps) if timestamps else 0

    async def update_member_status(self, member_name: str, team_name: str, status: str) -> bool:
        async with self._lock:
            member = self._members.get(member_name)
            if not member or member.team_name != team_name:
                team_logger.error("Member %s not found in team %s", member_name, team_name)
                return False
            if not is_valid_transition(MemberStatus(member.status), MemberStatus(status), MEMBER_TRANSITIONS):
                team_logger.error("Invalid state transition for member %s: %s -> %s", member_name, member.status, status)
                return False
            member.status = status
            team_logger.debug("Member %s status updated to %s", member_name, status)
            return True

    async def update_member_execution_status(self, member_name: str, team_name: str, execution_status: str) -> bool:
        async with self._lock:
            member = self._members.get(member_name)
            if not member or member.team_name != team_name:
                team_logger.error("Member %s not found in team %s", member_name, team_name)
                return False
            if not is_valid_transition(
                ExecutionStatus(member.execution_status),
                ExecutionStatus(execution_status),
                EXECUTION_TRANSITIONS,
            ):
                team_logger.error("Invalid state transition for member %s: %s -> %s", member_name, member.execution_status, execution_status)
                return False
            member.execution_status = execution_status
            team_logger.debug("Member %s execution status updated to %s", member_name, execution_status)
            return True

    # =====================================================================
    # Task Operations
    # =====================================================================

    async def create_task(
        self,
        task_id: str,
        team_name: str,
        title: str,
        content: str,
        status: str,
    ) -> bool:
        async with self._lock:
            if task_id in self._tasks:
                team_logger.error("Task %s already exists", task_id)
                return False
            self._tasks[task_id] = _MemTask(
                task_id=task_id,
                team_name=team_name,
                title=title,
                content=content,
                status=status,
                updated_at=self.get_current_time(),
            )
            team_logger.info("Task %s created", task_id)
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

    async def assign_task(self, task_id: str, member_name: str) -> bool:
        """Assign a task to a member and mark it as claimed."""
        async with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                team_logger.error(f"Task {task_id} not found")
                return False
            if task.assignee:
                team_logger.warning(f"Task {task_id} already assigned to {task.assignee}")
                return False
            if not is_valid_transition(TaskStatus(task.status), TaskStatus.CLAIMED, TASK_TRANSITIONS):
                team_logger.error(
                    f"Invalid state transition for task {task_id}: {task.status} -> {TaskStatus.CLAIMED.value}"
                )
                return False
            task.assignee = member_name
            task.status = TaskStatus.CLAIMED.value
            task.updated_at = self.get_current_time()
            team_logger.info(f"Task {task_id} assigned to {member_name} (status=claimed)")
            return True

    async def claim_task(self, task_id: str, member_name: str) -> bool:
        async with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                team_logger.error("Task %s not found", task_id)
                return False
            # Surface assignee conflicts before the state-transition check so
            # a task already held by another member does not masquerade as an
            # "invalid claimed → claimed transition" error.
            if task.assignee:
                team_logger.warning("Task %s is already claimed by member %s", task_id, task.assignee)
                return False
            if not is_valid_transition(TaskStatus(task.status), TaskStatus.CLAIMED, TASK_TRANSITIONS):
                team_logger.error("Invalid state transition for task %s: %s -> %s", task_id, task.status, TaskStatus.CLAIMED.value)
                return False
            task.status = TaskStatus.CLAIMED.value
            task.assignee = member_name
            task.updated_at = self.get_current_time()
            team_logger.info("Task %s claimed by member %s", task_id, member_name)
            return True

    async def reset_task(self, task_id: str) -> Optional[_MemTask]:
        async with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                team_logger.error("Task %s not found", task_id)
                return None
            if task.status != TaskStatus.CLAIMED.value:
                team_logger.error("Cannot reset task %s with status %s, only CLAIMED tasks can be reset", task_id, task.status)
                return None
            if not is_valid_transition(TaskStatus(task.status), TaskStatus.PENDING, TASK_TRANSITIONS):
                team_logger.error("Invalid state transition for task %s: %s -> %s", task_id, task.status, TaskStatus.PENDING.value)
                return None
            task.status = TaskStatus.PENDING.value
            task.assignee = None
            task.updated_at = self.get_current_time()
            team_logger.info("Task %s reset to PENDING", task_id)
            return task

    async def approve_plan_task(self, task_id: str) -> Optional[_MemTask]:
        async with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                team_logger.error("Task %s not found", task_id)
                return None
            if not is_valid_transition(TaskStatus(task.status), TaskStatus.PLAN_APPROVED, TASK_TRANSITIONS):
                team_logger.error("Invalid state transition for task %s: %s -> %s", task_id, task.status, TaskStatus.PLAN_APPROVED.value)
                return None
            task.status = TaskStatus.PLAN_APPROVED.value
            task.updated_at = self.get_current_time()
            team_logger.info("Task %s approved from CLAIMED to PLAN_APPROVED", task_id)
            return task

    async def update_task_status(self, task_id: str, status: str) -> bool:
        async with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                team_logger.error("Task %s not found", task_id)
                return False
            if not is_valid_transition(TaskStatus(task.status), TaskStatus(status), TASK_TRANSITIONS):
                team_logger.error("Invalid state transition for task %s: %s -> %s", task_id, task.status, status)
                return False
            task.status = status
            task.updated_at = self.get_current_time()
            if status == TaskStatus.COMPLETED.value:
                for dep in self._task_deps:
                    if dep.depends_on_task_id == task_id and not dep.resolved:
                        dep.resolved = True
            team_logger.info("Task %s status updated to %s", task_id, status)
            return True

    async def update_task(self, task_id: str, title: Optional[str] = None, content: Optional[str] = None) -> bool:
        async with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                team_logger.error("Task %s not found", task_id)
                return False
            if task.status in (TaskStatus.CLAIMED.value, TaskStatus.PLAN_APPROVED.value):
                team_logger.error("Cannot update task %s because it is currently %s", task_id, task.status)
                return False
            if title is not None:
                task.title = title
            if content is not None:
                task.content = content
            return True

    def _refresh_status_for_tasks(self, task_ids: Iterable[str], now: int) -> List[_MemTask]:
        """In-memory mirror of the SQL ``_refresh_status_in_session`` helper.

        Caller must hold ``self._lock``. Same rules: PENDING with
        unresolved deps becomes BLOCKED; BLOCKED with no unresolved deps
        becomes PENDING; everything else is left alone.
        """
        unique_ids = {tid for tid in task_ids if tid}
        if not unique_ids:
            return []
        refreshed: List[_MemTask] = []
        for tid in unique_ids:
            task = self._tasks.get(tid)
            if task is None:
                continue
            if task.status not in (TaskStatus.PENDING.value, TaskStatus.BLOCKED.value):
                continue
            unresolved = sum(1 for d in self._task_deps if d.task_id == tid and not d.resolved)
            if task.status == TaskStatus.PENDING.value and unresolved > 0:
                task.status = TaskStatus.BLOCKED.value
                task.updated_at = now
                refreshed.append(task)
                team_logger.info("Task %s blocked (%d unresolved deps)", tid, unresolved)
            elif task.status == TaskStatus.BLOCKED.value and unresolved == 0:
                task.status = TaskStatus.PENDING.value
                task.updated_at = now
                refreshed.append(task)
                team_logger.info("Task %s unblocked (all deps resolved)", tid)
        return refreshed

    def _terminate_task_locked(
        self,
        task_id: str,
        new_status: TaskStatus,
        now: int,
    ) -> Optional[tuple[_MemTask, List[_MemTask]]]:
        """In-memory mirror of the SQL ``_terminate_task_in_session`` helper.

        Caller must hold ``self._lock``.
        """
        if new_status not in (TaskStatus.COMPLETED, TaskStatus.CANCELLED):
            raise ValueError(f"_terminate_task_locked expects a terminal status, got {new_status}")

        task = self._tasks.get(task_id)
        if task is None:
            team_logger.error("Task %s not found", task_id)
            return None
        if task.status == new_status.value:
            team_logger.debug("Task %s already %s", task_id, new_status.value)
            return task, []
        if not is_valid_transition(TaskStatus(task.status), new_status, TASK_TRANSITIONS):
            team_logger.error("Invalid state transition for task %s: %s -> %s", task_id, task.status, new_status.value)
            return None

        task.status = new_status.value
        task.updated_at = now
        team_logger.info("Task %s %s at %s", task_id, new_status.value, now)

        downstream_ids: set[str] = set()
        for dep in self._task_deps:
            if dep.depends_on_task_id == task_id:
                if not dep.resolved:
                    dep.resolved = True
                downstream_ids.add(dep.task_id)

        refreshed = self._refresh_status_for_tasks(downstream_ids, now)
        return task, refreshed

    async def mutate_dependency_graph(
        self,
        team_name: str,
        *,
        new_tasks: Optional[List[NewTaskSpec]] = None,
        add_edges: Optional[List[tuple[str, str]]] = None,
    ) -> GraphMutationResult:
        """In-memory mirror of ``TeamDatabase.mutate_dependency_graph``."""
        new_tasks = list(new_tasks or [])
        add_edges = list(add_edges or [])
        if not new_tasks and not add_edges:
            return GraphMutationResult.success()

        async with self._lock:
            now = self.get_current_time()

            # 1. Validate and stage new tasks (do not mutate state until cycle check passes).
            seen_new_ids: set[str] = set()
            for spec in new_tasks:
                if spec.task_id in seen_new_ids:
                    return GraphMutationResult.fail(f"Duplicate task_id {spec.task_id} in new_tasks")
                seen_new_ids.add(spec.task_id)
                if spec.task_id in self._tasks:
                    return GraphMutationResult.fail(f"Task {spec.task_id} already exists")

            # 2. Validate edge endpoints. New tasks count as existing once staged.
            staged_status: Dict[str, str] = {spec.task_id: spec.initial_status for spec in new_tasks}
            for tid, dep_id in add_edges:
                src_status = staged_status.get(tid) or (self._tasks[tid].status if tid in self._tasks else None)
                dst_status = staged_status.get(dep_id) or (
                    self._tasks[dep_id].status if dep_id in self._tasks else None
                )
                if src_status is None:
                    return GraphMutationResult.fail(f"Task {tid} not found")
                if dst_status is None:
                    return GraphMutationResult.fail(f"Dependency target {dep_id} not found")
                # Reject when the source (the task gaining a new dep) is already
                # executing or terminal — see TASK_DEPENDENCY_REJECT_STATUSES.
                if tid not in seen_new_ids and src_status in TASK_DEPENDENCY_REJECT_STATUSES:
                    return GraphMutationResult.fail(
                        f"Cannot add dependency to {tid} in terminal or executing status: {src_status}"
                    )

            # 3. Build the post-mutation adjacency and run a single cycle check.
            existing_edge_set: set[tuple[str, str]] = {
                (d.task_id, d.depends_on_task_id) for d in self._task_deps if d.team_name == team_name
            }
            adjacency: Dict[str, List[str]] = {}
            for src, dst in existing_edge_set:
                adjacency.setdefault(src, []).append(dst)
            new_edge_set: set[tuple[str, str]] = set()
            for tid, dep_id in add_edges:
                edge = (tid, dep_id)
                if edge in existing_edge_set or edge in new_edge_set:
                    continue
                new_edge_set.add(edge)
                adjacency.setdefault(tid, []).append(dep_id)

            cycle = detect_cycle_in_adjacency(adjacency)
            if cycle is not None:
                return GraphMutationResult.fail(f"Circular dependency detected: {' -> '.join(cycle)}")

            # 4. Apply: insert new tasks then new edges.
            for spec in new_tasks:
                self._tasks[spec.task_id] = _MemTask(
                    task_id=spec.task_id,
                    team_name=team_name,
                    title=spec.title,
                    content=spec.content,
                    status=spec.initial_status,
                    updated_at=now,
                )
            for tid, dep_id in new_edge_set:
                dep_status = staged_status.get(dep_id) or self._tasks[dep_id].status
                initial_resolved = dep_status in TASK_TERMINAL_STATUSES
                self._task_deps.append(
                    _MemTaskDep(
                        task_id=tid,
                        depends_on_task_id=dep_id,
                        team_name=team_name,
                        resolved=initial_resolved,
                    )
                )

            # 5. Refresh status for affected tasks.
            affected_ids: set[str] = {spec.task_id for spec in new_tasks}
            affected_ids.update(tid for tid, _ in new_edge_set)
            refreshed = self._refresh_status_for_tasks(affected_ids, now)

            if new_tasks:
                team_logger.info("Created %d task(s); added %d edge(s); refreshed %d task(s)", len(new_tasks), len(new_edge_set), len(refreshed))
            else:
                team_logger.info("Added %d edge(s); refreshed %d task(s)", len(new_edge_set), len(refreshed))
            return GraphMutationResult.success(refreshed_tasks=list(refreshed))

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
        """Thin wrapper over ``mutate_dependency_graph`` for the legacy shape."""
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
            team_logger.error("Failed to create task %s: %s", task_id, result.reason)
        return result.ok

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
                team_logger.debug("Task %s not found for deletion", task_id)
                return False
            del self._tasks[task_id]
            self._task_deps = [d for d in self._task_deps if d.task_id != task_id and d.depends_on_task_id != task_id]
            team_logger.info("Task %s deleted", task_id)
            return True

    async def cancel_task(self, task_id: str) -> Optional[Dict]:
        async with self._lock:
            outcome = self._terminate_task_locked(task_id, TaskStatus.CANCELLED, self.get_current_time())
            if outcome is None:
                return None
            task, unblocked = outcome
            return {"task": task, "unblocked_tasks": unblocked}

    async def cancel_all_tasks(
        self,
        team_name: str,
        skip_assignees: Optional[set[str]] = None,
    ) -> Dict:
        async with self._lock:
            skip_assignees = skip_assignees or set()
            now = self.get_current_time()
            cancelled: List[_MemTask] = []
            unblocked_by_id: Dict[str, _MemTask] = {}
            # Snapshot the candidate IDs first — _terminate_task_locked mutates
            # the very dict we'd otherwise iterate over (dependent task statuses
            # flip during downstream refresh).
            already_terminal = {TaskStatus.CANCELLED.value, TaskStatus.COMPLETED.value}
            candidate_ids = [
                t.task_id
                for t in self._tasks.values()
                if t.team_name == team_name
                and t.status not in already_terminal
                and t.assignee not in skip_assignees
            ]
            for tid in candidate_ids:
                outcome = self._terminate_task_locked(tid, TaskStatus.CANCELLED, now)
                if outcome is None:
                    continue
                task, refreshed = outcome
                cancelled.append(task)
                for t in refreshed:
                    unblocked_by_id[t.task_id] = t
            cancelled_ids = {t.task_id for t in cancelled}
            unblocked_tasks = [t for tid, t in unblocked_by_id.items() if tid not in cancelled_ids]
            team_logger.info("Cancelled %d tasks for team %s; unblocked %d", len(cancelled), team_name, len(unblocked_tasks))
            return {"cancelled_tasks": cancelled, "unblocked_tasks": unblocked_tasks}

    async def complete_task(self, task_id: str) -> Optional[Dict]:
        async with self._lock:
            outcome = self._terminate_task_locked(task_id, TaskStatus.COMPLETED, self.get_current_time())
            if outcome is None:
                return None
            task, unblocked = outcome
            return {"task": task, "unblocked_tasks": unblocked}

    async def _verify_and_fix_blocked_tasks(self, team_name: str) -> List[_MemTask]:
        async with self._lock:
            blocked_ids = [
                t.task_id
                for t in self._tasks.values()
                if t.team_name == team_name and t.status == TaskStatus.BLOCKED.value
            ]
            if not blocked_ids:
                return []
            return self._refresh_status_for_tasks(blocked_ids, self.get_current_time())

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
        is_read: bool = False,
    ) -> bool:
        async with self._lock:
            for m in self._messages:
                if m.message_id == message_id:
                    team_logger.error("Message %s already exists", message_id)
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
                    # Broadcast rows leave is_read NULL — per-member read
                    # state lives in _MemReadStatus instead.
                    is_read=None if broadcast else is_read,
                )
            )
            team_logger.debug("Message %s created", message_id)
            return True

    async def get_messages(
        self,
        team_name: str,
        to_member_name: str,
        unread_only: bool = False,
        from_member_name: Optional[str] = None,
    ) -> List[_MemMessage]:
        result = [
            m
            for m in self._messages
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
            m for m in self._messages if m.team_name == team_name and m.broadcast and m.from_member_name != member_name
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

    async def get_team_messages(self, team_name: str, broadcast: Optional[bool] = None) -> List[_MemMessage]:
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
                team_logger.error("Message %s not found", message_id)
                return False
            if member_name not in self._members:
                team_logger.error("Member %s not found", member_name)
                return False

            if msg.broadcast:
                key = (member_name, msg.team_name)
                rs = self._read_status.get(key)
                if rs is None:
                    self._read_status[key] = _MemReadStatus(
                        member_name=member_name,
                        team_name=msg.team_name,
                        read_at=msg.timestamp,
                    )
                else:
                    rs.read_at = msg.timestamp
            else:
                msg.is_read = True
            team_logger.debug("Message %s marked as read by %s", message_id, member_name)
            return True
