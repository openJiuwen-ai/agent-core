# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unit tests for TeamTaskManager module"""

from unittest.mock import AsyncMock

import pytest
import pytest_asyncio

from openjiuwen.agent_teams.messager import Messager
from openjiuwen.agent_teams.tools.context import set_session_id, reset_session_id
from openjiuwen.agent_teams.tools.database import (
    DatabaseConfig,
    DatabaseType,
    TeamDatabase,
)
from openjiuwen.agent_teams.tools.status import TaskStatus, MemberMode
from openjiuwen.agent_teams.tools.task_manager import TeamTaskManager
from openjiuwen.core.single_agent import AgentCard


@pytest.fixture
def db_config():
    """Provide in-memory database config for testing"""
    return DatabaseConfig(db_type=DatabaseType.SQLITE, connection_string=":memory:")


@pytest_asyncio.fixture
async def db(db_config):
    """Provide initialized database instance"""
    token = set_session_id("session_id")
    database = TeamDatabase(db_config)
    try:
        await database.initialize()
        yield database
    finally:
        reset_session_id(token)
        await database.close()


@pytest_asyncio.fixture
async def message_bus():
    """Provide Messager mock instance for testing"""
    bus = AsyncMock(spec=Messager)
    yield bus


@pytest_asyncio.fixture
async def task_manager(db, message_bus):
    """Provide initialized task manager instance"""
    await db.create_team(
        team_id="test_team",
        name="Test Team",
        leader_member_id="leader1"
    )
    await db.create_member(
        member_id="member1",
        team_id="test_team",
        name="member1",
        agent_card=AgentCard().model_dump_json(),
        status="BUSY",
        mode=MemberMode.BUILD_MODE.value
    )
    return TeamTaskManager(db=db, member_id="member1", team_id="test_team", messager=message_bus)


class TestTeamTaskManager:
    """Test team task manager operations"""

    @pytest.mark.asyncio
    async def test_add_task_success(self, task_manager):
        """Test adding a task successfully"""
        task = await task_manager.add(
            title="Test Task",
            content="Test content"
        )
        assert task is not None
        assert task.title == "Test Task"
        assert task.content == "Test content"
        assert task.status == TaskStatus.PENDING.value
        assert task.team_id == "test_team"

    @pytest.mark.asyncio
    async def test_add_task_with_dependencies(self, task_manager):
        """Test adding a task with dependencies"""
        # Create dependency tasks
        task1 = await task_manager.add(title="Task 1", content="Content 1")
        task2 = await task_manager.add(title="Task 2", content="Content 2")

        # Create task that depends on both
        task3 = await task_manager.add(
            title="Task 3",
            content="Content 3",
            dependencies=[task1.task_id, task2.task_id]
        )

        assert task3 is not None
        assert task3.status == TaskStatus.BLOCKED.value

        # Verify dependencies
        deps = await task_manager.get_dependencies(task3.task_id)
        assert len(deps) == 2
        dep_ids = [d.depends_on_task_id for d in deps]
        assert task1.task_id in dep_ids
        assert task2.task_id in dep_ids


class TestAddAsTopPriority:
    """Test add_as_top_priority functionality"""

    @pytest.mark.asyncio
    async def test_add_as_top_priority_blocks_all_pending(self, task_manager):
        """Test that top:priority task blocks all pending tasks"""
        # Create existing pending tasks
        task1 = await task_manager.add(title="Task 1", content="Content 1")
        task2 = await task_manager.add(title="Task 2", content="Content 2")
        task3 = await task_manager.add(title="Task 3", content="Content 3")

        # Add top priority task
        top_task = await task_manager.add_as_top_priority(
            title="Top Priority Task",
            content="Urgent content"
        )

        assert top_task is not None
        assert top_task.status == TaskStatus.PENDING.value

        # Verify all existing tasks are now blocked
        task1_updated = await task_manager.get(task1.task_id)
        task2_updated = await task_manager.get(task2.task_id)
        task3_updated = await task_manager.get(task3.task_id)
        assert task1_updated.status == TaskStatus.BLOCKED.value
        assert task2_updated.status == TaskStatus.BLOCKED.value
        assert task3_updated.status == TaskStatus.BLOCKED.value


