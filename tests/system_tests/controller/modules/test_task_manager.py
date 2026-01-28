# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Unit tests for TaskManager

Tests all core functionality of the TaskManager class including:
- State management
- CRUD operations
- Task hierarchy management
- Status management
- Priority management
- Parallel/concurrent operations
"""


import asyncio
import unittest

from openjiuwen.core.controller import TaskManager, TaskManagerState, TaskFilter, Task, TaskStatus


class TestTaskManager(unittest.IsolatedAsyncioTestCase):
    """Test suite for TaskManager class"""

    def setUp(self):
        """Set up test fixtures before each test method"""
        config = {"default_task_priority": 1}
        self.task_manager = TaskManager(config=config)
        
        self.sample_task = Task(
            session_id="session1",
            task_id="task1",
            task_type="test_task",
            description="Test task",
            priority=1,
            status=TaskStatus.SUBMITTED
        )
        
        self.sample_tasks = [
            Task(
                session_id="session1",
                task_id="task1",
                task_type="test_task",
                description="Task 1",
                priority=1,
                status=TaskStatus.SUBMITTED
            ),
            Task(
                session_id="session1",
                task_id="task2",
                task_type="test_task",
                description="Task 2",
                priority=2,
                status=TaskStatus.WORKING
            ),
            Task(
                session_id="session2",
                task_id="task3",
                task_type="test_task",
                description="Task 3",
                priority=1,
                status=TaskStatus.COMPLETED
            ),
        ]

    # ==================== Add Task Tests ====================
    async def test_add_single_task(self):
        """Test adding a single task"""
        await self.task_manager.add_task(self.sample_task)

        self.assertIn(self.sample_task.task_id, self.task_manager.tasks)
        self.assertEqual(self.task_manager.tasks[self.sample_task.task_id], self.sample_task)

    async def test_add_multiple_tasks(self):
        """Test adding multiple tasks at once"""
        await self.task_manager.add_task(self.sample_tasks)

        self.assertEqual(len(self.task_manager.tasks), 3)
        for task in self.sample_tasks:
            self.assertIn(task.task_id, self.task_manager.tasks)

    async def test_add_task_with_parent(self):
        """Test adding a task with a parent task"""
        parent_task = Task(
            session_id="session1",
            task_id="parent_task",
            task_type="test_task",
            description="Parent task",
            priority=1,
            status=TaskStatus.SUBMITTED
        )

        await self.task_manager.add_task(parent_task)
        self.sample_task.parent_task_id = "parent_task"
        await self.task_manager.add_task(self.sample_task)

    # ==================== Get Task Tests ====================
    async def test_get_task_by_id(self):
        """Test getting a task by ID"""
        await self.task_manager.add_task(self.sample_task)

        result = await self.task_manager.get_task(task_filter=TaskFilter(task_id="task1"))
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].task_id, "task1")

    async def test_get_task_by_id_list(self):
        """Test getting tasks by ID list"""
        await self.task_manager.add_task(self.sample_tasks)

        result = await self.task_manager.get_task(task_filter=TaskFilter(task_id=["task1", "task2"]))
        self.assertEqual(len(result), 2)
        self.assertEqual({t.task_id for t in result}, {"task1", "task2"})

    async def test_get_task_by_session_id(self):
        """Test getting tasks by session ID"""
        await self.task_manager.add_task(self.sample_tasks)

        result = await self.task_manager.get_task(task_filter=TaskFilter(session_id="session1"))
        self.assertEqual(len(result), 2)
        self.assertTrue(all(t.session_id == "session1" for t in result))

    async def test_get_task_by_priority(self):
        """Test getting tasks by priority"""
        await self.task_manager.add_task(self.sample_tasks)

        result = await self.task_manager.get_task(task_filter=TaskFilter(priority=1))
        self.assertEqual(len(result), 2)
        self.assertTrue(all(t.priority == 1 for t in result))

    async def test_get_task_by_status(self):
        """Test getting tasks by status"""
        await self.task_manager.add_task(self.sample_tasks)

        result = await self.task_manager.get_task(task_filter=TaskFilter(status=TaskStatus.SUBMITTED))
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].status, TaskStatus.SUBMITTED)

    async def test_get_task_by_user_id(self):
        """Test getting tasks by user_id in metadata"""
        self.sample_task.metadata = {"user_id": "user1"}
        await self.task_manager.add_task(self.sample_task)

        result = await self.task_manager.get_task(task_filter=TaskFilter(user_id="user1"))
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].task_id, "task1")

    async def test_get_root_tasks(self):
        """Test getting root tasks"""
        await self.task_manager.add_task(self.sample_tasks)

        # Add a child task
        child_task = Task(
            session_id="session1",
            task_id="child_task",
            task_type="test_task",
            description="Child task",
            priority=1,
            status=TaskStatus.SUBMITTED,
            parent_task_id="task1"
        )
        await self.task_manager.add_task(child_task)

        result = await self.task_manager.get_task(task_filter=TaskFilter(is_root=True))
        self.assertEqual(len(result), 3)  # task1, task2, task3 (not child_task)
        self.assertNotIn("child_task", {t.task_id for t in result})

    async def test_get_task_with_children(self):
        """Test getting tasks with children"""
        parent = Task(
            session_id="session1",
            task_id="parent",
            task_type="test_task",
            description="Parent",
            priority=1,
            status=TaskStatus.SUBMITTED
        )
        child1 = Task(
            session_id="session1",
            task_id="child1",
            task_type="test_task",
            description="Child 1",
            priority=1,
            status=TaskStatus.SUBMITTED,
            parent_task_id="parent"
        )
        child2 = Task(
            session_id="session1",
            task_id="child2",
            task_type="test_task",
            description="Child 2",
            priority=1,
            status=TaskStatus.SUBMITTED,
            parent_task_id="parent"
        )

        await self.task_manager.add_task([parent, child1, child2])

        result = await self.task_manager.get_task(task_filter=TaskFilter(task_id="parent", with_children=True))
        self.assertEqual(len(result), 3)  # parent + 2 children
        self.assertEqual({t.task_id for t in result}, {"parent", "child1", "child2"})

    async def test_get_task_with_recursive_children(self):
        """Test getting tasks with recursive children"""
        parent = Task(
            session_id="session1",
            task_id="parent",
            task_type="test_task",
            description="Parent",
            priority=1,
            status=TaskStatus.SUBMITTED
        )
        child = Task(
            session_id="session1",
            task_id="child",
            task_type="test_task",
            description="Child",
            priority=1,
            status=TaskStatus.SUBMITTED,
            parent_task_id="parent"
        )
        grandchild = Task(
            session_id="session1",
            task_id="grandchild",
            task_type="test_task",
            description="Grandchild",
            priority=1,
            status=TaskStatus.SUBMITTED,
            parent_task_id="child"
        )

        await self.task_manager.add_task([parent, child, grandchild])

        result = await self.task_manager.get_task(task_filter=TaskFilter(task_id="parent", with_children=True))
        self.assertEqual(len(result), 3)  # parent + child + grandchild (with_children is recursive)
        self.assertEqual({t.task_id for t in result}, {"parent", "child", "grandchild"})

    async def test_get_all_tasks(self):
        """Test getting all tasks when no filter is provided"""
        await self.task_manager.add_task(self.sample_tasks)

        # When task_filter is None, get_task returns all tasks
        result = await self.task_manager.get_task(task_filter=None)
        self.assertEqual(len(result), 3)

    async def test_get_task_highest_priority_error(self):
        """Test that get_task raises error for 'highest' priority"""
        await self.task_manager.add_task(self.sample_tasks)

        with self.assertRaises(Exception):  # Should raise JiuWenBaseException
            await self.task_manager.get_task(task_filter=TaskFilter(priority="highest"))

    # ==================== Pop Task Tests ====================
    async def test_pop_task_by_id(self):
        """Test popping a task by ID"""
        await self.task_manager.add_task(self.sample_task)

        result = await self.task_manager.pop_task(task_filter=TaskFilter(task_id="task1"))
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].task_id, "task1")
        self.assertNotIn("task1", self.task_manager.tasks)

    async def test_pop_task_highest_priority(self):
        """Test popping task with highest priority"""
        await self.task_manager.add_task(self.sample_tasks)

        result = await self.task_manager.pop_task(task_filter=TaskFilter(priority="highest"))
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].priority, 2)  # Highest priority in sample_tasks

    async def test_pop_task_empty(self):
        """Test popping from empty task manager"""
        result = await self.task_manager.pop_task(task_filter=TaskFilter(priority="highest"))
        self.assertEqual(result, [])

    # ==================== Update Task Tests ====================
    async def test_update_task(self):
        """Test updating a task"""
        await self.task_manager.add_task(self.sample_task)

        self.sample_task.description = "Updated description"
        self.sample_task.status = TaskStatus.WORKING

        success = await self.task_manager.update_task(self.sample_task)
        self.assertTrue(success)
        self.assertEqual(self.task_manager.tasks["task1"].description, "Updated description")
        self.assertEqual(self.task_manager.tasks["task1"].status, TaskStatus.WORKING)

    async def test_update_nonexistent_task(self):
        """Test updating a non-existent task"""
        success = await self.task_manager.update_task(self.sample_task)
        self.assertFalse(success)
        self.assertNotIn("task1", self.task_manager.tasks)

    # ==================== Remove Task Tests ====================
    async def test_remove_task_by_id(self):
        """Test removing a task by ID"""
        await self.task_manager.add_task(self.sample_task)

        await self.task_manager.remove_task(task_filter=TaskFilter(task_id="task1"))
        self.assertNotIn("task1", self.task_manager.tasks)

    async def test_remove_task_with_children(self):
        """Test removing a task with children"""
        parent = Task(
            session_id="session1",
            task_id="parent",
            task_type="test_task",
            description="Parent",
            priority=1,
            status=TaskStatus.SUBMITTED
        )
        child = Task(
            session_id="session1",
            task_id="child",
            task_type="test_task",
            description="Child",
            priority=1,
            status=TaskStatus.SUBMITTED,
            parent_task_id="parent"
        )

        await self.task_manager.add_task([parent, child])
        await self.task_manager.remove_task(task_filter=TaskFilter(task_id="parent", with_children=True))

        self.assertNotIn("parent", self.task_manager.tasks)
        self.assertNotIn("child", self.task_manager.tasks)

    async def test_remove_task_promotes_children_to_root(self):
        """Test that removing a parent promotes children to root"""
        parent = Task(
            session_id="session1",
            task_id="parent",
            task_type="test_task",
            description="Parent",
            priority=1,
            status=TaskStatus.SUBMITTED
        )
        child = Task(
            session_id="session1",
            task_id="child",
            task_type="test_task",
            description="Child",
            priority=1,
            status=TaskStatus.SUBMITTED,
            parent_task_id="parent"
        )

        await self.task_manager.add_task([parent, child])
        await self.task_manager.remove_task(task_filter=TaskFilter(task_id="parent"))

        # Child should be promoted to root
        self.assertIsNone(self.task_manager.tasks["child"].parent_task_id)

    async def test_remove_task_by_session_id(self):
        """Test removing tasks by session ID"""
        await self.task_manager.add_task(self.sample_tasks)

        await self.task_manager.remove_task(task_filter=TaskFilter(session_id="session1"))

        self.assertEqual(len(self.task_manager.tasks), 1)
        self.assertIn("task3", self.task_manager.tasks)  # Only session2 task remains

    async def test_remove_task_by_status(self):
        """Test removing tasks by status"""
        await self.task_manager.add_task(self.sample_tasks)

        await self.task_manager.remove_task(task_filter=TaskFilter(status=TaskStatus.COMPLETED))

        self.assertEqual(len(self.task_manager.tasks), 2)
        self.assertNotIn("task3", self.task_manager.tasks)

    async def test_remove_task_no_filter_error(self):
        """Test that remove_task raises error when no filter criteria provided"""
        with self.assertRaises(Exception):  # Should raise JiuWenBaseException
            await self.task_manager.remove_task(task_filter=TaskFilter(task_id=None, session_id=None, 
                                                                  user_id=None, priority=None, 
                                                                  status=None, is_root=False))

    async def test_remove_task_highest_priority_error(self):
        """Test that remove_task raises error for 'highest' priority"""
        await self.task_manager.add_task(self.sample_tasks)

        with self.assertRaises(Exception):  # Should raise JiuWenBaseException
            await self.task_manager.remove_task(task_filter=TaskFilter(priority="highest"))

    async def test_pop_task_none_filter_error(self):
        """Test that pop_task raises error when task_filter is None"""
        with self.assertRaises(Exception):  # Should raise JiuWenBaseException
            await self.task_manager.pop_task(task_filter=None)

    async def test_remove_task_none_filter_error(self):
        """Test that remove_task raises error when task_filter is None"""
        with self.assertRaises(Exception):  # Should raise JiuWenBaseException
            await self.task_manager.remove_task(task_filter=None)

    # ==================== Get Child Task Tests ====================
    async def test_get_child_task(self):
        """Test getting child tasks"""
        parent = Task(
            session_id="session1",
            task_id="parent",
            task_type="test_task",
            description="Parent",
            priority=1,
            status=TaskStatus.SUBMITTED
        )
        child1 = Task(
            session_id="session1",
            task_id="child1",
            task_type="test_task",
            description="Child 1",
            priority=1,
            status=TaskStatus.SUBMITTED,
            parent_task_id="parent"
        )
        child2 = Task(
            session_id="session1",
            task_id="child2",
            task_type="test_task",
            description="Child 2",
            priority=1,
            status=TaskStatus.SUBMITTED,
            parent_task_id="parent"
        )

        await self.task_manager.add_task([parent, child1, child2])

        result = await self.task_manager.get_child_task("parent")
        self.assertEqual(len(result), 2)
        self.assertEqual({t.task_id for t in result}, {"child1", "child2"})

    async def test_get_child_task_recursive(self):
        """Test getting child tasks recursively"""
        parent = Task(
            session_id="session1",
            task_id="parent",
            task_type="test_task",
            description="Parent",
            priority=1,
            status=TaskStatus.SUBMITTED
        )
        child = Task(
            session_id="session1",
            task_id="child",
            task_type="test_task",
            description="Child",
            priority=1,
            status=TaskStatus.SUBMITTED,
            parent_task_id="parent"
        )
        grandchild = Task(
            session_id="session1",
            task_id="grandchild",
            task_type="test_task",
            description="Grandchild",
            priority=1,
            status=TaskStatus.SUBMITTED,
            parent_task_id="child"
        )

        await self.task_manager.add_task([parent, child, grandchild])

        result = await self.task_manager.get_child_task("parent", is_recursive=True)
        self.assertEqual(len(result), 2)  # child + grandchild
        self.assertEqual({t.task_id for t in result}, {"child", "grandchild"})

    # ==================== Update Task Status Tests ====================
    async def test_update_task_status(self):
        """Test updating task status"""
        await self.task_manager.add_task(self.sample_task)

        await self.task_manager.update_task_status("task1", TaskStatus.WORKING)

        self.assertEqual(self.task_manager.tasks["task1"].status, TaskStatus.WORKING)

    async def test_update_task_status_with_children(self):
        """Test updating task status with children"""
        parent = Task(
            session_id="session1",
            task_id="parent",
            task_type="test_task",
            description="Parent",
            priority=1,
            status=TaskStatus.SUBMITTED
        )
        child = Task(
            session_id="session1",
            task_id="child",
            task_type="test_task",
            description="Child",
            priority=1,
            status=TaskStatus.SUBMITTED,
            parent_task_id="parent"
        )

        await self.task_manager.add_task([parent, child])
        await self.task_manager.update_task_status("parent", TaskStatus.WORKING, with_children=True)

        self.assertEqual(self.task_manager.tasks["parent"].status, TaskStatus.WORKING)
        self.assertEqual(self.task_manager.tasks["child"].status, TaskStatus.WORKING)

    async def test_update_task_status_recursive(self):
        """Test updating task status recursively"""
        parent = Task(
            session_id="session1",
            task_id="parent",
            task_type="test_task",
            description="Parent",
            priority=1,
            status=TaskStatus.SUBMITTED
        )
        child = Task(
            session_id="session1",
            task_id="child",
            task_type="test_task",
            description="Child",
            priority=1,
            status=TaskStatus.SUBMITTED,
            parent_task_id="parent"
        )
        grandchild = Task(
            session_id="session1",
            task_id="grandchild",
            task_type="test_task",
            description="Grandchild",
            priority=1,
            status=TaskStatus.SUBMITTED,
            parent_task_id="child"
        )

        await self.task_manager.add_task([parent, child, grandchild])
        await self.task_manager.update_task_status("parent", TaskStatus.WORKING, with_children=True, is_recursive=True)

        self.assertEqual(self.task_manager.tasks["parent"].status, TaskStatus.WORKING)
        self.assertEqual(self.task_manager.tasks["child"].status, TaskStatus.WORKING)
        self.assertEqual(self.task_manager.tasks["grandchild"].status, TaskStatus.WORKING)

    # ==================== Set Priority Tests ====================
    async def test_set_priority(self):
        """Test setting task priority"""
        await self.task_manager.add_task(self.sample_task)

        await self.task_manager.set_priority("task1", 5)

        self.assertEqual(self.task_manager.tasks["task1"].priority, 5)

    async def test_set_priority_string(self):
        """Test setting priority with string value"""
        await self.task_manager.add_task(self.sample_task)

        await self.task_manager.set_priority("task1", "3")

        self.assertEqual(self.task_manager.tasks["task1"].priority, 3)

    async def test_set_priority_with_children(self):
        """Test setting priority with children"""
        parent = Task(
            session_id="session1",
            task_id="parent",
            task_type="test_task",
            description="Parent",
            priority=1,
            status=TaskStatus.SUBMITTED
        )
        child = Task(
            session_id="session1",
            task_id="child",
            task_type="test_task",
            description="Child",
            priority=1,
            status=TaskStatus.SUBMITTED,
            parent_task_id="parent"
        )

        await self.task_manager.add_task([parent, child])
        await self.task_manager.set_priority("parent", 5, with_children=True)

        self.assertEqual(self.task_manager.tasks["parent"].priority, 5)
        self.assertEqual(self.task_manager.tasks["child"].priority, 5)

    async def test_set_priority_recursive(self):
        """Test setting priority recursively"""
        parent = Task(
            session_id="session1",
            task_id="parent",
            task_type="test_task",
            description="Parent",
            priority=1,
            status=TaskStatus.SUBMITTED
        )
        child = Task(
            session_id="session1",
            task_id="child",
            task_type="test_task",
            description="Child",
            priority=1,
            status=TaskStatus.SUBMITTED,
            parent_task_id="parent"
        )
        grandchild = Task(
            session_id="session1",
            task_id="grandchild",
            task_type="test_task",
            description="Grandchild",
            priority=1,
            status=TaskStatus.SUBMITTED,
            parent_task_id="child"
        )

        await self.task_manager.add_task([parent, child, grandchild])
        await self.task_manager.set_priority("parent", 5, with_children=True, is_recursive=True)

        self.assertEqual(self.task_manager.tasks["parent"].priority, 5)
        self.assertEqual(self.task_manager.tasks["child"].priority, 5)
        self.assertEqual(self.task_manager.tasks["grandchild"].priority, 5)

    # ==================== State Management Tests ====================
    async def test_get_state(self):
        """Test getting task manager state"""
        await self.task_manager.add_task(self.sample_tasks)

        state = await self.task_manager.get_state()

        self.assertIsInstance(state, TaskManagerState)
        self.assertEqual(len(state.tasks), 3)
        self.assertGreater(len(state.priority_index), 0)
        self.assertGreater(len(state.root_tasks), 0)

    async def test_load_state(self):
        """Test loading task manager state"""
        await self.task_manager.add_task(self.sample_tasks)
        state = await self.task_manager.get_state()

        # Create new manager and load state
        new_manager = TaskManager(config={})
        await new_manager.load_state(state)

        self.assertEqual(len(new_manager.tasks), 3)
        self.assertEqual(new_manager.tasks["task1"].task_id, "task1")
        self.assertEqual(new_manager.tasks["task2"].task_id, "task2")
        self.assertEqual(new_manager.tasks["task3"].task_id, "task3")

    async def test_state_persistence(self):
        """Test that state can be saved and restored correctly"""
        # Create hierarchical tasks
        parent = Task(
            session_id="session1",
            task_id="parent",
            task_type="test_task",
            description="Parent",
            priority=1,
            status=TaskStatus.SUBMITTED
        )
        child = Task(
            session_id="session1",
            task_id="child",
            task_type="test_task",
            description="Child",
            priority=2,
            status=TaskStatus.WORKING,
            parent_task_id="parent"
        )

        await self.task_manager.add_task([parent, child])
        state = await self.task_manager.get_state()

        # Create new manager and verify state
        new_manager = TaskManager(config={})
        await new_manager.load_state(state)

        self.assertIn("parent", new_manager.tasks)
        self.assertIn("child", new_manager.tasks)

    # ==================== Parallel/Concurrent Operations Tests ====================
    async def test_parallel_add_tasks(self):
        """Test adding tasks concurrently from multiple coroutines"""
        num_tasks = 50
        tasks = [
            Task(
                session_id=f"session{i % 5}",
                task_id=f"task_{i}",
                task_type="test_task",
                description=f"Task {i}",
                priority=i % 10,
                status=TaskStatus.SUBMITTED
            )
            for i in range(num_tasks)
        ]

        # Add all tasks concurrently
        async def add_task(task):
            await self.task_manager.add_task(task)

        await asyncio.gather(*[add_task(task) for task in tasks])

        # Verify all tasks were added
        all_tasks = await self.task_manager.get_task(task_filter=None)
        self.assertEqual(len(all_tasks), num_tasks)
        self.assertEqual(len(self.task_manager.tasks), num_tasks)

    async def test_parallel_get_and_update(self):
        """Test concurrent get and update operations"""
        # Add initial tasks
        await self.task_manager.add_task(self.sample_tasks)

        # Concurrently get and update tasks
        async def get_and_update(task_id, new_status):
            tasks = await self.task_manager.get_task(task_filter=TaskFilter(task_id=task_id))
            if tasks:
                task = tasks[0]
                task.status = new_status
                await self.task_manager.update_task(task)

        # Run concurrent operations
        await asyncio.gather(
            get_and_update("task1", TaskStatus.WORKING),
            get_and_update("task2", TaskStatus.COMPLETED),
            get_and_update("task3", TaskStatus.FAILED),
        )

        # Verify updates
        task1 = await self.task_manager.get_task(task_filter=TaskFilter(task_id="task1"))
        task2 = await self.task_manager.get_task(task_filter=TaskFilter(task_id="task2"))
        task3 = await self.task_manager.get_task(task_filter=TaskFilter(task_id="task3"))

        self.assertEqual(task1[0].status, TaskStatus.WORKING)
        self.assertEqual(task2[0].status, TaskStatus.COMPLETED)
        self.assertEqual(task3[0].status, TaskStatus.FAILED)

    async def test_parallel_status_updates(self):
        """Test concurrent status updates on same tasks"""
        # Add tasks
        await self.task_manager.add_task(self.sample_tasks)

        # Concurrently update status multiple times
        async def update_status_multiple_times(task_id, iterations=10):
            for _ in range(iterations):
                await self.task_manager.update_task_status(task_id, TaskStatus.WORKING)
                await asyncio.sleep(0.001)  # Small delay to allow interleaving
                await self.task_manager.update_task_status(task_id, TaskStatus.COMPLETED)
                await asyncio.sleep(0.001)

        # Run concurrent updates
        await asyncio.gather(
            update_status_multiple_times("task1"),
            update_status_multiple_times("task2"),
            update_status_multiple_times("task3"),
        )

        # Verify final state (should be COMPLETED after all updates)
        tasks = await self.task_manager.get_task(task_filter=None)
        for task in tasks:
            self.assertEqual(task.status, TaskStatus.COMPLETED)

    async def test_parallel_add_and_remove(self):
        """Test concurrent add and remove operations"""
        num_tasks = 30

        # Concurrently add tasks
        async def add_task(i):
            task = Task(
                session_id="session1",
                task_id=f"task_{i}",
                task_type="test_task",
                description=f"Task {i}",
                priority=i % 5,
                status=TaskStatus.SUBMITTED
            )
            await self.task_manager.add_task(task)

        # Add tasks concurrently
        await asyncio.gather(*[add_task(i) for i in range(num_tasks)])

        # Concurrently remove some tasks
        async def remove_task(task_id):
            await self.task_manager.remove_task(task_filter=TaskFilter(task_id=task_id))

        # Remove half the tasks concurrently
        tasks_to_remove = [f"task_{i}" for i in range(0, num_tasks, 2)]
        await asyncio.gather(*[remove_task(task_id) for task_id in tasks_to_remove])

        # Verify remaining tasks
        remaining_tasks = await self.task_manager.get_task(task_filter=None)
        self.assertEqual(len(remaining_tasks), num_tasks // 2)

    async def test_parallel_priority_updates(self):
        """Test concurrent priority updates"""
        # Add tasks
        await self.task_manager.add_task(self.sample_tasks)

        # Concurrently update priorities
        async def update_priority(task_id, new_priority):
            await self.task_manager.set_priority(task_id, new_priority)

        await asyncio.gather(
            update_priority("task1", 10),
            update_priority("task2", 20),
            update_priority("task3", 30),
        )

        # Verify priorities
        tasks = await self.task_manager.get_task(task_filter=None)
        task_dict = {t.task_id: t.priority for t in tasks}
        self.assertEqual(task_dict["task1"], 10)
        self.assertEqual(task_dict["task2"], 20)
        self.assertEqual(task_dict["task3"], 30)

    async def test_parallel_pop_operations(self):
        """Test concurrent pop operations"""
        # Add many tasks with different priorities
        tasks = [
            Task(
                session_id="session1",
                task_id=f"task_{i}",
                task_type="test_task",
                description=f"Task {i}",
                priority=i % 5,
                status=TaskStatus.SUBMITTED
            )
            for i in range(20)
        ]
        await self.task_manager.add_task(tasks)

        # Concurrently pop tasks
        popped_tasks = []

        async def pop_highest():
            result = await self.task_manager.pop_task(task_filter=TaskFilter(priority="highest"))
            if result:
                popped_tasks.extend(result)

        # Pop multiple times concurrently
        await asyncio.gather(*[pop_highest() for _ in range(5)])

        # Verify that all popped tasks have the highest priority
        if popped_tasks:
            max_priority = max(t.priority for t in popped_tasks)
            self.assertTrue(all(t.priority == max_priority for t in popped_tasks))

    async def test_parallel_get_operations(self):
        """Test concurrent get operations"""
        # Add tasks
        await self.task_manager.add_task(self.sample_tasks)

        # Concurrently query tasks
        async def get_tasks():
            return await self.task_manager.get_task(task_filter=None)

        results = await asyncio.gather(*[get_tasks() for _ in range(10)])

        # All results should be consistent
        for result in results:
            self.assertEqual(len(result), 3)
            task_ids = {t.task_id for t in result}
            self.assertEqual(task_ids, {"task1", "task2", "task3"})

    async def test_parallel_mixed_operations(self):
        """Test mixed concurrent operations (add, get, update, remove)"""
        # Initial tasks
        await self.task_manager.add_task(self.sample_tasks)

        async def mixed_operations(operation_id):
            # Get tasks
            tasks = await self.task_manager.get_task(task_filter=TaskFilter(session_id="session1"))
            # Update a task
            if tasks:
                task = tasks[0]
                task.status = TaskStatus.WORKING
                await self.task_manager.update_task(task)
            # Add a new task
            new_task = Task(
                session_id="session1",
                task_id=f"new_task_{operation_id}",
                task_type="test_task",
                description="New task",
                priority=5,
                status=TaskStatus.SUBMITTED
            )
            await self.task_manager.add_task(new_task)

        # Run mixed operations concurrently
        await asyncio.gather(*[mixed_operations(i) for i in range(5)])

        # Verify final state is consistent
        all_tasks = await self.task_manager.get_task(task_filter=None)
        self.assertGreaterEqual(len(all_tasks), 3)  # At least original 3 tasks

