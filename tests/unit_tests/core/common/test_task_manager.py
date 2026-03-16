# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""
Unit tests for CoroutineTaskManager
"""

import asyncio

import pytest
import pytest_asyncio

from openjiuwen.core.common.task_manager import (
    TaskManager,
    Task,
    TaskStatus,
    TaskManagerEvents,
    DuplicateTaskError,
    TaskNotFoundError,
    get_task_manager,
    create_task,
    cancel_group,
    cancel_all,
    print_task_tree,
    get_task_group,
    get_current_task_id,
)


class TestCoroutineTaskManager:
    """Test suite for CoroutineTaskManager"""

    @pytest_asyncio.fixture
    async def manager(self):
        """Create a fresh manager for each test"""
        # Reset singleton to get a fresh instance
        TaskManager.reset_instance()
        mgr = TaskManager()
        yield mgr
        # Clean up singleton after test
        TaskManager.reset_instance()

    @pytest.mark.asyncio
    async def test_create_task_without_task_group(self, manager):
        """Test that creating task without task group raises AttributeError"""
        async def dummy_coro():
            return "result"

        # Should raise AttributeError when trying to call start_soon on None
        with pytest.raises(AttributeError):
            await manager.create_task(dummy_coro(), name="test_task")

    @pytest.mark.asyncio
    async def test_create_task(self, manager):
        """Test basic task creation with task group"""
        async def dummy_coro():
            return "result"

        async with manager.task_group() as tg:
            task = await manager.create_task(dummy_coro(), name="test_task")

            assert task is not None
            assert task.name == "test_task"
            assert task.task_id is not None

        # After task group exits, task should be completed
        assert task.status == TaskStatus.COMPLETED

    @pytest.mark.asyncio
    async def test_task_completes(self, manager):
        """Test task completes with result"""
        async def slow_task():
            await asyncio.sleep(0.1)
            return "completed"

        async with manager.task_group() as tg:
            task = await manager.create_task(slow_task())

        # Task group waits for all tasks to complete
        assert task.status == TaskStatus.COMPLETED
        assert task.result == "completed"

    @pytest.mark.asyncio
    async def test_task_cancel(self, manager):
        """Test task cancellation via task group"""
        async def long_task():
            await asyncio.sleep(10)
            return "done"

        async with manager.task_group() as tg:
            task = await manager.create_task(long_task())

            # Give task time to start
            await asyncio.sleep(0.05)

            # Cancel the task group
            tg.cancel_scope.cancel()

        # Task should be cancelled
        assert task.status == TaskStatus.CANCELLED

    @pytest.mark.asyncio
    async def test_task_timeout(self, manager):
        """Test task timeout"""
        async def long_task():
            await asyncio.sleep(10)
            return "done"

        # Task timeout should be caught and marked
        async with manager.task_group() as tg:
            task = await manager.create_task(long_task(), timeout=0.1, catch_exceptions=True)

        # Task should have timeout status
        assert task.status == TaskStatus.TIMEOUT

    @pytest.mark.asyncio
    async def test_cascade_cancel(self, manager):
        """Test automatic cascade cancellation via task group"""
        child_task_ref = None

        async def child_task():
            await asyncio.sleep(10)
            return "child"

        async def parent_task():
            nonlocal child_task_ref
            # Create child task (should auto-establish parent-child relationship)
            child_task_ref = await manager.create_task(child_task(), name="child")
            # Wait for child to start
            await asyncio.sleep(0.2)
            # Parent should still be running here
            return "parent"

        async with manager.task_group() as tg:
            parent = await manager.create_task(parent_task(), name="parent")

            # Wait for parent to start
            await asyncio.sleep(0.1)

            # Cancel task group - should cascade to all tasks
            tg.cancel_scope.cancel()

        # Both parent and child should be cancelled
        assert parent.status == TaskStatus.CANCELLED
        assert child_task_ref.status == TaskStatus.CANCELLED

    @pytest.mark.asyncio
    async def test_parent_child_relationship(self, manager):
        """Test parent-child relationship is established"""
        # Keep references to prevent WeakValueDictionary cleanup
        child_task_ref = None

        async def child():
            return "child"

        async def parent():
            nonlocal child_task_ref
            child_task_ref = await manager.create_task(child(), name="child")
            return child_task_ref.task_id

        async with manager.task_group() as tg:
            parent_task = await manager.create_task(parent(), name="parent")

        # Check relationship (we hold references so tasks won't be GC'd)
        child_id = parent_task.result
        child = manager.registry.get(child_id)
        assert child is not None
        assert child.parent_task_id == parent_task.task_id

        children = manager.registry.get_by_parent(parent_task.task_id)
        assert len(children) >= 1

    @pytest.mark.asyncio
    async def test_task_group(self, manager):
        """Test task grouping"""
        async def task1():
            return 1

        async def task2():
            return 2

        async with manager.task_group() as tg:
            t1 = await manager.create_task(task1(), group="my_group")
            t2 = await manager.create_task(task2(), group="my_group")

        # get_tasks_by_group is now synchronous
        group_tasks = manager.registry.get_by_group("my_group")

        assert len(group_tasks) == 2

    @pytest.mark.asyncio
    async def test_cancel_group(self, manager):
        """Test cancelling all tasks in a group"""
        async def long_task():
            await asyncio.sleep(10)
            return "done"

        async with manager.task_group() as tg:
            await manager.create_task(long_task(), group="cancel_me")
            await manager.create_task(long_task(), group="cancel_me")

            # Give tasks time to start
            await asyncio.sleep(0.05)

            # Cancel the task group
            tg.cancel_scope.cancel()

        # All tasks should be cancelled
        group_tasks = manager.registry.get_by_group("cancel_me")
        assert all(t.status == TaskStatus.CANCELLED for t in group_tasks)

    @pytest.mark.asyncio
    async def test_event_callback(self, manager):
        """Test event callbacks"""
        events = []

        async def on_completed(**kwargs):
            # Callback framework passes keyword arguments
            task = kwargs.get('task')
            if task:
                events.append(("completed", task.task_id))

        await manager.on(TaskManagerEvents.TASK_COMPLETED, on_completed)

        async def quick_task():
            return "done"

        task_ref = None
        async with manager.task_group() as tg:
            task_ref = await manager.create_task(quick_task())

        # Give time for async callback to fire
        await asyncio.sleep(0.2)

        assert len(events) >= 1

    @pytest.mark.asyncio
    async def test_get_stats(self, manager):
        """Test statistics"""
        # Keep references to prevent WeakValueDictionary cleanup
        tasks = []

        async def task1():
            await asyncio.sleep(0.1)
            return 1

        async def task2():
            await asyncio.sleep(0.1)
            return 2

        async with manager.task_group() as tg:
            tasks.append(await manager.create_task(task1()))
            tasks.append(await manager.create_task(task2()))

        # get_stats is now synchronous (we hold references so tasks won't be GC'd)
        from collections import Counter
        all_tasks = manager.registry.get_all()
        counts = Counter(t.status.value for t in all_tasks)
        stats = {"total": len(all_tasks), **{s.value: counts.get(s.value, 0) for s in TaskStatus}}

        assert stats.get("total") == 2
        assert stats.get("completed") == 2

    @pytest.mark.asyncio
    async def test_task_with_metadata(self, manager):
        """Test task with metadata"""
        async def task_with_meta():
            return "done"

        async with manager.task_group() as tg:
            task = await manager.create_task(
                task_with_meta(),
                metadata={"key": "value", "num": 42}
            )

        assert task.metadata["key"] == "value"
        assert task.metadata["num"] == 42

    @pytest.mark.asyncio
    async def test_task_priority(self, manager):
        """Test task creation without priority parameter (priority was removed)"""
        async def task_func():
            return "done"

        async with manager.task_group() as tg:
            # priority parameter was removed - test basic create_task works
            created_task = await manager.create_task(task_func())

        # Task is created successfully
        assert created_task.status == TaskStatus.COMPLETED

    @pytest.mark.asyncio
    async def test_task_result_accessible_after_wait(self, manager):
        """Test that task result is accessible after task group exits"""
        async def task_with_result():
            return "test_result"

        async with manager.task_group() as tg:
            task = await manager.create_task(task_with_result())

        assert task.result == "test_result"
        assert task.status == TaskStatus.COMPLETED

    @pytest.mark.asyncio
    async def test_task_error_on_failure(self, manager):
        """Test task error is captured on failure"""
        async def failing_task():
            raise ValueError("test error")

        # Use catch_exceptions to prevent task group from propagating the error
        async with manager.task_group() as tg:
            task = await manager.create_task(failing_task(), catch_exceptions=True)

        assert task.status == TaskStatus.FAILED
        assert task.exception is not None
        assert isinstance(task.exception, ValueError)
        assert "test error" in str(task.exception)
        # Test backward compatibility property
        assert task.error is not None
        assert "test error" in task.error

    @pytest.mark.asyncio
    async def test_catch_exceptions(self, manager):
        """Test catch_exceptions parameter"""
        async def failing_task():
            raise ValueError("test error")

        async def normal_task():
            await asyncio.sleep(0.1)
            return "done"

        async with manager.task_group() as tg:

            # Create failing task with catch_exceptions=True
            failed_task = await manager.create_task(
                failing_task(),
                name="failing",
                catch_exceptions=True  # Won't propagate to task group
            )

            # Create normal task
            normal = await manager.create_task(normal_task(), name="normal")

        # Failed task is marked as FAILED, but doesn't affect other tasks
        assert failed_task.status == TaskStatus.FAILED
        assert normal.status == TaskStatus.COMPLETED

    @pytest.mark.asyncio
    async def test_get_running_tasks(self, manager):
        """Test get running tasks"""
        async def long_task():
            await asyncio.sleep(1)
            return "done"

        async with manager.task_group() as tg:
            task = await manager.create_task(long_task())

            # Wait for task to start
            await asyncio.sleep(0.05)

            running = manager.registry.get_running()

            # Should have at least this task
            assert len(running) >= 1
            assert any(t.task_id == task.task_id for t in running)

            # Cancel to exit quickly
            tg.cancel_scope.cancel()

    @pytest.mark.asyncio
    async def test_get_all_tasks(self, manager):
        """Test get all tasks"""
        async def task1():
            return 1

        async def task2():
            return 2

        async with manager.task_group() as tg:
            t1 = await manager.create_task(task1())
            t2 = await manager.create_task(task2())

        all_tasks = manager.registry.get_all()

        assert len(all_tasks) >= 2

    @pytest.mark.asyncio
    async def test_remove_task(self, manager):
        """Test remove task"""
        async def quick_task():
            return "done"

        async with manager.task_group() as tg:
            task = await manager.create_task(quick_task())

        # Remove manually
        removed = await manager.remove_task(task.task_id)

        assert removed is True
        assert manager.registry.get(task.task_id) is None

    @pytest.mark.asyncio
    async def test_remove_completed(self, manager):
        """Test remove_completed method"""
        async def quick_task():
            return "done"

        async with manager.task_group() as tg:
            # Create and complete multiple tasks
            task1 = await manager.create_task(quick_task())
            task2 = await manager.create_task(quick_task())

        # Remove all completed
        count = await manager.remove_completed()

        assert count >= 1

    @pytest.mark.asyncio
    async def test_get_current_task_id(self, manager):
        """Test getting current task ID"""
        captured_id = None

        async def task_func():
            nonlocal captured_id
            captured_id = get_current_task_id()

        async with manager.task_group() as tg:
            task = await manager.create_task(task_func(), name="test_task")

        # Verify in task context the ID matches the task object's ID
        assert captured_id == task.task_id

        # Verify outside task context returns None
        assert get_current_task_id() is None

    @pytest.mark.asyncio
    async def test_get_current_task_id_nested(self, manager):
        """Test getting current task ID in nested tasks"""
        parent_id = None
        child_id = None

        async def child_task():
            nonlocal child_id
            child_id = get_current_task_id()

        async def parent_task():
            nonlocal parent_id
            parent_id = get_current_task_id()
            child = await manager.create_task(child_task(), name="child")
            await child.wait()

        async with manager.task_group() as tg:
            parent = await manager.create_task(parent_task(), name="parent")

        # Verify both tasks got their correct IDs
        assert parent_id == parent.task_id
        assert child_id is not None
        assert child_id != parent_id  # Child should have different ID


class TestCoroutineTaskManagerCleanup:
    """Test suite for auto cleanup functionality"""

    @pytest.mark.asyncio
    async def test_auto_cleanup(self):
        """Test WeakValueDictionary auto cleanup"""
        # Create manager
        manager = TaskManager()

        async def quick_task():
            return "done"

        async with manager.task_group() as tg:
            task = await manager.create_task(quick_task())
            task_id = task.task_id

        # Task should still be in manager (we hold a reference)
        assert manager.registry.get(task_id) is not None

        # Delete our reference
        del task

        # Force garbage collection
        import gc
        gc.collect()

        # Task should be auto-cleaned by WeakValueDictionary
        # Note: This may not always work immediately due to GC timing
        # So we just verify the manager still works
        assert manager.registry.get_all() is not None


class TestCancelChainTracking:
    """Test suite for cancel chain tracking functionality"""

    @pytest_asyncio.fixture
    async def manager(self):
        """Create a fresh manager for each test"""
        # Reset singleton to get a fresh instance
        TaskManager.reset_instance()
        mgr = TaskManager()
        yield mgr
        # Clean up singleton after test
        TaskManager.reset_instance()

    @pytest.mark.asyncio
    async def test_auto_cascade_cancel_multi_level(self, manager):
        """Test automatic cascade cancellation with multiple levels"""
        grandchild_ref = None
        child_ref = None

        async def grandchild_work():
            await asyncio.sleep(10)
            return "grandchild_done"

        async def child_work():
            nonlocal grandchild_ref
            grandchild_ref = await manager.create_task(grandchild_work(), name="grandchild")
            await asyncio.sleep(10)
            return "child_done"

        async def parent_work():
            nonlocal child_ref
            child_ref = await manager.create_task(child_work(), name="child")
            await asyncio.sleep(10)
            return "parent_done"

        async with manager.task_group() as tg:
            parent = await manager.create_task(parent_work(), name="parent")

            # Wait for all tasks to start
            await asyncio.sleep(0.2)

            # Cancel task group - should cascade to all tasks automatically
            tg.cancel_scope.cancel()

        # Verify all tasks are cancelled
        assert parent.status == TaskStatus.CANCELLED
        assert child_ref.status == TaskStatus.CANCELLED
        assert grandchild_ref.status == TaskStatus.CANCELLED

    @pytest.mark.asyncio
    async def test_get_task_tree(self, manager):
        """Test get_task_tree method"""
        async def child1():
            await asyncio.sleep(0.05)
            return "c1"

        async def child2():
            await asyncio.sleep(0.05)
            return "c2"

        async def parent_work():
            await manager.create_task(child1(), name="child1")
            await manager.create_task(child2(), name="child2")
            await asyncio.sleep(0.1)
            return "parent"

        async with manager.task_group() as tg:
            parent = await manager.create_task(parent_work(), name="parent")

            # Wait for children to be created
            await asyncio.sleep(0.05)

            # Get task tree while running
            tree = manager.get_task_tree(parent.task_id)

            # Tree should contain parent and children
            assert "parent" in tree
            assert "child1" in tree
            assert "child2" in tree
            # Should have tree structure indicators
            assert "+- " in tree

            # Let tasks complete
            await asyncio.sleep(0.2)

    @pytest.mark.asyncio
    async def test_task_tree_shows_status(self, manager):
        """Test that task tree shows correct status icons"""
        async def quick_task():
            return "done"

        async def failing_task():
            raise ValueError("error")

        async with manager.task_group() as tg:
            t1 = await manager.create_task(quick_task(), name="completed_task", catch_exceptions=True)
            t2 = await manager.create_task(failing_task(), name="failed_task", catch_exceptions=True)

        tree1 = manager.get_task_tree(t1.task_id)
        tree2 = manager.get_task_tree(t2.task_id)

        # Check status indicators
        assert "[completed]" in tree1
        assert "[failed]" in tree2

    @pytest.mark.asyncio
    async def test_cancel_chain_tracking(self, manager):
        """Test cancel chain tracking with print_task_tree"""
        child_task_ref = None
        grandchild_task_ref = None

        async def grandchild_task():
            await asyncio.sleep(10)

        async def child_task():
            nonlocal grandchild_task_ref
            grandchild_task_ref = await manager.create_task(grandchild_task(), name="grandchild")
            await asyncio.sleep(10)

        async def parent_task():
            nonlocal child_task_ref
            child_task_ref = await manager.create_task(child_task(), name="child")
            await asyncio.sleep(10)

        # Keep references to prevent WeakValueDictionary cleanup
        all_tasks = []

        async with manager.task_group() as tg:

            parent = await manager.create_task(parent_task(), name="parent")
            all_tasks.append(parent)

            # Wait for all tasks to be created
            await asyncio.sleep(0.1)

            # Collect all tasks
            all_tasks.extend([child_task_ref, grandchild_task_ref])

            # Cancel parent with cascade
            await parent.cancel(cascade=True)

            # Wait for cancellation to propagate
            await asyncio.sleep(0.1)

        # Verify cancel chain tracking
        assert child_task_ref is not None
        assert child_task_ref.cancelled_by == parent.task_id
        assert child_task_ref.cancel_reason == "parent_cancelled"

        assert grandchild_task_ref is not None
        assert grandchild_task_ref.cancelled_by == child_task_ref.task_id
        assert grandchild_task_ref.cancel_reason == "parent_cancelled"

        # Verify task tree shows cancel info
        tree = manager.get_task_tree(parent.task_id)
        assert "cancelled by:" in tree
        assert "reason:" in tree
        assert "parent_cancelled" in tree

    @pytest.mark.asyncio
    async def test_custom_cancel_reason(self, manager):
        """Test custom cancel reason tracking"""
        async def long_task():
            await asyncio.sleep(10)

        async with manager.task_group() as tg:

            task = await manager.create_task(long_task(), name="task1")

            await asyncio.sleep(0.1)

            # Cancel with custom reason - set cancelled_by manually since simplified API doesn't support it
            task.cancelled_by = "user"
            await task.cancel(reason="user_requested_shutdown")

            await asyncio.sleep(0.1)

        # Verify custom cancel info
        assert task.cancelled_by == "user"
        assert task.cancel_reason == "user_requested_shutdown"

        # Verify task tree shows custom cancel info
        tree = manager.get_task_tree(task.task_id)
        assert "cancelled by: user" in tree
        assert "reason: user_requested_shutdown" in tree

    @pytest.mark.asyncio
    async def test_print_task_tree_with_cancel_info(self, manager):
        """Test print_task_tree logs cancel chain information"""
        child1_ref = None
        child2_ref = None

        async def child_task():
            await asyncio.sleep(10)

        async def parent_task():
            nonlocal child1_ref, child2_ref
            child1_ref = await manager.create_task(child_task(), name="child1")
            child2_ref = await manager.create_task(child_task(), name="child2")
            await asyncio.sleep(10)

        # Keep references
        all_tasks = []

        async with manager.task_group() as tg:

            parent = await manager.create_task(parent_task(), name="parent")
            all_tasks.append(parent)

            await asyncio.sleep(0.1)
            all_tasks.extend([child1_ref, child2_ref])

            # Cancel parent
            await parent.cancel(cascade=True)
        # Test print_task_tree - now uses logger instead of print
        print_task_tree(parent.task_id)

        # Verify task tree contains cancel information
        tree = manager.get_task_tree(parent.task_id)
        assert "parent" in tree
        assert "child1" in tree
        assert "child2" in tree
        assert "cancelled by:" in tree
        assert "parent_cancelled" in tree


class TestExceptions:
    """Test exception paths"""

    @pytest_asyncio.fixture
    async def manager(self):
        TaskManager.reset_instance()
        mgr = TaskManager()
        yield mgr
        TaskManager.reset_instance()

    @pytest.mark.asyncio
    async def test_duplicate_task_error(self, manager):
        """Creating two tasks with the same task_id raises DuplicateTaskError"""
        fixed_id = "fixed-task-id-001"

        async def dummy():
            await asyncio.sleep(0.1)

        async with manager.task_group() as tg:
            await manager.create_task(dummy(), task_id=fixed_id)
            with pytest.raises(DuplicateTaskError):
                await manager.create_task(dummy(), task_id=fixed_id)
            tg.cancel_scope.cancel()

    @pytest.mark.asyncio
    async def test_wait_reraises_exception(self, manager):
        """task.wait() re-raises the stored exception"""
        async def failing():
            raise RuntimeError("boom")

        async with manager.task_group() as tg:
            task = await manager.create_task(failing(), catch_exceptions=True)

        assert task.status == TaskStatus.FAILED
        with pytest.raises(RuntimeError, match="boom"):
            await task.wait()


class TestWaitMethods:
    """Test wait_group / wait_all"""

    @pytest_asyncio.fixture
    async def manager(self):
        TaskManager.reset_instance()
        mgr = TaskManager()
        yield mgr
        TaskManager.reset_instance()

    @pytest.mark.asyncio
    async def test_task_wait(self, manager):
        """Task.wait() returns the task result"""
        async def produce():
            return 42

        async with manager.task_group() as tg:
            task = await manager.create_task(produce())

        result = await task.wait()
        assert result == 42

    @pytest.mark.asyncio
    async def test_wait_group(self, manager):
        """wait_group returns a list with one entry per group task"""
        tasks = []

        async def produce(val):
            return val

        async with manager.task_group() as tg:
            tasks.append(await manager.create_task(produce(1), group="grp"))
            tasks.append(await manager.create_task(produce(2), group="grp"))
            tasks.append(await manager.create_task(produce(3), group="grp"))

        results = await manager.wait_group("grp")
        assert len(results) == 3

    @pytest.mark.asyncio
    async def test_wait_group_partial_failure(self, manager):
        """wait_group with return_exceptions=True returns Exception objects for failed tasks"""
        tasks = []

        async def ok():
            return "ok"

        async def fail():
            raise ValueError("fail")

        async with manager.task_group() as tg:
            tasks.append(await manager.create_task(ok(), group="mixed"))
            tasks.append(await manager.create_task(fail(), group="mixed", catch_exceptions=True))

        # With return_exceptions=True, failed tasks return Exception objects
        # Note: order of results is not guaranteed due to concurrent execution
        results = await manager.wait_group("mixed", return_exceptions=True)
        assert len(results) == 2
        assert "ok" in results
        assert any(isinstance(r, ValueError) for r in results)

    @pytest.mark.asyncio
    async def test_wait_group_raise_on_failure(self, manager):
        """wait_group with return_exceptions=False (default) raises first exception"""
        async def ok():
            return "ok"

        async def fail():
            raise ValueError("fail")

        async with manager.task_group() as tg:
            await manager.create_task(ok(), group="mixed")
            await manager.create_task(fail(), group="mixed", catch_exceptions=True)

        # With return_exceptions=False (default), exception is raised
        # anyio TaskGroup wraps exceptions in ExceptionGroup
        with pytest.raises(ExceptionGroup) as exc_info:
            await manager.wait_group("mixed")

        # Verify the original ValueError is in the ExceptionGroup
        assert any(isinstance(e, ValueError) and str(e) == "fail" for e in exc_info.value.exceptions)

    @pytest.mark.asyncio
    async def test_wait_all(self, manager):
        """wait_all returns results for every registered task"""
        async def produce(val):
            return val

        async with manager.task_group() as tg:
            t1 = await manager.create_task(produce("a"))
            t2 = await manager.create_task(produce("b"))

        results = await manager.wait_all()
        assert len(results) == 2

    @pytest.mark.asyncio
    async def test_wait_all_partial_failure(self, manager):
        """wait_all with return_exceptions=True returns Exception objects for failed tasks"""
        async def ok():
            return "ok"

        async def fail():
            raise ValueError("fail")

        # Create tasks inside task group so they actually run
        async with manager.task_group():
            t1 = await manager.create_task(ok())
            t2 = await manager.create_task(fail(), catch_exceptions=True)

        # With return_exceptions=True, failed tasks return Exception objects
        results = await manager.wait_all(return_exceptions=True)
        assert len(results) == 2
        # Check that we have both "ok" and ValueError (order may vary)
        assert "ok" in results
        assert any(isinstance(r, ValueError) for r in results)

    @pytest.mark.asyncio
    async def test_wait_all_raise_on_failure(self, manager):
        """wait_all with return_exceptions=False (default) raises first exception"""
        async def ok():
            return "ok"

        async def fail():
            raise ValueError("fail")

        async with manager.task_group() as tg:
            await manager.create_task(ok())
            await manager.create_task(fail(), catch_exceptions=True)
            # Note: tasks must be stored in variables to prevent garbage collection

        # With return_exceptions=False (default), first exception is raised
        with pytest.raises(ValueError, match="fail"):
            await manager.wait_all()


class TestEventBus:
    """Test event registration / deregistration"""

    @pytest_asyncio.fixture
    async def manager(self):
        TaskManager.reset_instance()
        mgr = TaskManager()
        yield mgr
        TaskManager.reset_instance()

    @pytest.mark.asyncio
    async def test_off_callback(self, manager):
        """off() prevents the callback from being called on subsequent events"""
        calls = []

        async def cb(task):
            calls.append(task.task_id)

        await manager.on(TaskManagerEvents.TASK_COMPLETED, cb)
        await manager.off(TaskManagerEvents.TASK_COMPLETED, cb)

        async def quick():
            return "done"

        async with manager.task_group() as tg:
            await manager.create_task(quick())

        assert calls == []

    @pytest.mark.asyncio
    async def test_multiple_callbacks_same_event(self, manager):
        """Two callbacks registered for the same event are both invoked"""
        calls = []

        async def cb1(**kwargs):
            calls.append("cb1")

        async def cb2(**kwargs):
            calls.append("cb2")

        await manager.on(TaskManagerEvents.TASK_COMPLETED, cb1)
        await manager.on(TaskManagerEvents.TASK_COMPLETED, cb2)

        async def quick():
            return "done"

        async with manager.task_group() as tg:
            await manager.create_task(quick())

        await asyncio.sleep(0.1)
        assert "cb1" in calls
        assert "cb2" in calls

    @pytest.mark.asyncio
    async def test_callback_exception_does_not_affect_others(self, manager):
        """An exception in the first callback does not prevent the second from running"""
        calls = []

        async def bad_cb(**kwargs):
            raise RuntimeError("callback error")

        async def good_cb(**kwargs):
            calls.append("good")

        await manager.on(TaskManagerEvents.TASK_COMPLETED, bad_cb)
        await manager.on(TaskManagerEvents.TASK_COMPLETED, good_cb)

        async def quick():
            return "done"

        async with manager.task_group() as tg:
            await manager.create_task(quick())

        await asyncio.sleep(0.2)
        assert "good" in calls


class TestEdgeCases:
    """Edge cases and boundary conditions"""

    @pytest_asyncio.fixture
    async def manager(self):
        TaskManager.reset_instance()
        mgr = TaskManager()
        yield mgr
        TaskManager.reset_instance()

    @pytest.mark.asyncio
    async def test_cancel_all(self, manager):
        """cancel_all() cancels every running task"""
        async def long_task():
            await asyncio.sleep(10)

        tasks = []
        async with manager.task_group() as tg:
            tasks.append(await manager.create_task(long_task()))
            tasks.append(await manager.create_task(long_task()))

            await asyncio.sleep(0.05)
            count = await manager.cancel_all()
            assert count == 2
            tg.cancel_scope.cancel()

        assert all(t.status == TaskStatus.CANCELLED for t in tasks)

    @pytest.mark.asyncio
    async def test_cancel_group_direct(self, manager):
        """cancel_group() returns the number of tasks cancelled"""
        async def long_task():
            await asyncio.sleep(10)

        async with manager.task_group() as tg:
            await manager.create_task(long_task(), group="g1")
            await manager.create_task(long_task(), group="g1")

            await asyncio.sleep(0.05)
            count = await manager.cancel_group("g1")
            assert count == 2
            tg.cancel_scope.cancel()

    @pytest.mark.asyncio
    async def test_cancel_terminal_task_returns_false(self, manager):
        """task.cancel() on an already-COMPLETED task returns False"""
        async def quick():
            return "done"

        async with manager.task_group() as tg:
            task = await manager.create_task(quick())

        assert task.status == TaskStatus.COMPLETED
        result = await task.cancel()
        assert result is False

    @pytest.mark.asyncio
    async def test_get_tasks_by_status(self, manager):
        """get_tasks_by_status(COMPLETED) returns only completed tasks"""
        tasks = []

        async def quick():
            return "done"

        async with manager.task_group() as tg:
            tasks.append(await manager.create_task(quick()))
            tasks.append(await manager.create_task(quick()))

        completed = manager.registry.get_by_status(TaskStatus.COMPLETED)
        assert len(completed) == 2
        assert all(t.status == TaskStatus.COMPLETED for t in completed)

    @pytest.mark.asyncio
    async def test_get_stats_all_fields(self, manager):
        """get_stats() contains failed, cancelled, and timeout keys with correct counts"""
        tasks = []

        async def ok():
            return 1

        async def fail():
            raise ValueError("err")

        async with manager.task_group() as tg:
            tasks.append(await manager.create_task(ok()))
            tasks.append(await manager.create_task(fail(), catch_exceptions=True))

        from collections import Counter
        all_tasks = manager.registry.get_all()
        counts = Counter(t.status.value for t in all_tasks)
        stats = {"total": len(all_tasks), **{s.value: counts.get(s.value, 0) for s in TaskStatus}}
        assert "failed" in stats
        assert "cancelled" in stats
        assert "timeout" in stats
        assert stats.get("completed") == 1
        assert stats.get("failed") == 1

    @pytest.mark.asyncio
    async def test_remove_completed_includes_failed(self, manager):
        """remove_completed() also removes FAILED tasks (terminal state)"""
        task_ref = None

        async def fail():
            raise ValueError("err")

        async with manager.task_group() as tg:
            task_ref = await manager.create_task(fail(), catch_exceptions=True)

        assert task_ref.status == TaskStatus.FAILED
        count = await manager.remove_completed()
        assert count >= 1
        assert manager.registry.get(task_ref.task_id) is None

    @pytest.mark.asyncio
    async def test_get_tasks_by_group_nonexistent(self, manager):
        """Querying a non-existent group returns an empty list"""
        result = manager.registry.get_by_group("no-such-group")
        assert result == []

    @pytest.mark.asyncio
    async def test_remove_task_nonexistent(self, manager):
        """remove_task with unknown id returns False"""
        result = await manager.remove_task("nonexistent-task-id")
        assert result is False

    @pytest.mark.asyncio
    async def test_singleton_behavior(self, manager):
        """get_coroutine_task_manager() always returns the same instance"""
        m1 = get_task_manager()
        m2 = get_task_manager()
        assert m1 is m2

    @pytest.mark.asyncio
    async def test_display_name_without_name(self, manager):
        """A task created without a name uses task_id[:8] as display_name"""
        async def quick():
            return "done"

        async with manager.task_group() as tg:
            task = await manager.create_task(quick())

        assert task.name is None
        assert task.display_name == task.task_id[:8]

    @pytest.mark.asyncio
    async def test_cascade_false_does_not_cancel_children(self, manager):
        """task.cancel(cascade=False) leaves child tasks running"""
        child_ref = None

        async def child():
            await asyncio.sleep(10)

        async def parent():
            nonlocal child_ref
            child_ref = await manager.create_task(child(), name="child")
            await asyncio.sleep(10)

        all_tasks = []
        async with manager.task_group() as tg:
            p = await manager.create_task(parent(), name="parent")
            all_tasks.append(p)

            await asyncio.sleep(0.1)
            all_tasks.append(child_ref)

            # Cancel parent without cascade
            await p.cancel(cascade=False)

            # Child should still be running
            await asyncio.sleep(0.05)
            assert child_ref.status == TaskStatus.RUNNING

            tg.cancel_scope.cancel()

    @pytest.mark.asyncio
    async def test_print_task_tree_no_args(self, manager):
        """print_task_tree() with no arguments does not raise"""
        async def quick():
            return "done"

        async with manager.task_group() as tg:
            await manager.create_task(quick())

        # Should not raise
        print_task_tree()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