class TestTaskCompletionWithDependencyResolution:
    """Test task completion and dependency resolution"""

    @pytest.mark.asyncio
    async def test_complete_task_sets_completed_at(self, task_manager):
        """Test that completing a task sets completed_at"""
        task = await task_manager.add(title="Test Task", content="Content")
        assert await task_manager.claim(task.task_id)
        assert task.completed_at is None

        await task_manager.complete(task.task_id)

        task_updated = await task_manager.get(task.task_id)
        assert task_updated.completed_at is not None
        assert task_updated.status == TaskStatus.COMPLETED.value

    @pytest.mark.asyncio
    async def test_complete_task_unblocks_dependent_tasks(self, task_manager):
        """Test that completing a task unblocks dependent tasks"""
        # Create tasks: task2 depends on task1, task3 depends on task1
        task1 = await task_manager.add(title="Task 1", content="Content 1")
        task2 = await task_manager.add(title="Task 2", content="Content 2", dependencies=[task1.task_id])
        task3 = await task_manager.add(title="Task 3", content="Content 3", dependencies=[task1.task_id])

        # claim task1
        assert await task_manager.claim(task1.task_id)

        # task2 and task3 should be BLOCKED
        task2_before = await task_manager.get(task2.task_id)
        task3_before = await task_manager.get(task3.task_id)
        assert task2.status == TaskStatus.BLOCKED.value
        assert task3_before.status == TaskStatus.BLOCKED.value

        # Complete task1
        await task_manager.complete(task1.task_id)

        # task2 and task3 should now be PENDING (unblocked)
        task2_after = await task_manager.get(task2.task_id)
        task3_after = await task_manager.get(task3.task_id)
        assert task2_after.status == TaskStatus.PENDING.value
        assert task3_after.status == TaskStatus.PENDING.value


