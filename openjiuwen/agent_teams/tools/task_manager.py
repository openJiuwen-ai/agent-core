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
from openjiuwen.agent_teams.schema.status import (
    MemberMode,
    TaskStatus,
)
from openjiuwen.agent_teams.schema.task import TaskDetail, TaskListResult, TaskSummary
from openjiuwen.agent_teams.tools.database import (
    TeamDatabase,
    TeamTaskBase,
    TeamTaskDependencyBase,
)
from openjiuwen.agent_teams.schema.status import (
    is_valid_transition,
    TASK_TRANSITIONS,
    TaskStatus,
)
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
from openjiuwen.agent_teams.spawn.context import get_session_id
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

    async def add_batch(
        self,
        tasks: List[dict]
    ) -> List[TeamTaskBase]:
        """Add multiple tasks to the team in batch

        Creates multiple tasks in a single operation for efficiency.
        Each task in the list is a dictionary with keys:
        - title: Task title (required)
        - content: Task content (required)
        - task_id: Optional custom task ID (auto-generated if not provided)
        - dependencies: Optional list of task IDs this task depends on

        Args:
            tasks: List of task dictionaries

        Returns:
            List of TeamTask objects created (successful tasks only)

        Example:
            tasks = [
                {"title": "Task 1", "content": "First task"},
                {"title": "Task 2", "content": "Second task", "dependencies": ["task1_id"]},
                {"title": "Task 3", "content": "Third task"}
            ]
            created_tasks = task_manager.add_batch(tasks)
        """
        created_tasks = []
        for task_spec in tasks:
            title = task_spec.get("title")
            content = task_spec.get("content")
            task_id = task_spec.get("task_id")
            dependencies = task_spec.get("dependencies")

            if not title or not content:
                team_logger.warning(f"Skipping invalid task: {task_spec}")
                continue

            task = await self.add(
                title=title,
                content=content,
                task_id=task_id,
                dependencies=dependencies
            )
            if task:
                created_tasks.append(task)

        team_logger.info(f"Batch added {len(created_tasks)} tasks")
        return created_tasks

    async def add(
        self,
        title: str,
        content: str,
        task_id: Optional[str] = None,
        dependencies: Optional[List[str]] = None
    ) -> Optional[TeamTaskBase]:
        """Add a task to the team

        Creates a task in the team_task table and optionally adds
        task dependencies to the team_task_dependency table.

        Args:
            title: Task title
            content: Task content
            task_id: Optional custom task ID (auto-generated if not provided)
            dependencies: List of task IDs this task depends on

        Returns:
            TeamTask object if successful, None otherwise

        Example:
            task = task_manager.add(
                title="Analyze data",
                content="Analyze the sales data for Q4",
                dependencies=["task1", "task2"]
            )
        """
        if task_id is None:
            task_id = str(uuid.uuid4())

        # Determine initial status based on dependencies
        if dependencies and len(dependencies) > 0:
            status = TaskStatus.BLOCKED.value
        else:
            status = TaskStatus.PENDING.value

        # Create task
        success = await self.db.create_task(
            task_id=task_id,
            team_name=self.team_name,
            title=title,
            content=content,
            status=status
        )

        if not success:
            team_logger.error(f"Failed to create task {task_id}")
            return None

        # Add dependencies if provided
        if dependencies:
            for dep_task_id in dependencies:
                await self.db.add_task_dependency(
                    task_id=task_id,
                    depends_on_task_id=dep_task_id,
                    team_name=self.team_name
                )
            team_logger.debug(f"Added task {task_id} with dependencies: {dependencies}")

        # Publish task created event
        try:
            await self.messager.publish(
                topic_id=TeamTopic.TASK.build(get_session_id(), self.team_name),
                message=EventMessage.from_event(TaskCreatedEvent(
                    team_name=self.team_name,
                    task_id=task_id,
                    status=status
                )),
            )
            team_logger.debug(f"Task created event published: {task_id}")
        except Exception as e:
            team_logger.error(f"Failed to publish task created event for {task_id}: {e}")

        return TeamTaskBase(
            task_id=task_id,
            team_name=self.team_name,
            title=title,
            content=content,
            status=status,
            assignee=None
        )

    async def add_with_priority(
        self,
        title: str,
        content: str,
        task_id: Optional[str] = None,
        dependencies: Optional[List[str]] = None,
        dependent_task_ids: Optional[List[str]] = None
    ) -> Optional[TeamTaskBase]:
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
            TeamTask object if successful, None otherwise (e.g., circular dependency detected)

        Example:
            # High priority task - existing tasks wait for this task to complete
            task = task_manager.add_with_priority(
                title="Fix critical bug",
                content="Fix critical security issue",
                dependent_task_ids=["task1", "task2"]  # task1, task2 now wait for this task
            )

            # Insert task between other tasks - depends on A, B waits for this task
            task = task_manager.add_with_priority(
                title="Verify fix",
                content="Verify the fix works",
                dependencies=["taskA"],  # This task depends on taskA
                dependent_task_ids=["taskB"]  # taskB waits for this task
            )
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
        success = await self.db.add_task_with_bidirectional_dependencies(
            task_id=task_id,
            team_name=self.team_name,
            title=title,
            content=content,
            status=status,
            dependencies=dependencies,
            dependent_task_ids=dependent_task_ids
        )

        if not success:
            team_logger.error(f"Failed to create prioritized task {task_id} (possibly circular dependency)")
            return None

        # Publish task created event
        try:
            await self.messager.publish(
                topic_id=TeamTopic.TASK.build(get_session_id(), self.team_name),
                message=EventMessage.from_event(TaskCreatedEvent(
                    team_name=self.team_name,
                    task_id=task_id,
                    status=status
                )),
            )
            team_logger.debug(f"Task created event published: {task_id}")
        except Exception as e:
            team_logger.error(f"Failed to publish task created event for {task_id}: {e}")

        return TeamTaskBase(
            task_id=task_id,
            team_name=self.team_name,
            title=title,
            content=content,
            status=status,
            assignee=None
        )

    async def add_as_top_priority(
        self,
        title: str,
        content: str,
        task_id: Optional[str] = None
    ) -> Optional[TeamTaskBase]:
        """Add a task as top priority (blocks all existing pending/blockable tasks)

        This method creates a new task and makes all existing tasks that can be blocked
        (pending or claimed status) depend on it. This ensures the new task is executed
        before those tasks.

        This is useful for inserting urgent tasks that must be processed before
        any other pending work.

        This operation is atomic and prevents circular dependencies.

        Args:
            title: Task title
            content: Task content
            task_id: Optional custom task ID (auto-generated if not provided)

        Returns:
            TeamTask object if successful, None otherwise (e.g., circular dependency detected)

        Example:
            # Insert an urgent task that all pending tasks must wait for
            urgent_task = task_manager.add_as_top_priority(
                title="Fix critical bug",
                content="Fix critical security issue immediately"
            )
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
        success = await self.db.add_task_with_bidirectional_dependencies(
            task_id=task_id,
            team_name=self.team_name,
            title=title,
            content=content,
            status=status,
            dependencies=None,
            dependent_task_ids=dependent_task_ids if dependent_task_ids else None
        )

        if not success:
            team_logger.error(f"Failed to create top priority task {task_id} (possibly circular dependency)")
            return None

        team_logger.info(
            f"Added top priority task {task_id}, blocking {len(dependent_task_ids)} existing tasks"
        )

        # Publish task created event
        try:
            await self.messager.publish(
                topic_id=TeamTopic.TASK.build(get_session_id(), self.team_name),
                message=EventMessage.from_event(TaskCreatedEvent(
                    team_name=self.team_name,
                    task_id=task_id,
                    status=status
                )),
            )
            team_logger.debug(f"Task created event published: {task_id}")
        except Exception as e:
            team_logger.error(f"Failed to publish task created event for {task_id}: {e}")

        return TeamTaskBase(
            task_id=task_id,
            team_name=self.team_name,
            title=title,
            content=content,
            status=status,
            assignee=None
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
            summaries.append(TaskSummary(
                task_id=task.task_id,
                title=task.title,
                status=task.status,
                assignee=task.assignee,
                blocked_by=unresolved,
            ))
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

        downstream = await self.db.get_tasks_depending_on(task_id)
        blocks = [t.task_id for t in downstream]

        return TaskDetail(
            task_id=task.task_id,
            title=task.title,
            content=task.content,
            status=task.status,
            assignee=task.assignee,
            blocked_by=blocked_by,
            blocks=blocks,
            completed_at=task.completed_at,
        )

    async def get(self, task_id: str) -> Optional[TeamTaskBase]:
        """Get a task by ID

        Args:
            task_id: Task identifier

        Returns:
            TeamTask object if found, None otherwise
        """
        return await self.db.get_task(task_id)

    async def assign(self, task_id: str, assignee: str) -> bool:
        """Assign a task to a member (Leader only).

        Sets the assignee without changing task status. Only succeeds when
        the task currently has no assignee. Sends a notification message
        to the assigned member's mailbox.

        Args:
            task_id: Task identifier.
            assignee: Member ID to assign.

        Returns:
            True if assigned, False otherwise.
        """
        task = await self.get(task_id)
        if not task:
            team_logger.error(f"Task {task_id} not found")
            return False

        success = await self.db.assign_task(task_id, assignee)
        if not success:
            return False

        # Notify the assigned member via message
        content = f"Task #{task_id} \"{task.title}\" has been assigned to you."
        await self.messager.publish(
            topic_id=TeamTopic.TASK.build(get_session_id(), self.team_name),
            message=EventMessage.from_event(TaskClaimedEvent(
                team_name=self.team_name,
                task_id=task_id,
                member_name=assignee,
            )),
        )
        team_logger.info(f"Task {task_id} assigned to {assignee}, notification sent")
        return True

    async def add_dependencies(self, task_id: str, depends_on_ids: List[str]) -> bool:
        """Add dependencies to an existing task and refresh its status.

        For each new dependency, adds the dependency edge. Then checks
        whether the task should be blocked (has unresolved deps) or
        unblocked (all deps resolved).

        Args:
            task_id: Task to add dependencies to.
            depends_on_ids: Task IDs that this task should depend on.

        Returns:
            True if all dependencies added, False on error.
        """
        task = await self.get(task_id)
        if not task:
            team_logger.error(f"Task {task_id} not found")
            return False

        for dep_id in depends_on_ids:
            await self.db.add_task_dependency(
                task_id=task_id,
                depends_on_task_id=dep_id,
                team_name=self.team_name,
            )

        # Refresh: if task has unresolved deps, it should be blocked
        unresolved = await self.db.get_unresolved_dependencies_count(task_id)
        if unresolved > 0 and task.status == TaskStatus.PENDING.value:
            await self.db.update_task_status(task_id, TaskStatus.BLOCKED.value)
            team_logger.info(f"Task {task_id} blocked ({unresolved} unresolved deps)")
        elif unresolved == 0 and task.status == TaskStatus.BLOCKED.value:
            await self.db.update_task_status(task_id, TaskStatus.PENDING.value)
            team_logger.info(f"Task {task_id} unblocked (all deps resolved)")

        return True

    async def claim(self, task_id: str) -> bool:
        """Claim a task for the current member.

        Args:
            task_id: Task identifier.
        """
        member_name = self.member_name
        task = await self.get(task_id)
        if not task:
            team_logger.error(f"Task {task_id} not found")
            return False

        member = await self.db.get_member(member_name, self.team_name)
        if not member:
            team_logger.error(f"Member {member_name} not found in team {self.team_name}")
            return False

        # Idempotent re-claim: if the caller already owns this task, succeed silently.
        if task.assignee == member_name and task.status == TaskStatus.CLAIMED.value:
            team_logger.debug(f"Task {task_id} already claimed by {member_name}; no-op")
            return True

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

        # Check if task is already claimed by someone else
        if task.assignee:
            team_logger.warning(f"Task {task_id} is already claimed by {task.assignee}")
            return False

        # Claim task
        success = await self.db.claim_task(task_id, member_name)
        if success:
            team_logger.info(f"Task {task_id} claimed by member {member_name}")

            # Publish task claimed event
            try:
                await self.messager.publish(
                    topic_id=TeamTopic.TASK.build(get_session_id(), self.team_name),
                    message=EventMessage.from_event(TaskClaimedEvent(
                        team_name=self.team_name,
                        task_id=task_id,
                        member_name=member_name
                    )),
                )
                team_logger.debug(f"Task claimed event published: {task_id}")
            except Exception as e:
                team_logger.error(f"Failed to publish task claimed event for {task_id}: {e}")

            return True

        return False

    async def complete(self, task_id: str) -> bool:
        """Complete a task

        Updates task status to 'completed' and unblocks dependent tasks.
        This operation is atomic to prevent race conditions.

        Args:
            task_id: Task identifier

        Returns:
            True if successful, False otherwise

        Example:
            success = task_manager.complete(task_id="task123")
        """
        # Get member to check mode
        member = await self.db.get_member(self.member_name, self.team_name)
        if not member:
            team_logger.error(f"Member {self.member_name} not found in team {self.team_name}")
            return False

        # Check if member is in PLAN_MODE
        if member.mode == MemberMode.PLAN_MODE.value:
            # Get task to check status
            task = await self.db.get_task(task_id)
            if not task:
                team_logger.error(f"Task {task_id} not found")
                return False

            # PLAN_MODE members can only complete PLAN_APPROVED tasks
            if task.status != TaskStatus.PLAN_APPROVED.value:
                team_logger.error(
                    f"PLAN_MODE member cannot complete task {task_id} with status {task.status}, "
                    f"only PLAN_APPROVED tasks can be completed"
                )
                return False

        # Complete task atomically - this handles state validation, dependency resolution,
        # and unblocking dependent tasks in a single transaction to prevent race conditions
        result = await self.db.complete_task(task_id)
        if not result:
            team_logger.error(f"Failed to complete task {task_id}")
            return False

        # Extract completed task info and unblocked tasks
        completed_task = result.get("task")
        unblocked_tasks = result.get("unblocked_tasks", [])

        team_logger.info(f"Task {task_id} completed")

        # Publish task completed event
        try:
            await self.messager.publish(
                topic_id=TeamTopic.TASK.build(get_session_id(), self.team_name),
                message=EventMessage.from_event(TaskCompletedEvent(
                    team_name=self.team_name,
                    task_id=task_id,
                    member_name=completed_task.assignee
                )),
            )
            team_logger.debug(f"Task completed event published: {task_id}")
        except Exception as e:
            team_logger.error(f"Failed to publish task completed event for {task_id}: {e}")

        # Publish task unblocked events for each unblocked task
        for unblocked_task in unblocked_tasks:
            try:
                await self.messager.publish(
                    topic_id=TeamTopic.TASK.build(get_session_id(), self.team_name),
                    message=EventMessage.from_event(TaskUnblockedEvent(
                        team_name=self.team_name,
                        task_id=unblocked_task.task_id
                    )),
                )
                team_logger.debug(f"Task unblocked event published: {unblocked_task.task_id}")
            except Exception as e:
                team_logger.error(f"Failed to publish task unblocked event for {unblocked_task.task_id}: {e}")

        if unblocked_tasks:
            team_logger.info(f"Unblocked {len(unblocked_tasks)} tasks after completing {task_id}")

        return True

    async def cancel(self, task_id: str) -> Optional[TeamTaskBase]:
        """Cancel a task

        Updates the task status to 'cancelled'.

        Args:
            task_id: Task identifier

        Returns:
            TeamTask if successful, None otherwise
        """

        # cancel task
        task = await self.db.cancel_task(task_id)
        if task:
            team_logger.info(f"Task {task_id} cancelled")

            # Publish task cancelled event
            try:
                await self.messager.publish(
                    topic_id=TeamTopic.TASK.build(get_session_id(), self.team_name),
                    message=EventMessage.from_event(TaskCancelledEvent(
                        team_name=self.team_name,
                        task_id=task_id
                    )),
                )
                team_logger.debug(f"Task cancelled event published: {task_id}")
            except Exception as e:
                team_logger.error(f"Failed to publish task cancelled event for {task_id}: {e}")

            return task
        return None

    async def cancel_all_tasks(self) -> List[TeamTaskBase]:
        """Cancel all non-cancelled and non-completed tasks

        Cancels all active tasks (pending, claimed, blocked) in a single atomic transaction.

        Returns:
            List of cancelled TeamTask objects
        """
        # Cancel all tasks atomically
        cancelled_tasks = await self.db.cancel_all_tasks(self.team_name)

        if not cancelled_tasks:
            team_logger.info(f"No tasks to cancel in team {self.team_name}")
            return []

        # Publish task cancelled event for each cancelled task
        for task in cancelled_tasks:
            try:
                await self.messager.publish(
                    topic_id=TeamTopic.TASK.build(get_session_id(), self.team_name),
                    message=EventMessage.from_event(TaskCancelledEvent(
                        team_name=self.team_name,
                        task_id=task.task_id
                    )),
                )
                team_logger.debug(f"Task cancelled event published: {task.task_id}")
            except Exception as e:
                team_logger.error(f"Failed to publish task cancelled event for {task.task_id}: {e}")

        return cancelled_tasks

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
        return await self.db.get_team_tasks(self.team_name, status)

    async def get_dependencies(self, task_id: str) -> List[TeamTaskDependencyBase]:
        """Get task dependencies

        Args:
            task_id: Task identifier

        Returns:
            List of TeamTaskDependency objects
        """
        return await self.db.get_task_dependencies(task_id)

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
        return await self.db.get_tasks_by_assignee(self.team_name, member_name, status)

    async def update_task(
        self,
        task_id: str,
        title: Optional[str] = None,
        content: Optional[str] = None
    ) -> bool:
        """Update task content

        Updates task title and/or content. Publishes TASK_UPDATED event on success.

        Args:
            task_id: Task identifier
            title: Optional new title
            content: Optional new content

        Returns:
            True if successful, False otherwise

        Example:
            success = task_manager.update_task(
                task_id="task123",
                title="Updated title",
                content="Updated content"
            )
        """
        task = await self.get(task_id)
        if not task:
            team_logger.error(f"Task {task_id} not found")
            return False

        # Update task in database
        success = await self.db.update_task(task_id, title=title, content=content)
        if not success:
            return False

        team_logger.info(f"Task {task_id} updated")

        # Publish task updated event
        try:
            await self.messager.publish(
                topic_id=TeamTopic.TASK.build(get_session_id(), self.team_name),
                message=EventMessage.from_event(TaskUpdatedEvent(
                    team_name=self.team_name,
                    task_id=task_id
                )),
            )
            team_logger.debug(f"Task updated event published: {task_id}")
        except Exception as e:
            team_logger.error(f"Failed to publish task updated event for {task_id}: {e}")

        return True

    async def reset(self, task_id: str) -> bool:
        """Reset a task from CLAIMED to PENDING and clear assignee

        This method resets a claimed task back to pending status and clears
        assignee field. Useful for re-assigning task to other members.

        Args:
            task_id: Task identifier

        Returns:
            True if successful, False otherwise

        Example:
            success = task_manager.reset(task_id="task123")
        """
        result = await self.db.reset_task(task_id)
        if result:
            team_logger.info(f"Task {task_id} reset successfully")
            return True
        return False

    async def approve_plan(self, task_id: str) -> bool:
        """Approve a task plan for PLAN_MODE members

        This method transitions a task from CLAIMED to PLAN_APPROVED.
        Only team leader can call this method.

        Args:
            task_id: Task identifier

        Returns:
            True if successful, False otherwise

        Example:
            success = task_manager.approve_plan(task_id="task123")
        """
        task = await self.db.approve_plan_task(task_id)
        if task:
            team_logger.info(f"Task {task_id} approved successfully")
            return True
        return False
