# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Task and task-dependency data access object."""

from typing import Dict, Iterable, List, Optional

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from openjiuwen.agent_teams.schema.status import (
    TASK_TRANSITIONS,
    TaskStatus,
    is_valid_transition,
)
from openjiuwen.agent_teams.schema.task import (
    GraphMutationResult,
    NewTaskSpec,
)
from openjiuwen.agent_teams.tools.database.engine import get_current_time
from openjiuwen.agent_teams.tools.database.graph import (
    _TASK_DEPENDENCY_REJECT_STATUSES,
    _TASK_TERMINAL_STATUSES,
    detect_cycle_in_adjacency,
)
from openjiuwen.agent_teams.tools.models import (
    TeamTaskBase,
    TeamTaskDependencyBase,
    _get_task_dependency_model,
    _get_task_model,
)
from openjiuwen.core.common.logging import team_logger


class TaskDao:
    """Data access object for task and task-dependency tables."""

    def __init__(self, session_local: async_sessionmaker) -> None:
        self._session_local = session_local

    async def create_task(
        self,
        task_id: str,
        team_name: str,
        title: str,
        content: str,
        status: str,
    ) -> bool:
        """Create a new team task."""
        team_task_model = _get_task_model()
        async with self._session_local() as session:
            try:
                task = team_task_model(
                    task_id=task_id,
                    team_name=team_name,
                    title=title,
                    content=content,
                    status=status,
                    updated_at=get_current_time(),
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
        """Get task information by ID."""
        team_task_model = _get_task_model()
        async with self._session_local() as session:
            result = await session.execute(select(team_task_model).where(team_task_model.task_id == task_id))
            return result.scalar_one_or_none()

    async def get_team_tasks(self, team_name: str, status: Optional[str] = None) -> List[TeamTaskBase]:
        """Get all tasks for a team, optionally filtered by status."""
        team_task_model = _get_task_model()
        async with self._session_local() as session:
            query = select(team_task_model).where(team_task_model.team_name == team_name)
            if status:
                query = query.where(team_task_model.status == status)
            result = await session.execute(query)
            return result.scalars().all()

    async def get_tasks_by_assignee(
        self, team_name: str, assignee_id: str, status: Optional[str] = None
    ) -> List[TeamTaskBase]:
        """Get tasks assigned to a specific member, optionally filtered by status."""
        team_task_model = _get_task_model()
        async with self._session_local() as session:
            query = select(team_task_model).where(
                team_task_model.team_name == team_name,
                team_task_model.assignee == assignee_id,
            )
            if status:
                query = query.where(team_task_model.status == status)
            result = await session.execute(query)
            return result.scalars().all()

    async def assign_task(self, task_id: str, member_name: str) -> bool:
        """Assign a task to a member and mark it as claimed."""
        team_task_model = _get_task_model()
        async with self._session_local() as session:
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
            task.updated_at = get_current_time()
            await session.commit()
            team_logger.info(f"Task {task_id} assigned to {member_name} (status=claimed)")
            return True

    async def claim_task(self, task_id: str, member_name: str) -> bool:
        """Claim a task for a member."""
        team_task_model = _get_task_model()
        async with self._session_local() as session:
            result = await session.execute(select(team_task_model).where(team_task_model.task_id == task_id))
            task = result.scalar_one_or_none()
            if not task:
                team_logger.error(f"Task {task_id} not found")
                return False

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
            task.updated_at = get_current_time()
            await session.commit()
            team_logger.info(f"Task {task_id} claimed by member {member_name}")
            return True

    async def reset_task(self, task_id: str) -> Optional[TeamTaskBase]:
        """Reset a claimed or plan_approved task back to pending status and clear assignee."""
        team_task_model = _get_task_model()
        async with self._session_local() as session:
            result = await session.execute(select(team_task_model).where(team_task_model.task_id == task_id))
            task = result.scalar_one_or_none()
            if not task:
                team_logger.error(f"Task {task_id} not found")
                return None

            if task.status != TaskStatus.CLAIMED.value:
                team_logger.error(
                    f"Cannot reset task {task_id} with status {task.status}, only CLAIMED tasks can be reset"
                )
                return None

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
            task.updated_at = get_current_time()
            await session.commit()
            team_logger.info(f"Task {task_id} reset from {origin_task_status} to PENDING")

            return task

    async def approve_plan_task(self, task_id: str) -> Optional[TeamTaskBase]:
        """Approve a task plan for PLAN_MODE members."""
        team_task_model = _get_task_model()
        async with self._session_local() as session:
            result = await session.execute(select(team_task_model).where(team_task_model.task_id == task_id))
            task = result.scalar_one_or_none()
            if not task:
                team_logger.error(f"Task {task_id} not found")
                return None

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
            task.updated_at = get_current_time()
            await session.commit()
            team_logger.info(f"Task {task_id} approved from CLAIMED to PLAN_APPROVED")

            return task

    async def update_task_status(self, task_id: str, status: str) -> bool:
        """Update task status."""
        team_task_model = _get_task_model()
        task_dependency_model = _get_task_dependency_model()
        async with self._session_local() as session:
            result = await session.execute(select(team_task_model).where(team_task_model.task_id == task_id))
            task = result.scalar_one_or_none()
            if not task:
                team_logger.error(f"Task {task_id} not found")
                return False

            if not is_valid_transition(
                TaskStatus(task.status),
                TaskStatus(status),
                TASK_TRANSITIONS,
            ):
                team_logger.error(f"Invalid state transition for task {task_id}: {task.status} -> {status}")
                return False

            now = get_current_time()
            task.status = status
            task.updated_at = now

            if status == TaskStatus.COMPLETED.value:
                team_logger.info(f"Task {task_id} completed at {now}")

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

            await session.commit()
            team_logger.info(f"Task {task_id} status updated to {status}")
            return True

    async def update_task(
        self,
        task_id: str,
        title: Optional[str] = None,
        content: Optional[str] = None,
    ) -> bool:
        """Update task content (title, content, etc.)."""
        team_task_model = _get_task_model()
        async with self._session_local() as session:
            result = await session.execute(select(team_task_model).where(team_task_model.task_id == task_id))
            task = result.scalar_one_or_none()
            if not task:
                team_logger.error(f"Task {task_id} not found")
                return False

            if task.status in (
                TaskStatus.CLAIMED.value,
                TaskStatus.PLAN_APPROVED.value,
            ):
                team_logger.error(f"Cannot update task {task_id} because it is currently {task.status}")
                return False

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
        """Recompute PENDING/BLOCKED status for tasks based on unresolved deps."""
        unique_ids = list({tid for tid in task_ids if tid})
        if not unique_ids:
            return []

        team_task_model = _get_task_model()
        task_dependency_model = _get_task_dependency_model()

        tasks_result = await session.execute(select(team_task_model).where(team_task_model.task_id.in_(unique_ids)))
        candidates = [
            t for t in tasks_result.scalars().all() if t.status in (TaskStatus.PENDING.value, TaskStatus.BLOCKED.value)
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
        """Terminate a task and propagate dependency resolution downstream."""
        if new_status not in (TaskStatus.COMPLETED, TaskStatus.CANCELLED):
            raise ValueError(f"_terminate_task_in_session expects a terminal status, got {new_status}")

        team_task_model = _get_task_model()
        task_dependency_model = _get_task_dependency_model()

        result = await session.execute(select(team_task_model).where(team_task_model.task_id == task_id))
        task = result.scalar_one_or_none()
        if task is None:
            team_logger.error(f"Task {task_id} not found")
            return None

        if task.status == new_status.value:
            team_logger.debug(f"Task {task_id} already {new_status.value}")
            return task, []

        if not is_valid_transition(TaskStatus(task.status), new_status, TASK_TRANSITIONS):
            team_logger.error(f"Invalid state transition for task {task_id}: {task.status} -> {new_status.value}")
            return None

        task.status = new_status.value
        task.updated_at = now
        team_logger.info(f"Task {task_id} {new_status.value} at {now}")

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
            select(task_dependency_model.task_id).where(task_dependency_model.depends_on_task_id == task_id).distinct()
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
        """Atomic dependency-graph mutation: insert nodes and/or edges."""
        new_tasks = list(new_tasks or [])
        add_edges = list(add_edges or [])
        if not new_tasks and not add_edges:
            return GraphMutationResult.success()

        team_task_model = _get_task_model()
        task_dependency_model = _get_task_dependency_model()

        async with self._session_local() as session:
            try:
                now = get_current_time()

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
                    await session.flush()

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
                        continue
                    new_edge_set.add(edge)
                    adjacency.setdefault(tid, []).append(dep_id)

                cycle = detect_cycle_in_adjacency(adjacency)
                if cycle is not None:
                    await session.rollback()
                    return GraphMutationResult.fail(f"Circular dependency detected: {' -> '.join(cycle)}")

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
                    team_logger.info(f"Added {len(new_edge_set)} edge(s); refreshed {len(refreshed)} task(s)")
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
        """Create a task and wire it into the dependency chain atomically."""
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
        """Get all dependencies for a task."""
        task_dependency_model = _get_task_dependency_model()
        async with self._session_local() as session:
            result = await session.execute(
                select(task_dependency_model).where(task_dependency_model.task_id == task_id)
            )
            rows = result.scalars().all()
            return rows

    async def get_unresolved_dependencies_count(self, task_id: str) -> int:
        """Get count of unresolved dependencies for a task."""
        task_dependency_model = _get_task_dependency_model()
        async with self._session_local() as session:
            result = await session.execute(
                select(task_dependency_model).where(
                    task_dependency_model.task_id == task_id,
                    task_dependency_model.resolved.is_(False),
                )
            )
            return len(result.scalars().all())

    async def get_tasks_depending_on(self, depends_on_task_id: str) -> List[TeamTaskBase]:
        """Get all tasks that depend on a specific task."""
        task_dependency_model = _get_task_dependency_model()
        team_task_model = _get_task_model()
        async with self._session_local() as session:
            result = await session.execute(
                select(task_dependency_model).where(task_dependency_model.depends_on_task_id == depends_on_task_id)
            )
            deps = result.scalars().all()

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
        """Delete a task."""
        team_task_model = _get_task_model()
        async with self._session_local() as session:
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
        """Cancel a task atomically and unblock dependent tasks."""
        async with self._session_local() as session:
            now = get_current_time()
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
        """Cancel every active task for a team atomically."""
        team_task_model = _get_task_model()
        skip_assignees = skip_assignees or set()
        async with self._session_local() as session:
            skip_statuses = [
                TaskStatus.CANCELLED.value,
                TaskStatus.COMPLETED.value,
            ]
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

            now = get_current_time()
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
                for t in refreshed:
                    unblocked_by_id[t.task_id] = t

            await session.commit()
            cancelled_ids = {t.task_id for t in cancelled_tasks}
            unblocked_tasks = [t for tid, t in unblocked_by_id.items() if tid not in cancelled_ids]
            team_logger.info(
                f"Cancelled {len(cancelled_tasks)} tasks for team {team_name}; unblocked {len(unblocked_tasks)}"
            )
            return {
                "cancelled_tasks": cancelled_tasks,
                "unblocked_tasks": unblocked_tasks,
            }

    async def complete_task(self, task_id: str) -> Optional[Dict]:
        """Complete a task atomically and unblock dependent tasks."""
        async with self._session_local() as session:
            now = get_current_time()
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
        """Recovery sweep: re-evaluate every BLOCKED task in the team."""
        team_task_model = _get_task_model()
        async with self._session_local() as session:
            result = await session.execute(
                select(team_task_model.task_id).where(
                    team_task_model.team_name == team_name,
                    team_task_model.status == TaskStatus.BLOCKED.value,
                )
            )
            blocked_ids = [row[0] for row in result.all()]
            if not blocked_ids:
                return []

            now = get_current_time()
            refreshed = await self._refresh_status_in_session(session, blocked_ids, now)
            await session.commit()
            return refreshed

    async def verify_and_fix_task_consistency(self, team_name: str) -> List[TeamTaskBase]:
        """Verify and fix task consistency for a team."""
        return await self._verify_and_fix_blocked_tasks(team_name)