class TestAddWithPriority:
    """Test add_with_priority functionality"""

    @pytest.mark.asyncio
    async def test_add_with_priority_basic(self, task_manager):
        """Test adding a task with priority (basic case, no dependencies)"""
        task = await task_manager.add_with_priority(
            title="Priority Task",
            content="Priority content"
        )

        assert task is not None
        assert task.title == "Priority Task"
        assert task.status == TaskStatus.PENDING.value

    @pytest.mark.asyncio
    async def test_add_with_priority_dependencies(self, task_manager):
        """Test adding a task that depends on existing tasks"""
        # Create existing tasks
        task1 = await task_manager.add(title="Task 1", content="Content 1")
        task2 = await task_manager.add(title="Task 2", content="Content 2")

        # Add task that depends on both
        new_task = await task_manager.add_with_priority(
            title="Dependent Task",
            content="Depends on task1 and task2",
            dependencies=[task1.task_id, task2.task_id]
        )

        assert new_task is not None
        assert new_task.status == TaskStatus.BLOCKED.value

        # Verify dependencies
        deps = await task_manager.get_dependencies(new_task.task_id)
        dep_ids = [d.depends_on_task_id for d in deps]
        assert task1.task_id in dep_ids
        assert task2.task_id in dep_ids

    @pytest.mark.asyncio
    async def test_add_with_priority_dependent_tasks(self, task_manager):
        """Test adding a task that existing tasks depend on (high priority)"""
        # Create existing pending tasks
        task1 = await task_manager.add(title="Task 1", content="Content 1")
        task2 = await task_manager.add(title="Task 2", content="Content 2")

        # Add high priority task that task1 and task2 depend on
        priority_task = await task_manager.add_with_priority(
            title="High Priority Task",
            content="Critical task",
            dependent_task_ids=[task1.task_id, task2.task_id]
        )

        assert priority_task is not None
        assert priority_task.status == TaskStatus.PENDING.value

        # Verify existing tasks are now BLOCKED
        task1_updated = await task_manager.get(task1.task_id)
        task2_updated = await task_manager.get(task2.task_id)
        assert task1_updated.status == TaskStatus.BLOCKED.value
        assert task2_updated.status == TaskStatus.BLOCKED.value

        # Verify task1 and task2 now depend on priority_task
        deps1 = await task_manager.get_dependencies(task1.task_id)
        deps2 = await task_manager.get_dependencies(task2.task_id)
        assert priority_task.task_id in [d.depends_on_task_id for d in deps1]
        assert priority_task.task_id in [d.depends_on_task_id for d in deps2]

    @pytest.mark.asyncio
    async def test_add_with_priority_bidirectional(self, task_manager):
        """Test adding a task between other tasks (both dependencies and dependent tasks)"""
        # Create task A (will be completed)
        task_a = await task_manager.add(title="Task A", content="Content A")

        # Create task C (initially pending)
        task_c = await task_manager.add(title="Task C", content="Content C")

        # Insert task B between them: B depends on A, C depends on B
        task_b = await task_manager.add_with_priority(
            title="Task B",
            content="Inserted task",
            dependencies=[task_a.task_id],
            dependent_task_ids=[task_c.task_id]
        )

        assert task_b is not None
        assert task_b.status == TaskStatus.BLOCKED.value  # Depends on task_a

        # Verify task B depends on task A
        deps_b = await task_manager.get_dependencies(task_b.task_id)
        assert task_a.task_id in [d.depends_on_task_id for d in deps_b]

        # Verify task C depends on task B
        deps_c = await task_manager.get_dependencies(task_c.task_id)
        assert task_b.task_id in [d.depends_on_task_id for d in deps_c]

    @pytest.mark.asyncio
    async def test_add_with_priority_custom_task_id(self, task_manager):
        """Test adding a task with custom task ID"""
        custom_id = "custom-task-123"
        task = await task_manager.add_with_priority(
            title="Custom ID Task",
            content="Content",
            task_id=custom_id
        )

        assert task is not None
        assert task.task_id == custom_id

        # Verify we can retrieve it with the custom ID
        retrieved = await task_manager.get(custom_id)
        assert retrieved is not None
        assert retrieved.task_id == custom_id


class TestCancel:
    """Test cancel task functionality"""

    @pytest.mark.asyncio
    async def test_cancel_pending_task(self, task_manager):
        """Test cancelling a pending task"""
        task = await task_manager.add(title="Task 1", content="Content 1")
        assert task.status == TaskStatus.PENDING.value

        result = await task_manager.cancel(task.task_id)
        assert result is not None
        assert result.status == TaskStatus.CANCELLED.value

    @pytest.mark.asyncio
    async def test_cancel_claimed_task(self, task_manager):
        """Test cancelling a claimed task"""
        task = await task_manager.add(title="Task 1", content="Content 1")
        await task_manager.claim(task.task_id)
        assert task.status == TaskStatus.PENDING.value

        result = await task_manager.cancel(task.task_id)
        assert result is not None
        assert result.status == TaskStatus.CANCELLED.value

    @pytest.mark.asyncio
    async def test_cancel_nonexistent_task(self, task_manager):
        """Test cancelling a non-existent task"""
        result = await task_manager.cancel("nonexistent-task-id")
        assert result is None

    @pytest.mark.asyncio
    async def test_cancel_completed_task_fails(self, task_manager):
        """Test that cancelling a completed task fails (invalid state transition)"""
        task = await task_manager.add(title="Task 1", content="Content 1")
        await task_manager.claim(task.task_id)
        await task_manager.complete(task.task_id)

        # Cannot cancel a completed task
        result = await task_manager.cancel(task.task_id)
        assert result is None

        updated_task = await task_manager.get(task.task_id)
        assert updated_task.status == TaskStatus.COMPLETED.value

    @pytest.mark.asyncio
    async def test_cancel_already_cancelled_task_fails(self, task_manager):
        """Test that cancelling an already cancelled task fails (invalid state transition)"""
        task = await task_manager.add(title="Task 1", content="Content 1")
        await task_manager.cancel(task.task_id)

        # Cannot cancel again
        result = await task_manager.cancel(task.task_id)
        assert result is None

        updated_task = await task_manager.get(task.task_id)
        assert updated_task.status == TaskStatus.CANCELLED.value


