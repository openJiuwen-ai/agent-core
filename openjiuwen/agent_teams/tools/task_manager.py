# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Team Task Manager Module

This module provides task management functionality for agent teams.
"""

import uuid
from typing import (
    List,
    Optional,
)

from openjiuwen.agent_teams.messager import Messager
from openjiuwen.agent_teams.schema.events import (
    EventMessage,
    TaskCancelledEvent,
    TaskClaimedEvent,
    TaskCompletedEvent,
    TaskCreatedEvent,
    TaskUnblockedEvent,
    TaskUpdatedEvent,
    TeamTopic,
)
from openjiuwen.agent_teams.schema.status import (
    TASK_TRANSITIONS,
    MemberMode,
    TaskStatus,
    is_valid_transition,
)
from openjiuwen.agent_teams.schema.task import (
    NewTaskSpec,
    TaskCreateResult,
    TaskDetail,
    TaskListResult,
    TaskOpResult,
    TaskSummary,
)
from openjiuwen.agent_teams.spawn.context import get_session_id
from openjiuwen.agent_teams.tools.database import (
    TeamDatabase,
    TeamTaskBase,
    TeamTaskDependencyBase,
)
from openjiuwen.core.common.logging import team_logger


class TeamTaskManager:
    """Manager for team tasks

    This class provides methods to add, claim, complete, and manage tasks
    within a team.

    Attributes:
        db: Team database instance
        team_name: Team identifier
        member_name: Current member identifier
        messager: Messager instance for event publishing
    """

    def __init__(self, team_name: str, member_name: str, db: TeamDatabase, messager: Messager):
        """Initialize task manager.

        Args:
            db: Team database instance.
            team_name: Team identifier.
            member_name: Current member identifier.
            messager: Messager instance for event publishing.
        """
        self.db = db
        self.team_name = team_name
        self.member_name = member_name
        self.messager = messager

    async def add_batch(self, tasks: List[dict]) -> List[TaskCreateResult]:
        """Add multiple tasks to the team in batch.

        Creates multiple tasks in a single operation for efficiency. Each
        task in the list is a dictionary with keys ``title``, ``content``,
        optional ``task_id`` and ``dependencies``. Invalid specs (missing
        title/content) are skipped silently.

        Args:
            tasks: List of task dictionaries.

        Returns:
            List of ``TaskCreateResult`` — one per successfully created
            task. Failed specs (missing fields, creation errors) are
            omitted so callers can still treat the return value as a
            list of created tasks. Because ``TaskCreateResult`` delegates
            attribute lookups to the wrapped task, existing call sites
            that iterate and read ``.task_id`` / ``.title`` work
            unchanged.
        """
        created_tasks: List[TaskCreateResult] = []
        for task_spec in tasks:
            title = task_spec.get("title")
            content = task_spec.get("content")
            task_id = task_spec.get("task_id")
            dependencies = task_spec.get("dependencies")

            if not title or not content:
                team_logger.warning(f"Skipping invalid task: {task_spec}")
                continue

            result = await self.add(
                title=title,
                content=content,
                task_id=task_id,
                dependencies=dependencies,
            )
            if result.ok:
                created_tasks.append(result)
            else:
                team_logger.warning(f"Batch add skipped task {task_id or title!r}: {result.reason}")

        team_logger.info(f"Batch added {len(created_tasks)} tasks")
        return created_tasks

    async def add(
        self,
        title: str,
        content: str,
        task_id: Optional[str] = None,
        dependencies: Optional[List[str]] = None,
    ) -> TaskCreateResult:
        """Add a task to the team.

        Creates a task in the team_task table and optionally adds
        task dependencies to the team_task_dependency table.

        Args:
            title: Task title.
            content: Task content.
            task_id: Optional custom task ID (auto-generated if not provided).
            dependencies: List of task IDs this task depends on.

        Returns:
            ``TaskCreateResult`` — on success ``result.task`` carries the
            created task (and attribute access like ``result.task_id``
            / ``result.title`` transparently delegates to it); on
            failure ``result.reason`` holds the specific cause.
        """
        if task_id is None:
            task_id = str(uuid.uuid4())

        # Initial seed: PENDING for an isolated task; the graph mutation
        # path's refresh pass will flip it to BLOCKED if any dependency
        # is still unresolved at insert time.
        status = TaskStatus.PENDING.value

        if dependencies:
            # Edges + node go through the unified mutation primitive so
            # cycle detection and BLOCKED/PENDING refresh fire as one
            # atomic step.
            mutation = await self.db.task.mutate_dependency_graph(
                team_name=self.team_name,
                new_tasks=[
                    NewTaskSpec(
                        task_id=task_id,
                        title=title,
                        content=content,
                        initial_status=status,
                    )
                ],
                add_edges=[(task_id, dep_id) for dep_id in dependencies],
            )
            if not mutation.ok:
                return TaskCreateResult.fail(f"Failed to create task {task_id}: {mutation.reason}")
            # Refresh pass may have flipped PENDING -> BLOCKED; reflect
            # that in the response so the caller and downstream event
            # carry the right status.
            for refreshed in mutation.refreshed_tasks:
                if refreshed.task_id == task_id:
                    status = refreshed.status
                    break
            team_logger.debug(f"Added task {task_id} with dependencies: {dependencies}")
        else:
            success = await self.db.task.create_task(
                task_id=task_id,
                team_name=self.team_name,
                title=title,
                content=content,
                status=status,
            )
            if not success:
                return TaskCreateResult.fail(f"Failed to create task {task_id} (likely a task_id collision)")

        # Publish task created event
        try:
            await self.messager.publish(
                topic_id=TeamTopic.TASK.build(get_session_id(), self.team_name),
                message=EventMessage.from_event(
                    TaskCreatedEvent(
                        team_name=self.team_name,
                        task_id=task_id,
                        status=status,
                    )
                ),
            )
            team_logger.debug(f"Task created event published: {task_id}")
        except Exception as e:
            team_logger.error(f"Failed to publish task created event for {task_id}: {e}")

        return TaskCreateResult.success(
            TeamTaskBase(
                task_id=task_id,
                team_name=self.team_name,
                title=title,
                content=content,
                status=status,
                assignee=None,
            )
        )

    async def add_with_priority(
        self,
        title: str,
        content: str,
        task_id: Optional[str] = None,
        dependencies: Optional[List[str]] = None,
        dependent_task_ids: Optional[List[str]] = None,
    ) -> TaskCreateResult:
        """Add a task with bidirectional dependency support (prioritized task)

        This method allows creating a task that can:
        1. Depend on existing tasks (dependencies parameter)
        2. Have existing tasks depend on it (dependent_task_ids parameter)
        3. Both of the above (inserting the task between other tasks in the dependency chain)

        When existing tasks are made to depend on the new task (dependent_task_ids),
        their status is automatically updated from 'pending' to 'blocked' if applicable.

        This operation is atomic and prevents circular dependencies.

        Args:
            title: Task title
            content: Task content
            task_id: Optional custom task ID (auto-generated if not provided)
            dependencies: List of existing task IDs that the new task depends on
            dependent_task_ids: List of existing task IDs that should depend on the new task

        Returns:
            ``TaskCreateResult`` describing the outcome. On failure
            ``result.reason`` typically points at a circular dependency
            or a conflicting task_id.
        """
        if task_id is None:
            task_id = str(uuid.uuid4())

        # Determine initial status based on dependencies
        # If the task has dependencies, it starts as BLOCKED
        if dependencies and len(dependencies) > 0:
            status = TaskStatus.BLOCKED.value
        else:
            status = TaskStatus.PENDING.value

        # Use database method for atomic operation with cycle detection
        success = await self.db.task.add_task_with_bidirectional_dependencies(
            task_id=task_id,
            team_name=self.team_name,
            title=title,
            content=content,
            status=status,
            dependencies=dependencies,
            dependent_task_ids=dependent_task_ids,
        )

        if not success:
            return TaskCreateResult.fail(
                f"Failed to create prioritized task {task_id} "
                f"(circular dependency, missing dependent task, or task_id collision)"
            )

        # Publish task created event
        try:
            await self.messager.publish(
                topic_id=TeamTopic.TASK.build(get_session_id(), self.team_name),
                message=EventMessage.from_event(
                    TaskCreatedEvent(
                        team_name=self.team_name,
                        task_id=task_id,
                        status=status,
                    )
                ),
            )
            team_logger.debug(f"Task created event published: {task_id}")
        except Exception as e:
            team_logger.error(f"Failed to publish task created event for {task_id}: {e}")

        return TaskCreateResult.success(
            TeamTaskBase(
                task_id=task_id,
                team_name=self.team_name,
                title=title,
                content=content,
                status=status,
                assignee=None,
            )
        )

    async def add_as_top_priority(
        self,
        title: str,
        content: str,
        task_id: Optional[str] = None,
    ) -> TaskCreateResult:
        """Add a task as top priority (blocks all existing pending/blockable tasks)

        This method creates a new task and makes all existing tasks that can be blocked
        (pending or claimed status) depend on it. This ensures the new task is executed
        before those tasks.

        This is useful for inserting urgent tasks that must be processed before
        any other pending work.

        This operation is atomic and prevents circular dependencies.

        Args:
            title: Task title.
            content: Task content.
            task_id: Optional custom task ID (auto-generated if not provided).

        Returns:
            ``TaskCreateResult`` describing the outcome.
        """
        if task_id is None:
            task_id = str(uuid.uuid4())

        # Get all tasks that can be blocked (pending or claimed status)
        # These tasks will be made to depend on the new top priority task
        pending_tasks = await self.list_tasks(status=TaskStatus.PENDING.value)

        dependent_task_ids = [task.task_id for task in pending_tasks]

        # Top priority task has no dependencies, so it starts as PENDING
        status = TaskStatus.PENDING.value

        # Use database method for atomic operation with cycle detection
        success = await self.db.task.add_task_with_bidirectional_dependencies(
            task_id=task_id,
            team_name=self.team_name,
            title=title,
            content=content,
            status=status,
            dependencies=None,
            dependent_task_ids=dependent_task_ids if dependent_task_ids else None,
        )

        if not success:
            return TaskCreateResult.fail(
                f"Failed to create top priority task {task_id} (circular dependency or task_id collision)"
            )

        team_logger.info(f"Added top priority task {task_id}, blocking {len(dependent_task_ids)} existing tasks")

        # Publish task created event
        try:
            await self.messager.publish(
                topic_id=TeamTopic.TASK.build(get_session_id(), self.team_name),
                message=EventMessage.from_event(
                    TaskCreatedEvent(
                        team_name=self.team_name,
                        task_id=task_id,
                        status=status,
                    )
                ),
            )
            team_logger.debug(f"Task created event published: {task_id}")
        except Exception as e:
            team_logger.error(f"Failed to publish task created event for {task_id}: {e}")

        return TaskCreateResult.success(
            TeamTaskBase(
                task_id=task_id,
                team_name=self.team_name,
                title=title,
                content=content,
                status=status,
                assignee=None,
            )
        )

    async def list_tasks_with_deps(self, status: Optional[str] = None) -> TaskListResult:
        """List tasks with blocked_by info (summary view, no content).

        Returns a lightweight task list where each entry includes unresolved
        dependency IDs. Suitable for list/claimable actions.

        Args:
            status: Optional status filter.

        Returns:
            TaskListResult containing task summaries with dependency info.
        """
        tasks = await self.list_tasks(status=status)
        summaries = []
        for task in tasks:
            deps = await self.get_dependencies(task.task_id)
            unresolved = [d.depends_on_task_id for d in deps if not d.resolved]
            summaries.append(
                TaskSummary(
                    task_id=task.task_id,
                    title=task.title,
                    status=task.status,
                    assignee=task.assignee,
                    blocked_by=unresolved,
                )
            )
        return TaskListResult(tasks=summaries, count=len(summaries))

    async def get_task_detail(self, task_id: str) -> Optional[TaskDetail]:
        """Get single task with full detail including dependency info.

        Returns complete task information with both upstream (blocked_by)
        and downstream (blocks) dependency relationships.

        Args:
            task_id: Task identifier.

        Returns:
            TaskDetail if found, None otherwise.
        """
        task = await self.get(task_id=task_id)
        if not task:
            return None

        deps = await self.get_dependencies(task_id)
        blocked_by = [d.depends_on_task_id for d in deps if not d.resolved]

        downstream = await self.db.task.get_tasks_depending_on(task_id)
        blocks = [t.task_id for t in downstream]

        return TaskDetail(
            task_id=task.task_id,
            title=task.title,
            content=task.content,
            status=task.status,
            assignee=task.assignee,
            blocked_by=blocked_by,
            blocks=blocks,
            updated_at=task.updated_at,
        )

    async def get(self, task_id: str) -> Optional[TeamTaskBase]:
        """Get a task by ID

        Args:
            task_id: Task identifier

        Returns:
            TeamTask object if found, None otherwise
        """
        return await self.db.task.get_task(task_id)

    async def assign(self, task_id: str, assignee: str) -> TaskOpResult:
        """Assign a task to a member and mark it as claimed (Leader only).

        Atomically sets the assignee and transitions the task to ``CLAIMED``
        so leader-driven assignment is symmetric with a member self-claim.
        Idempotent when the task is already claimed by ``assignee``. Fails
        when the task is currently held by a different member; the caller
        must reset the task first (see ``reset``) for true reassignment.
        Sends a notification message to the assigned member's mailbox on
        success.

        Args:
            task_id: Task identifier.
            assignee: Member ID to assign.

        Returns:
            ``TaskOpResult.success()`` on assign (or idempotent re-assign);
            ``TaskOpResult.fail(reason)`` with the specific failure cause
            otherwise.
        """
        task = await self.get(task_id)
        if not task:
            return TaskOpResult.fail(f"Task {task_id} not found")

        # Idempotent re-assign: same member, status already claimed → no-op success.
        if task.assignee == assignee and task.status == TaskStatus.CLAIMED.value:
            team_logger.debug(f"Task {task_id} already assigned to {assignee}; no-op")
            return TaskOpResult.success()

        if task.assignee and task.assignee != assignee:
            return TaskOpResult.fail(
                f"Task {task_id} is already claimed by {task.assignee}; reset the task before reassigning to {assignee}"
            )

        success = await self.db.task.claim_task(task_id, assignee)
        if not success:
            return TaskOpResult.fail(
                f"Database rejected assign for task {task_id} (invalid state transition from {task.status})"
            )

        await self.messager.publish(
            topic_id=TeamTopic.TASK.build(get_session_id(), self.team_name),
            message=EventMessage.from_event(
                TaskClaimedEvent(
                    team_name=self.team_name,
                    task_id=task_id,
                    member_name=assignee,
                )
            ),
        )
        team_logger.info(f"Task {task_id} assigned to {assignee}, notification sent")
        return TaskOpResult.success()

    async def add_dependencies(self, task_id: str, depends_on_ids: List[str]) -> TaskOpResult:
        """Add dependencies to an existing task atomically.

        Routes through ``mutate_dependency_graph`` so the operation
        carries the same guarantees as a graph-shaping create:
        - Cycle detection runs against the post-mutation graph; a
          rejected mutation surfaces the cycle path in the failure
          reason.
        - BLOCKED/PENDING is refreshed in the same transaction as the
          edge writes, so the on-disk state is always consistent with
          the dependency graph.

        Args:
            task_id: Task to add dependencies to.
            depends_on_ids: Task IDs that this task should depend on.

        Returns:
            ``TaskOpResult`` carrying the mutation reason on failure.
        """
        if not depends_on_ids:
            return TaskOpResult.success()

        mutation = await self.db.task.mutate_dependency_graph(
            team_name=self.team_name,
            add_edges=[(task_id, dep_id) for dep_id in depends_on_ids],
        )
        if not mutation.ok:
            return TaskOpResult.fail(mutation.reason)
        return TaskOpResult.success()

    async def claim(self, task_id: str) -> TaskOpResult:
        """Claim a task for the current member.

        Args:
            task_id: Task identifier.

        Returns:
            ``TaskOpResult`` carrying the specific failure reason on error
            so the caller (typically a team tool) can pass it through to
            the LLM instead of dropping it to the log sink.
        """
        member_name = self.member_name
        task = await self.get(task_id)
        if not task:
            return TaskOpResult.fail(f"Task {task_id} not found")

        member = await self.db.member.get_member(member_name, self.team_name)
        if not member:
            return TaskOpResult.fail(f"Member {member_name} not found in team {self.team_name}")

        # Idempotent re-claim: if the caller already owns this task, succeed silently.
        if task.assignee == member_name and task.status == TaskStatus.CLAIMED.value:
            team_logger.debug(f"Task {task_id} already claimed by {member_name}; no-op")
            return TaskOpResult.success()

        # Claim conflict must be reported before the state-transition check —
        # otherwise a CLAIMED task held by someone else surfaces as the
        # misleading "invalid claimed → claimed transition" error.
        if task.assignee:
            return TaskOpResult.fail(
                f"Task {task_id} is already claimed by {task.assignee}, {member_name} cannot claim it"
            )

        # Validate state transition (blocks e.g. COMPLETED/CANCELLED/BLOCKED claims).
        if not is_valid_transition(
            TaskStatus(task.status),
            TaskStatus.CLAIMED,
            TASK_TRANSITIONS,
        ):
            return TaskOpResult.fail(
                f"Task {task_id} cannot be claimed from status '{task.status}' (only pending tasks are claimable)"
            )

        # Claim task
        success = await self.db.task.claim_task(task_id, member_name)
        if not success:
            return TaskOpResult.fail(f"Database rejected claim for task {task_id} (likely a concurrent claim race)")

        team_logger.info(f"Task {task_id} claimed by member {member_name}")

        try:
            await self.messager.publish(
                topic_id=TeamTopic.TASK.build(get_session_id(), self.team_name),
                message=EventMessage.from_event(
                    TaskClaimedEvent(
                        team_name=self.team_name,
                        task_id=task_id,
                        member_name=member_name,
                    )
                ),
            )
            team_logger.debug(f"Task claimed event published: {task_id}")
        except Exception as e:
            team_logger.error(f"Failed to publish task claimed event for {task_id}: {e}")

        return TaskOpResult.success()

    async def complete(self, task_id: str) -> TaskOpResult:
        """Complete a task.

        Updates task status to 'completed' and unblocks dependent tasks.
        This operation is atomic to prevent race conditions.

        Args:
            task_id: Task identifier.

        Returns:
            ``TaskOpResult`` describing the outcome.
        """
        # Get member to check mode
        member = await self.db.member.get_member(self.member_name, self.team_name)
        if not member:
            return TaskOpResult.fail(f"Member {self.member_name} not found in team {self.team_name}")

        # Check if member is in PLAN_MODE
        if member.mode == MemberMode.PLAN_MODE.value:
            task = await self.db.task.get_task(task_id)
            if not task:
                return TaskOpResult.fail(f"Task {task_id} not found")

            # PLAN_MODE members can only complete PLAN_APPROVED tasks
            if task.status != TaskStatus.PLAN_APPROVED.value:
                return TaskOpResult.fail(
                    f"PLAN_MODE member cannot complete task {task_id} in status "
                    f"'{task.status}'; only plan_approved tasks can be completed"
                )

        # Complete task atomically - this handles state validation, dependency resolution,
        # and unblocking dependent tasks in a single transaction to prevent race conditions
        result = await self.db.task.complete_task(task_id)
        if not result:
            current = await self.db.task.get_task(task_id)
            if current is None:
                return TaskOpResult.fail(f"Task {task_id} not found")
            return TaskOpResult.fail(
                f"Task {task_id} cannot be completed from status '{current.status}' (must be claimed or plan_approved)"
            )

        # Extract completed task info and unblocked tasks
        completed_task = result.get("task")
        unblocked_tasks = result.get("unblocked_tasks", [])

        team_logger.info(f"Task {task_id} completed")

        await self._publish_task_event(
            TaskCompletedEvent(
                team_name=self.team_name,
                task_id=task_id,
                member_name=completed_task.assignee,
            ),
            error_label=f"Task completed event for {task_id}",
        )
        await self._publish_unblocked_events(unblocked_tasks)
        return TaskOpResult.success()

    async def cancel(self, task_id: str) -> Optional[TeamTaskBase]:
        """Cancel a task and notify any tasks unblocked as a side effect.

        Args:
            task_id: Task identifier.

        Returns:
            The cancelled task, or ``None`` if the task is missing or
            the transition is invalid.
        """
        result = await self.db.task.cancel_task(task_id)
        if result is None:
            return None

        task = result["task"]
        unblocked_tasks = result.get("unblocked_tasks") or []
        team_logger.info(f"Task {task_id} cancelled")

        await self._publish_task_event(
            TaskCancelledEvent(team_name=self.team_name, task_id=task_id),
            error_label=f"Task cancelled event for {task_id}",
        )
        await self._publish_unblocked_events(unblocked_tasks)
        return task

    async def cancel_all_tasks(
        self,
        skip_assignees: Optional[set[str]] = None,
    ) -> List[TeamTaskBase]:
        """Cancel every active task in the team in a single transaction.

        Args:
            skip_assignees: Member names whose claimed tasks must be
                preserved. Any task assigned to one of these members is
                left untouched.

        Returns:
            The cancelled tasks. Side effect: publishes a CANCELLED
            event for each cancelled task and an UNBLOCKED event for
            each task that flipped from BLOCKED to PENDING during the
            cascade.
        """
        result = await self.db.task.cancel_all_tasks(
            self.team_name,
            skip_assignees=skip_assignees,
        )
        cancelled_tasks: List[TeamTaskBase] = result.get("cancelled_tasks") or []
        unblocked_tasks: List[TeamTaskBase] = result.get("unblocked_tasks") or []

        if not cancelled_tasks:
            team_logger.info(f"No tasks to cancel in team {self.team_name}")
            return []

        for task in cancelled_tasks:
            await self._publish_task_event(
                TaskCancelledEvent(team_name=self.team_name, task_id=task.task_id),
                error_label=f"Task cancelled event for {task.task_id}",
            )
        await self._publish_unblocked_events(unblocked_tasks)
        return cancelled_tasks

    async def _publish_task_event(self, event, *, error_label: str) -> None:
        """Publish a task event on the team task topic; log on failure."""
        try:
            await self.messager.publish(
                topic_id=TeamTopic.TASK.build(get_session_id(), self.team_name),
                message=EventMessage.from_event(event),
            )
            team_logger.debug(f"Published: {error_label}")
        except Exception as e:
            team_logger.error(f"Failed to publish {error_label}: {e}")

    async def _publish_unblocked_events(self, unblocked_tasks: List[TeamTaskBase]) -> None:
        """Notify the team about tasks that just transitioned to PENDING."""
        if not unblocked_tasks:
            return
        for task in unblocked_tasks:
            await self._publish_task_event(
                TaskUnblockedEvent(team_name=self.team_name, task_id=task.task_id),
                error_label=f"Task unblocked event for {task.task_id}",
            )
        team_logger.info(f"Unblocked {len(unblocked_tasks)} tasks")

    async def list_tasks(self, status: Optional[str] = None) -> List[TeamTaskBase]:
        """List all tasks for the team

        Args:
            status: Optional status filter

        Returns:
            List of TeamTask objects

        Example:
            # Get all pending tasks
            pending_tasks = task_manager.list_tasks(status="pending")

            # Get all tasks
            all_tasks = task_manager.list_tasks()
        """
        return await self.db.task.get_team_tasks(self.team_name, status)

    async def get_dependencies(self, task_id: str) -> List[TeamTaskDependencyBase]:
        """Get task dependencies

        Args:
            task_id: Task identifier

        Returns:
            List of TeamTaskDependency objects
        """
        return await self.db.task.get_task_dependencies(task_id)

    async def get_claimable_tasks(self) -> List[TeamTaskBase]:
        """Get tasks that can be claimed

        Returns tasks that are in 'pending' status.

        Returns:
            List of claimable Task objects
        """
        return await self.list_tasks(status=TaskStatus.PENDING.value)

    async def get_tasks_by_assignee(self, member_name: str, status: Optional[str] = None) -> List[TeamTaskBase]:
        """Get tasks assigned to a specific member

        Args:
            member_name: Member identifier who is assigned tasks
            status: Optional status filter

        Returns:
            List of Task objects assigned to the member
        """
        return await self.db.task.get_tasks_by_assignee(self.team_name, member_name, status)

    async def update_task(
        self,
        task_id: str,
        title: Optional[str] = None,
        content: Optional[str] = None,
    ) -> TaskOpResult:
        """Update task title / content.

        Publishes ``TASK_UPDATED`` on success. Does not bump the task's
        ``updated_at`` column — that timestamp tracks status transitions
        only.

        Args:
            task_id: Task identifier.
            title: Optional new title.
            content: Optional new content.

        Returns:
            ``TaskOpResult`` describing the outcome.
        """
        task = await self.get(task_id)
        if not task:
            return TaskOpResult.fail(f"Task {task_id} not found")

        success = await self.db.task.update_task(task_id, title=title, content=content)
        if not success:
            return TaskOpResult.fail(
                f"Task {task_id} cannot be edited while in status '{task.status}'; "
                f"content updates are only allowed on pending / blocked tasks"
            )

        team_logger.info(f"Task {task_id} updated")

        try:
            await self.messager.publish(
                topic_id=TeamTopic.TASK.build(get_session_id(), self.team_name),
                message=EventMessage.from_event(TaskUpdatedEvent(team_name=self.team_name, task_id=task_id)),
            )
            team_logger.debug(f"Task updated event published: {task_id}")
        except Exception as e:
            team_logger.error(f"Failed to publish task updated event for {task_id}: {e}")

        return TaskOpResult.success()

    async def reset(self, task_id: str) -> TaskOpResult:
        """Reset a claimed task back to PENDING and clear assignee.

        Useful for re-assigning a task to another member.

        Args:
            task_id: Task identifier.

        Returns:
            ``TaskOpResult`` describing the outcome.
        """
        existing = await self.db.task.get_task(task_id)
        if not existing:
            return TaskOpResult.fail(f"Task {task_id} not found")

        result = await self.db.task.reset_task(task_id)
        if not result:
            return TaskOpResult.fail(
                f"Task {task_id} cannot be reset from status '{existing.status}'; only claimed tasks can be reset"
            )
        team_logger.info(f"Task {task_id} reset successfully")
        return TaskOpResult.success()

    async def approve_plan(self, task_id: str) -> TaskOpResult:
        """Approve a task plan for PLAN_MODE members.

        Transitions a task from CLAIMED to PLAN_APPROVED. Leader only.

        Args:
            task_id: Task identifier.

        Returns:
            ``TaskOpResult`` describing the outcome.
        """
        existing = await self.db.task.get_task(task_id)
        if not existing:
            return TaskOpResult.fail(f"Task {task_id} not found")

        task = await self.db.task.approve_plan_task(task_id)
        if not task:
            return TaskOpResult.fail(
                f"Task {task_id} cannot be plan-approved from status "
                f"'{existing.status}'; only claimed tasks can be approved"
            )
        team_logger.info(f"Task {task_id} approved successfully")
        return TaskOpResult.success()