class TestGetClaimableTasks:
    """Test get_claimable_tasks functionality"""

    @pytest.mark.asyncio
    async def test_get_claimable_tasks_empty(self, task_manager):
        """Test getting claimable tasks when there are none"""
        claimable = await task_manager.get_claimable_tasks()
        assert claimable == []

    @pytest.mark.asyncio
    async def test_get_claimable_tasks_pending(self, task_manager):
        """Test getting claimable tasks includes pending tasks"""
        task1 = await task_manager.add(title="Task 1", content="Content 1")
        task2 = await task_manager.add(title="Task 2", content="Content 2")

        claimable = await task_manager.get_claimable_tasks()
        assert len(claimable) == 2
        claimable_ids = [t.task_id for t in claimable]
        assert task1.task_id in claimable_ids
        assert task2.task_id in claimable_ids

    @pytest.mark.asyncio
    async def test_get_claimable_tasks_excludes_blocked(self, task_manager):
        """Test that blocked tasks are not claimable"""
        task1 = await task_manager.add(title="Task 1", content="Content 1")
        task2 = await task_manager.add(title="Task 2", content="Content 2", dependencies=[task1.task_id])

        claimable = await task_manager.get_claimable_tasks()
        assert len(claimable) == 1
        assert claimable[0].task_id == task1.task_id

    @pytest.mark.asyncio
    async def test_get_claimable_tasks_excludes_claimed(self, task_manager):
        """Test that claimed tasks are not claimable"""
        task1 = await task_manager.add(title="Task 1", content="Content 1")
        task2 = await task_manager.add(title="Task 2", content="Content 2")

        await task_manager.claim(task1.task_id)

        claimable = await task_manager.get_claimable_tasks()
        assert len(claimable) == 1
        assert claimable[0].task_id == task2.task_id

    @pytest.mark.asyncio
    async def test_get_claimable_tasks_excludes_completed(self, task_manager):
        """Test that completed tasks are not claimable"""
        task1 = await task_manager.add(title="Task 1", content="Content 1")
        task2 = await task_manager.add(title="Task 2", content="Content 2")

        await task_manager.claim(task1.task_id)
        await task_manager.complete(task1.task_id)

        claimable = await task_manager.get_claimable_tasks()
        assert len(claimable) == 1
        assert claimable[0].task_id == task2.task_id

    @pytest.mark.asyncio
    async def test_get_claimable_tasks_excludes_cancelled(self, task_manager):
        """Test that cancelled tasks are not claimable"""
        task1 = await task_manager.add(title="Task 1", content="Content 1")
        task2 = await task_manager.add(title="Task 2", content="Content 2")

        await task_manager.cancel(task1.task_id)

        claimable = await task_manager.get_claimable_tasks()
        assert len(claimable) == 1
        assert claimable[0].task_id == task2.task_id


class TestUpdateTask:
    """Test update_task functionality"""

    @pytest.mark.asyncio
    async def test_update_task_title_only(self, task_manager):
        """Test updating task title only"""
        task = await task_manager.add(title="Original Title", content="Content")
        assert task.title == "Original Title"

        success = await task_manager.update_task(task.task_id, title="Updated Title")
        assert success is True

        updated_task = await task_manager.get(task.task_id)
        assert updated_task.title == "Updated Title"
        assert updated_task.content == "Content"

    @pytest.mark.asyncio
    async def test_update_task_content_only(self, task_manager):
        """Test updating task content only"""
        task = await task_manager.add(title="Title", content="Original Content")
        assert task.content == "Original Content"

        success = await task_manager.update_task(task.task_id, content="Updated Content")
        assert success is True

        updated_task = await task_manager.get(task.task_id)
        assert updated_task.title == "Title"
        assert updated_task.content == "Updated Content"

    @pytest.mark.asyncio
    async def test_update_task_both_title_and_content(self, task_manager):
        """Test updating both task title and content"""
        task = await task_manager.add(title="Original Title", content="Original Content")

        success = await task_manager.update_task(
            task.task_id,
            title="Updated Title",
            content="Updated Content"
        )
        assert success is True

        updated_task = await task_manager.get(task.task_id)
        assert updated_task.title == "Updated Title"
        assert updated_task.content == "Updated Content"

    @pytest.mark.asyncio
    async def test_update_task_not_found(self, task_manager):
        """Test updating non-existent task returns False"""
        success = await task_manager.update_task("nonexistent-task", title="New Title")
        assert success is False

    @pytest.mark.asyncio
    async def test_update_task_none_parameters(self, task_manager):
        """Test updating task with None parameters (no change)"""
        task = await task_manager.add(title="Title", content="Content")

        # Update with None values - should still return True
        success = await task_manager.update_task(task.task_id, title=None, content=None)
        assert success is True

        # Task should remain unchanged
        updated_task = await task_manager.get(task.task_id)
        assert updated_task.title == "Title"
        assert updated_task.content == "Content"


class TestAddBatch:
    """Test add_batch functionality"""

    @pytest.mark.asyncio
    async def test_add_batch_success(self, task_manager):
        """Test adding multiple tasks in batch successfully"""
        tasks = [
            {"title": "Task 1", "content": "Content 1"},
            {"title": "Task 2", "content": "Content 2"},
            {"title": "Task 3", "content": "Content 3"}
        ]

        created_tasks = await task_manager.add_batch(tasks)

        assert len(created_tasks) == 3
        assert created_tasks[0].title == "Task 1"
        assert created_tasks[1].title == "Task 2"
        assert created_tasks[2].title == "Task 3"

    @pytest.mark.asyncio
    async def test_add_batch_with_dependencies(self, task_manager):
        """Test adding multiple tasks in batch with dependencies"""
        # Create a dependency task first
        dep_task = await task_manager.add(title="Dependency Task", content="Dep content")

        tasks = [
            {"title": "Task 1", "content": "Content 1"},
            {"title": "Task 2", "content": "Content 2", "dependencies": [dep_task.task_id]},
            {"title": "Task 3", "content": "Content 3"}
        ]

        created_tasks = await task_manager.add_batch(tasks)

        assert len(created_tasks) == 3
        # Task 2 should be blocked due to dependency
        assert created_tasks[1].status == "blocked"
        # Task 1 and 3 should be pending
        assert created_tasks[0].status == "pending"
        assert created_tasks[2].status == "pending"

    @pytest.mark.asyncio
    async def test_add_batch_with_custom_task_ids(self, task_manager):
        """Test adding tasks with custom task IDs"""
        tasks = [
            {"title": "Task 1", "content": "Content 1", "task_id": "custom-task-1"},
            {"title": "Task 2", "content": "Content 2", "task_id": "custom-task-2"}
        ]

        created_tasks = await task_manager.add_batch(tasks)

        assert len(created_tasks) == 2
        assert created_tasks[0].task_id == "custom-task-1"
        assert created_tasks[1].task_id == "custom-task-2"

    @pytest.mark.asyncio
    async def test_add_batch_with_invalid_tasks(self, task_manager):
        """Test adding batch with some invalid tasks (missing required fields)"""
        tasks = [
            {"title": "Valid Task", "content": "Valid content"},
            {"title": "Missing content"},  # invalid - no content
            {"content": "Missing title"},  # invalid - no title
            {"title": "Another Valid Task", "content": "Valid content"}
        ]

        created_tasks = await task_manager.add_batch(tasks)

        # Only 2 valid tasks should be created
        assert len(created_tasks) == 2
        assert created_tasks[0].title == "Valid Task"
        assert created_tasks[1].title == "Another Valid Task"

    @pytest.mark.asyncio
    async def test_add_batch_empty(self, task_manager):
        """Test adding empty batch"""
        created_tasks = await task_manager.add_batch([])

        assert len(created_tasks) == 0

    @pytest.mark.asyncio
    async def test_add_batch_single_task(self, task_manager):
        """Test adding batch with single task"""
        tasks = [{"title": "Single Task", "content": "Single content"}]

        created_tasks = await task_manager.add_batch(tasks)

        assert len(created_tasks) == 1
        assert created_tasks[0].title == "Single Task"


class TestCancelAllTasks:
    """Test cancel_all_tasks functionality"""

    @pytest.mark.asyncio
    async def test_cancel_all_multiple_tasks(self, task_manager):
        """Test cancelling multiple tasks at once"""
        # Create multiple pending tasks
        task1 = await task_manager.add(title="Task 1", content="Content 1")
        task2 = await task_manager.add(title="Task 2", content="Content 2")
        task3 = await task_manager.add(title="Task 3", content="Content 3")

        # Cancel all tasks
        cancelled = await task_manager.cancel_all_tasks()

        assert len(cancelled) == 3

        # Verify all tasks are cancelled
        assert all(t.status == TaskStatus.CANCELLED.value for t in cancelled)

    @pytest.mark.asyncio
    async def test_cancel_all_mixed_status(self, task_manager):
        """Test cancelling tasks with mixed statuses"""
        # Create tasks with different statuses
        task_pending = await task_manager.add(title="Pending", content="Content")
        task_claimed = await task_manager.add(title="Claimed", content="Content")
        await task_manager.claim(task_claimed.task_id)
        task_cancelled = await task_manager.add(title="Cancelled", content="Content")
        await task_manager.cancel(task_cancelled.task_id)
        task_completed = await task_manager.add(title="Completed", content="Content")
        await task_manager.claim(task_completed.task_id)
        await task_manager.complete(task_completed.task_id)

        # Cancel all - should only cancel non-cancelled, non-completed
        cancelled = await task_manager.cancel_all_tasks()

        # Should have cancelled pending, claimed tasks (and skipped cancelled, completed)
        assert len(cancelled) == 2

    @pytest.mark.asyncio
    async def test_cancel_all_no_active_tasks(self, task_manager):
        """Test cancelling when no active tasks"""
        # Only have cancelled and completed tasks
        task_cancelled = await task_manager.add(title="Cancelled", content="Content")
        await task_manager.cancel(task_cancelled.task_id)
        task_completed = await task_manager.add(title="Completed", content="Content")
        await task_manager.claim(task_completed.task_id)
        await task_manager.complete(task_completed.task_id)

        # Cancel all
        cancelled = await task_manager.cancel_all_tasks()

        assert len(cancelled) == 0

    @pytest.mark.asyncio
    async def test_cancel_all_empty_team(self, task_manager):
        """Test cancelling when team has no tasks"""
        cancelled = await task_manager.cancel_all_tasks()
        assert len(cancelled) == 0


class TestResetTask:
    """Test reset functionality"""

    @pytest.mark.asyncio
    async def test_reset_claimed_task(self, task_manager):
        """Test resetting a claimed task back to pending"""
        task = await task_manager.add(title="Test Task", content="Content")
        await task_manager.claim(task.task_id)

        task_claimed = await task_manager.get(task.task_id)
        assert task_claimed.status == TaskStatus.CLAIMED.value
        assert task_claimed.assignee == "member1"

        result = await task_manager.reset(task.task_id)
        assert result is True

        task_reset = await task_manager.get(task.task_id)
        assert task_reset.status == TaskStatus.PENDING.value
        assert task_reset.assignee is None

    @pytest.mark.asyncio
    async def test_reset_nonexistent_task(self, task_manager):
        """Test resetting a non-existent task"""
        result = await task_manager.reset("nonexistent-task-id")
        assert result is False

    @pytest.mark.asyncio
    async def test_reset_pending_task_fails(self, task_manager):
        """Test resetting a pending task fails (invalid state transition)"""
        task = await task_manager.add(title="Test Task", content="Content")
        assert task.status == TaskStatus.PENDING.value

        result = await task_manager.reset(task.task_id)
        assert result is False

        task_after = await task_manager.get(task.task_id)
        assert task_after.status == TaskStatus.PENDING.value

    @pytest.mark.asyncio
    async def test_reset_completed_task_fails(self, task_manager):
        """Test resetting a completed task fails (invalid state transition)"""
        task = await task_manager.add(title="Test Task", content="Content")
        await task_manager.claim(task.task_id)
        await task_manager.complete(task.task_id)

        result = await task_manager.reset(task.task_id)
        assert result is False

        task_after = await task_manager.get(task.task_id)
        assert task_after.status == TaskStatus.COMPLETED.value

    @pytest.mark.asyncio
    async def test_reset_cancelled_task_fails(self, task_manager):
        """Test resetting a cancelled task fails (invalid state transition)"""
        task = await task_manager.add(title="Test Task", content="Content")
        await task_manager.cancel(task.task_id)

        result = await task_manager.reset(task.task_id)
        assert result is False

        task_after = await task_manager.get(task.task_id)
        assert task_after.status == TaskStatus.CANCELLED.value


class TestGetTasksByAssignee:
    """Test get_tasks_by_assignee functionality"""

    @pytest.mark.asyncio
    async def test_get_tasks_by_assignee_empty(self, task_manager):
        """Test getting tasks by assignee when none exist"""
        tasks = await task_manager.get_tasks_by_assignee(member_id="member1")
        assert tasks == []

    @pytest.mark.asyncio
    async def test_get_tasks_by_assignee_with_claimed_tasks(self, task_manager):
        """Test getting tasks assigned to a specific member"""
        task1 = await task_manager.add(title="Task 1", content="Content 1")
        await task_manager.claim(task1.task_id)

        task2 = await task_manager.add(title="Task 2", content="Content 2")
        await task_manager.claim(task2.task_id)

        tasks = await task_manager.get_tasks_by_assignee(member_id="member1")
        assert len(tasks) == 2
        task_ids = [t.task_id for t in tasks]
        assert task1.task_id in task_ids
        assert task2.task_id in task_ids

    @pytest.mark.asyncio
    async def test_get_tasks_by_assignee_with_status_filter(self, task_manager):
        """Test getting tasks by assignee with status filter"""
        task1 = await task_manager.add(title="Task 1", content="Content 1")
        await task_manager.claim(task1.task_id)
        await task_manager.reset(task1.task_id)  # Reset back to pending

        task2 = await task_manager.add(title="Task 2", content="Content 2")
        await task_manager.claim(task2.task_id)

        # Get only claimed tasks
        claimed_tasks = await task_manager.get_tasks_by_assignee(
            member_id="member1",
            status=TaskStatus.CLAIMED.value
        )
        assert len(claimed_tasks) == 1
        assert claimed_tasks[0].task_id == task2.task_id

        await task_manager.complete(task2.task_id)
        # Get only completed tasks
        completed_tasks = await task_manager.get_tasks_by_assignee(
            member_id="member1",
            status=TaskStatus.COMPLETED.value
        )
        assert len(completed_tasks) == 1
        assert completed_tasks[0].task_id == task2.task_id

    @pytest.mark.asyncio
    async def test_get_tasks_by_assignee_different_members(self, task_manager):
        """Test that tasks are correctly filtered by member"""
        task1 = await task_manager.add(title="Task 1", content="Content 1")
        await task_manager.claim(task1.task_id)

        tasks_member1 = await task_manager.get_tasks_by_assignee(member_id="member1")
        assert len(tasks_member1) == 1

        tasks_member2 = await task_manager.get_tasks_by_assignee(member_id="member2")
        assert len(tasks_member2) == 0
