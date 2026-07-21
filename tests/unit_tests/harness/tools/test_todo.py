# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
import json
import unittest
import uuid
from datetime import datetime, timezone
from unittest.mock import patch, MagicMock, mock_open, AsyncMock, PropertyMock

from openjiuwen.core.common.exception.errors import FrameworkError
from openjiuwen.core.common.logging import tool_logger
from openjiuwen.core.sys_operation import SysOperation
from openjiuwen.core.sys_operation.fs import BaseFsOperation
from openjiuwen.harness.tools.todo import (
    TodoStatus,
    TodoItem,
    TodoTool,
    TodoCreateTool,
    TodoListTool,
    TodoModifyTool,
    STATUS_ICONS
)
from openjiuwen.harness.tools.todo_resume import (
    todo_create_blocked_message,
    todo_create_read_failed_message,
)


def create_mock_file_stream(tell_return=0):
    """Create mock file stream with valid tell() method"""
    mock_file = mock_open()
    mock_file.return_value.tell = MagicMock(return_value=tell_return)
    type(mock_file.return_value).closed = PropertyMock(return_value=False)
    return mock_file


class TestTodoItem(unittest.TestCase):
    """Test core functionality of TodoItem data class"""

    def setUp(self):
        """Initialization before each test case execution"""
        self.test_content = "Test task content"
        self.test_active_form = "Executing test task content"
        self.test_status = TodoStatus.PENDING

    def test_todo_item_create(self):
        """Test TodoItem.create method for task creation"""
        todo = TodoItem.create(content=self.test_content, active_form=self.test_active_form)

        # Verify attributes
        self.assertEqual(todo.content, self.test_content)
        self.assertEqual(todo.activeForm, self.test_active_form)
        self.assertEqual(todo.status, TodoStatus.PENDING)
        self.assertEqual(todo.createdAt, todo.updatedAt)
        # Verify timestamp format complies with ISO 8601
        self.assertRegex(todo.createdAt, r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(.\d+)?([+-]\d{2}:\d{2})?$')

    def test_todo_item_to_dict(self):
        """Test TodoItem conversion to dictionary"""
        todo = TodoItem.create(content=self.test_content, active_form=self.test_active_form)
        todo_dict = todo.to_dict()

        # Verify dictionary structure and values
        self.assertEqual(todo_dict["content"], self.test_content)
        self.assertEqual(todo_dict["activeForm"], self.test_active_form)
        self.assertEqual(todo_dict["status"], TodoStatus.PENDING.value)
        self.assertEqual(todo_dict["id"], todo.id)

    def test_todo_item_from_dict(self):
        """Test creating TodoItem from dictionary"""
        # Construct test dictionary
        test_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()
        todo_dict = {
            "id": test_id,
            "content": self.test_content,
            "activeForm": self.test_active_form,
            "status": TodoStatus.IN_PROGRESS.value,
            "createdAt": now,
            "updatedAt": now
        }

        # Create from dictionary
        todo = TodoItem.from_dict(todo_dict)

        # Verify
        self.assertEqual(todo.id, test_id)
        self.assertEqual(todo.status, TodoStatus.IN_PROGRESS)
        self.assertEqual(todo.createdAt, now)


class TestTodoTool(unittest.IsolatedAsyncioTestCase):
    """Test TodoTool base class functionality"""

    def setUp(self):
        """Initialize test environment"""
        self.test_todos = [
            TodoItem.create(content="Task 1", status=TodoStatus.IN_PROGRESS),
            TodoItem.create(content="Task 2", status=TodoStatus.PENDING)
        ]

        # Mock SysOperation
        self.mock_operation = MagicMock(spec=SysOperation)
        self.mock_fs = MagicMock(spec=BaseFsOperation)
        self.mock_operation.fs.return_value = self.mock_fs
        self.mock_fs.read_file = AsyncMock()
        self.mock_fs.write_file = AsyncMock()

        # Mock logger
        self.logger_mocks = {
            "info": patch.object(tool_logger, 'info', MagicMock()).start(),
            "error": patch.object(tool_logger, 'error', MagicMock()).start(),
            "warning": patch.object(tool_logger, 'warning', MagicMock()).start()
        }

    def tearDown(self):
        """Clean up mocks"""
        for mock in self.logger_mocks.values():
            mock.stop()

    async def test_load_todos_success(self):
        """Test successful loading of todo list"""
        # Mock read file return value
        mock_read_result = MagicMock(
            code=0,
            data=MagicMock(content=json.dumps([todo.to_dict() for todo in self.test_todos]))
        )
        self.mock_fs.read_file.return_value = mock_read_result

        # Create TodoTool instance
        tool = TodoTool(MagicMock(), self.mock_operation)
        self.test_file_path = tool.file_path_for_session("test-session")

        # Execute and verify
        with patch('os.path.abspath', return_value='/mock/absolute/path'), \
             patch('os.path.isfile', return_value=True):
            loaded_todos = await tool.load_todos(self.test_file_path)
        self.assertEqual(len(loaded_todos), 2)
        self.assertEqual(loaded_todos[0].content, "Task 1")
        self.assertEqual(loaded_todos[1].status, TodoStatus.PENDING)

    async def test_load_todos_read_fail(self):
        """Test file read failure scenario"""
        # Mock read file return error code
        self.mock_fs.read_file.return_value = MagicMock(code=1, data=None)

        # Create TodoTool instance
        tool = TodoTool(MagicMock(), self.mock_operation)
        test_file_path = tool.file_path_for_session("test-session")

        # Verify exception
        with self.assertRaises(FrameworkError) as cm:
            await tool.load_todos(test_file_path)
        self.assertIn("Failed to load todo list: [182500] todo tool loads failed", str(cm.exception))

    async def test_save_todos_success(self):
        """Test successful saving of todo list"""
        # Mock write file return success
        self.mock_fs.write_file.return_value = MagicMock(code=0)

        # Create TodoTool instance
        tool = TodoTool(MagicMock(), self.mock_operation)
        test_file_path = tool.file_path_for_session("test-session")

        # Execute save
        await tool.save_todos(self.test_todos, test_file_path)

        # Verify call parameters
        self.mock_fs.write_file.assert_called_once()
        call_args = self.mock_fs.write_file.call_args
        self.assertIsNotNone(call_args)
        self.assertIn("Task 1", call_args[0][1])
        self.assertEqual(call_args[1].get("mode"), "text")

    async def test_save_todos_write_fail(self):
        """Test file write failure scenario"""
        # Mock write file return error code
        self.mock_fs.write_file.return_value = MagicMock(code=1)

        # Create TodoTool instance
        tool = TodoTool(MagicMock(), self.mock_operation)
        test_file_path = tool.file_path_for_session("test-session")

        # Verify exception
        with self.assertRaises(FrameworkError) as cm:
            await tool.save_todos(self.test_todos, test_file_path)
        self.assertIn("Failed to save todo list, because write_file fail", str(cm.exception))


class TestTodoCreateTool(unittest.IsolatedAsyncioTestCase):
    """Test TodoCreateTool functionality"""

    def setUp(self):
        """Initialize test environment"""

        # Mock SysOperation
        self.mock_operation = MagicMock(spec=SysOperation)
        self.mock_fs = MagicMock(spec=BaseFsOperation)
        self.mock_operation.fs.return_value = self.mock_fs

        # Create tool with mock operation
        self.tool = TodoCreateTool(operation=self.mock_operation)
        self.mock_session = MagicMock()
        self.mock_session.get_session_id.return_value = "test-session"

        # Mock persistence methods
        self.tool.load_todos = AsyncMock(return_value=[])
        self.tool.save_todos = AsyncMock()

        # Mock logger
        self.logger_mocks = {
            "info": patch.object(tool_logger, 'info', MagicMock()).start(),
            "error": patch.object(tool_logger, 'error', MagicMock()).start(),
            "warning": patch.object(tool_logger, 'warning', MagicMock()).start()
        }

    def tearDown(self):
        """Clean up mocks"""
        patch.stopall()

    async def test_invoke_create_simplified(self):
        """Test creating Todo items from mixed separators string"""
        # Construct input
        inputs = {
            "tasks": "Create Task 1；Create Task 2；Create Task 3"
        }

        # Execute call
        result = await self.tool.invoke(inputs, session=self.mock_session)

        # Verify
        self.assertIn("Successfully created 3 task(s)", result["message"])
        self.assertIn("task_id:", result["message"])
        self.assertIn("Next step: Immediately execute task 'Create Task 1'", result["message"])

        # Verify saved task status: first is IN_PROGRESS, rest are PENDING
        saved_todos = self.tool.save_todos.call_args[0][0]
        self.assertEqual(len(saved_todos), 3)
        self.assertEqual(saved_todos[0].status, TodoStatus.IN_PROGRESS)
        self.assertEqual(saved_todos[1].status, TodoStatus.PENDING)
        self.assertEqual(saved_todos[2].status, TodoStatus.PENDING)

    async def test_invoke_create_not_split_by_enumeration_comma(self):
        """Do not split a single task sentence by Chinese enumeration comma."""
        inputs = {
            "tasks": (
                "需求分析：明确项目目标、用户需求及功能边界；"
                "技术选型：评估并选择合适的技术栈和开发工具；"
                "实施方案：制定开发计划、分配任务并启动执行"
            )
        }

        result = await self.tool.invoke(inputs, session=self.mock_session)

        self.assertIn("Successfully created 3 task(s)", result["message"])
        saved_todos = self.tool.save_todos.call_args[0][0]
        self.assertEqual(len(saved_todos), 3)
        self.assertIn("目标、用户需求及功能边界", saved_todos[0].content)
        self.assertIn("开发计划、分配任务并启动执行", saved_todos[2].content)

    async def test_invoke_create_empty_tasks(self):
        """Test error handling for empty task string"""
        # Construct empty input
        inputs = {"tasks": "；\n  "}

        # Execute and verify exception
        with self.assertRaises(Exception) as cm:
            await self.tool.invoke(inputs, session=self.mock_session)
        self.assertIn("Task content cannot be empty", str(cm.exception))

    async def test_invoke_missing_tasks_param(self):
        """Test error handling for missing 'tasks' parameter"""
        # Construct input without required parameter
        inputs = {}

        # Execute and verify exception
        with self.assertRaises(Exception) as cm:
            await self.tool.invoke(inputs, session=self.mock_session)
        self.assertIn("Invalid task data", str(cm.exception))

    async def test_invoke_create_blocked_when_active_without_force(self):
        """Reject todo_create when active items exist and force is false."""
        active_todos = [
            TodoItem.create(content="existing task", status=TodoStatus.IN_PROGRESS),
        ]
        self.tool.load_todos = AsyncMock(return_value=active_todos)

        with patch("openjiuwen.harness.tools.todo.os.path.isfile", return_value=True):
            with self.assertRaises(Exception) as cm:
                await self.tool.invoke(
                    {"tasks": "New task"},
                    session=self.mock_session,
                )

        self.assertIn(todo_create_blocked_message("cn"), str(cm.exception))
        self.tool.save_todos.assert_not_awaited()

    async def test_invoke_create_overwrites_when_force_true(self):
        """Allow todo_create to overwrite when force=true and active items exist."""
        active_todos = [
            TodoItem.create(content="existing task", status=TodoStatus.IN_PROGRESS),
        ]
        self.tool.load_todos = AsyncMock(return_value=active_todos)

        with patch("openjiuwen.harness.tools.todo.os.path.isfile", return_value=True):
            result = await self.tool.invoke(
                {"tasks": "Replacement task", "force": True},
                session=self.mock_session,
            )

        self.assertIn("Successfully created 1 task(s)", result["message"])
        self.tool.save_todos.assert_awaited_once()

    async def test_invoke_create_force_true_tolerates_corrupted_file(self):
        """force=true should recover when existing todo file cannot be read."""
        self.tool.load_todos = AsyncMock(side_effect=ValueError("corrupted json"))

        with patch("openjiuwen.harness.tools.todo.os.path.isfile", return_value=True):
            result = await self.tool.invoke(
                {"tasks": "Fresh task", "force": True},
                session=self.mock_session,
            )

        self.assertIn("Successfully created 1 task(s)", result["message"])
        self.tool.save_todos.assert_awaited_once()

    async def test_invoke_create_raises_when_corrupted_file_without_force(self):
        """force=false should reject create when existing todo file cannot be read."""
        self.tool.load_todos = AsyncMock(side_effect=ValueError("corrupted json"))

        with patch("openjiuwen.harness.tools.todo.os.path.isfile", return_value=True):
            with self.assertRaises(Exception) as cm:
                await self.tool.invoke(
                    {"tasks": "Fresh task"},
                    session=self.mock_session,
                )

        self.assertIn(todo_create_read_failed_message("cn"), str(cm.exception))
        self.tool.save_todos.assert_not_awaited()


class TestTodoListTool(unittest.IsolatedAsyncioTestCase):
    """Test TodoListTool functionality"""

    def setUp(self):
        """Initialize test environment"""

        # Mock SysOperation
        self.mock_operation = MagicMock(spec=SysOperation)
        self.mock_fs = MagicMock(spec=BaseFsOperation)
        self.mock_operation.fs.return_value = self.mock_fs

        # Create tool with mock operation
        self.tool = TodoListTool(operation=self.mock_operation)
        self.mock_session = MagicMock()
        self.mock_session.get_session_id.return_value = "test-session"

        # Construct test data
        self.test_todos = [
            TodoItem.create(content="In Progress Task", status=TodoStatus.IN_PROGRESS),
            TodoItem.create(content="Pending Task", status=TodoStatus.PENDING),
            TodoItem.create(content="Completed Task", status=TodoStatus.COMPLETED),
            TodoItem.create(content="Cancelled Task", status=TodoStatus.CANCELLED)
        ]

        # Mock persistence methods
        self.tool.load_todos = AsyncMock(return_value=self.test_todos)

        # Mock logger
        self.logger_mocks = {
            "info": patch.object(tool_logger, 'info', MagicMock()).start(),
            "error": patch.object(tool_logger, 'error', MagicMock()).start(),
            "warning": patch.object(tool_logger, 'warning', MagicMock()).start()
        }

    def tearDown(self):
        """Clean up mocks"""
        patch.stopall()

    async def test_invoke_list_success(self):
        """Test successful listing of todos with grouping"""
        # Execute call
        result = await self.tool.invoke({}, session=self.mock_session)

        # Verify result
        self.assertIn("Todo List (Total: 4 items)", result["message"])
        self.assertIn(f"{STATUS_ICONS[TodoStatus.IN_PROGRESS]} In Progress Task", result["message"])
        self.assertIn(f"{STATUS_ICONS[TodoStatus.PENDING]} Pending Tasks", result["message"])
        self.assertIn(f"{STATUS_ICONS[TodoStatus.COMPLETED]} Completed Tasks", result["message"])
        self.assertIn(f"{STATUS_ICONS[TodoStatus.CANCELLED]} Cancelled Tasks", result["message"])


class TestTodoModifyTool(unittest.IsolatedAsyncioTestCase):
    """Test TodoModifyTool functionality"""

    def setUp(self):
        """Initialize test environment"""

        # Mock SysOperation
        self.mock_operation = MagicMock(spec=SysOperation)
        self.mock_fs = MagicMock(spec=BaseFsOperation)
        self.mock_operation.fs.return_value = self.mock_fs

        # Create tool with mock operation
        self.tool = TodoModifyTool(operation=self.mock_operation)
        self.mock_session = MagicMock()
        self.mock_session.get_session_id.return_value = "test-session"

        # Construct test data
        self.test_todos = [
            TodoItem.create(content="Task 1", status=TodoStatus.IN_PROGRESS, active_form="Executing Task 1"),
            TodoItem.create(content="Task 2", status=TodoStatus.PENDING, active_form="Executing Task 2"),
            TodoItem.create(content="Task 3", status=TodoStatus.PENDING, active_form="Executing Task 3")
        ]
        self.test_todo_ids = [todo.id for todo in self.test_todos]

        # Mock persistence methods
        self.tool.load_todos = AsyncMock(return_value=self.test_todos.copy())
        self.tool.save_todos = AsyncMock()

        # Mock logger
        self.logger_mocks = {
            "info": patch.object(tool_logger, 'info', MagicMock()).start(),
            "error": patch.object(tool_logger, 'error', MagicMock()).start(),
            "warning": patch.object(tool_logger, 'warning', MagicMock()).start()
        }

    def tearDown(self):
        """Clean up mocks"""
        patch.stopall()

    async def test_invoke_delete_success(self):
        """Test successful deletion of todo items"""
        # Construct input
        inputs = {
            "action": "delete",
            "ids": [self.test_todo_ids[1]]  # Delete Task 2
        }

        # Execute call
        result = await self.tool.invoke(inputs, session=self.mock_session)

        # Verify
        self.assertIn(f"Successfully deleted 1 task(s) (IDs: {self.test_todo_ids[1]})", result["message"])
        # Verify Task 2 removed from saved list
        saved_todos = self.tool.save_todos.call_args[0][0]
        self.assertEqual(len(saved_todos), 2)
        self.assertEqual(saved_todos[0].content, "Task 1")
        self.assertEqual(saved_todos[1].content, "Task 3")

    async def test_invoke_delete_nonexistent(self):
        """Test deletion of nonexistent todo items"""
        # Construct input
        inputs = {
            "action": "delete",
            "ids": ["nonexistent_id"]
        }

        # Execute call
        result = await self.tool.invoke(inputs, session=self.mock_session)

        # Verify
        self.assertIn("No tasks deleted: None of the provided IDs (nonexistent_id) were found", result["message"])

    async def test_invoke_update_success(self):
        """Test successful update of todo items"""
        # Construct update data
        update_data = [{
            "id": self.test_todo_ids[0],
            "content": "Updated Task 1",
            "activeForm": "Executing Updated Task 1",
            "status": TodoStatus.COMPLETED.value
        }]
        inputs = {
            "action": "update",
            "todos": update_data
        }

        # Execute call
        result = await self.tool.invoke(inputs, session=self.mock_session)

        # Verify
        self.assertIn("Successfully updated 1 task(s)", result["message"])
        # Verify updated data
        saved_todos = self.tool.save_todos.call_args[0][0]
        updated_todo = next(t for t in saved_todos if t.id == self.test_todo_ids[0])
        self.assertEqual(updated_todo.content, "Updated Task 1")
        self.assertEqual(updated_todo.status, TodoStatus.COMPLETED)

    async def test_invoke_update_partial_fields_success(self):
        """Test partial update (id + status only) keeps existing fields."""
        inputs = {
            "action": "update",
            "todos": [{
                "id": self.test_todo_ids[0],
                "status": TodoStatus.COMPLETED.value,
            }],
        }

        result = await self.tool.invoke(inputs, session=self.mock_session)

        self.assertIn("Successfully updated 1 task(s)", result["message"])
        saved_todos = self.tool.save_todos.call_args[0][0]
        updated_todo = next(
            t for t in saved_todos if t.id == self.test_todo_ids[0]
        )
        self.assertEqual(updated_todo.status, TodoStatus.COMPLETED)
        self.assertEqual(updated_todo.content, "Task 1")
        self.assertEqual(updated_todo.activeForm, "Executing Task 1")

    async def test_invoke_append_success(self):
        """Test successful appending of new todo items"""
        # Construct append data
        new_todo_id = str(uuid.uuid4())
        append_data = [{
            "id": new_todo_id,
            "content": "New Task 4",
            "activeForm": "Executing New Task 4",
            "status": TodoStatus.PENDING.value
        }]
        inputs = {
            "action": "append",
            "todos": append_data
        }

        # Execute call
        result = await self.tool.invoke(inputs, session=self.mock_session)

        # Verify
        self.assertIn("Successfully append 1 task(s)", result["message"])
        saved_todos = self.tool.save_todos.call_args[0][0]
        self.assertEqual(len(saved_todos), 4)
        self.assertEqual(saved_todos[-1].id, new_todo_id)

    async def test_invoke_append_reports_duplicate_id_in_current_todo_list(self):
        """Report when an appended task duplicates an existing task ID."""
        append_data = [{
            "id": self.test_todo_ids[0],
            "content": "Duplicate Task",
            "activeForm": "Executing Duplicate Task",
            "status": TodoStatus.PENDING.value,
        }]

        with self.assertRaises(FrameworkError) as cm:
            await self.tool.invoke(
                {"action": "append", "todos": append_data},
                session=self.mock_session,
            )

        self.assertIn(
            f"Task with ID '{self.test_todo_ids[0]}' is duplicated in current todo list",
            str(cm.exception),
        )
        self.tool.save_todos.assert_not_awaited()

    async def test_invoke_append_reports_duplicate_id_in_batch_input(self):
        """Report when two appended tasks share the same ID."""
        duplicate_id = str(uuid.uuid4())
        append_data = [
            {
                "id": duplicate_id,
                "content": "New Task 4",
                "activeForm": "Executing New Task 4",
                "status": TodoStatus.PENDING.value,
            },
            {
                "id": duplicate_id,
                "content": "New Task 5",
                "activeForm": "Executing New Task 5",
                "status": TodoStatus.PENDING.value,
            },
        ]

        with self.assertRaises(FrameworkError) as cm:
            await self.tool.invoke(
                {"action": "append", "todos": append_data},
                session=self.mock_session,
            )

        self.assertIn(
            f"Task with ID '{duplicate_id}' is duplicated in batch input",
            str(cm.exception),
        )
        self.tool.save_todos.assert_not_awaited()

    async def test_invoke_append_rejects_invalid_sequence_without_mutation(self):
        """Reject append that advances past a pending item without mutating input."""
        current_todos = [
            TodoItem.create(content="Pending Task", status=TodoStatus.PENDING),
        ]
        self.tool.load_todos = AsyncMock(return_value=current_todos)
        new_todo_id = str(uuid.uuid4())

        with self.assertRaises(FrameworkError):
            await self.tool.invoke(
                {
                    "action": "append",
                    "todos": [{
                        "id": new_todo_id,
                        "content": "Completed Task",
                        "activeForm": "Completed Task",
                        "status": TodoStatus.COMPLETED.value,
                    }],
                },
                session=self.mock_session,
            )

        self.tool.save_todos.assert_not_awaited()
        self.assertEqual(len(current_todos), 1)
        self.assertEqual(current_todos[0].status, TodoStatus.PENDING)

    async def test_invoke_insert_after_success(self):
        """Test successful insertion after target task"""
        # Construct insert data
        new_todo_id = str(uuid.uuid4())
        insert_data = [{
            "id": new_todo_id,
            "content": "Inserted Task",
            "activeForm": "Executing Inserted Task",
            "status": TodoStatus.PENDING.value
        }]
        inputs = {
            "action": "insert_after",
            "todo_data": {"target_id": self.test_todo_ids[0], "items": insert_data}
        }

        # Execute call
        result = await self.tool.invoke(inputs, session=self.mock_session)

        # Verify
        self.assertIn(f"Successfully inserted 1 task(s) after target task, id: '{self.test_todo_ids[0]}'",
                      result["message"])
        saved_todos = self.tool.save_todos.call_args[0][0]
        self.assertEqual(len(saved_todos), 4)
        self.assertEqual(saved_todos[1].id, new_todo_id)  # Verify insertion position

    async def test_invoke_insert_before_success(self):
        """Test successful insertion before target task"""
        # Construct insert data
        new_todo_id = str(uuid.uuid4())
        insert_data = [{
            "id": new_todo_id,
            "content": "Inserted Before Task",
            "activeForm": "Executing Inserted Before Task",
            "status": TodoStatus.PENDING.value
        }]
        inputs = {
            "action": "insert_before",
            "todo_data": {"target_id": self.test_todo_ids[1], "items": insert_data}
        }

        # Execute call
        result = await self.tool.invoke(inputs, session=self.mock_session)

        # Verify
        self.assertIn(f"Successfully inserted 1 task(s) before target task, id: '{self.test_todo_ids[1]}'",
                      result["message"])
        saved_todos = self.tool.save_todos.call_args[0][0]
        self.assertEqual(len(saved_todos), 4)
        self.assertEqual(saved_todos[1].id, new_todo_id)  # Verify insertion position

    async def test_invoke_insert_after_rejects_invalid_sequence_without_save(self):
        """Reject insertion before an existing completed item."""
        current_todos = [
            TodoItem.create(content="Active Task", status=TodoStatus.IN_PROGRESS),
            TodoItem.create(content="Completed Task", status=TodoStatus.COMPLETED),
        ]
        self.tool.load_todos = AsyncMock(return_value=current_todos)
        new_todo_id = str(uuid.uuid4())

        with self.assertRaises(FrameworkError):
            await self.tool.invoke(
                {
                    "action": "insert_after",
                    "todo_data": {
                        "target_id": current_todos[0].id,
                        "items": [{
                            "id": new_todo_id,
                            "content": "Inserted Task",
                            "activeForm": "Executing inserted task",
                            "status": TodoStatus.PENDING.value,
                        }],
                    },
                },
                session=self.mock_session,
            )

        self.tool.save_todos.assert_not_awaited()
        self.assertNotIn(new_todo_id, [todo.id for todo in current_todos])

    async def test_invoke_insert_before_rejects_invalid_sequence_without_save(self):
        """Reject insertion that leaves a pending item before a completed item."""
        current_todos = [
            TodoItem.create(content="Pending Task", status=TodoStatus.PENDING),
            TodoItem.create(content="Completed Task", status=TodoStatus.COMPLETED),
        ]
        self.tool.load_todos = AsyncMock(return_value=current_todos)
        new_todo_id = str(uuid.uuid4())

        with self.assertRaises(FrameworkError):
            await self.tool.invoke(
                {
                    "action": "insert_before",
                    "todo_data": {
                        "target_id": current_todos[0].id,
                        "items": [{
                            "id": new_todo_id,
                            "content": "Inserted Task",
                            "activeForm": "Executing inserted task",
                            "status": TodoStatus.PENDING.value,
                        }],
                    },
                },
                session=self.mock_session,
            )

        self.tool.save_todos.assert_not_awaited()
        self.assertNotIn(new_todo_id, [todo.id for todo in current_todos])

    async def test_invoke_insert_after_rejects_second_in_progress_without_save(self):
        """Reject an inserted in-progress item without persisting any new task."""
        current_todos = [
            TodoItem.create(content="Active Task", status=TodoStatus.IN_PROGRESS),
        ]
        self.tool.load_todos = AsyncMock(return_value=current_todos)
        new_todo_id = str(uuid.uuid4())

        with self.assertRaises(FrameworkError):
            await self.tool.invoke(
                {
                    "action": "insert_after",
                    "todo_data": {
                        "target_id": current_todos[0].id,
                        "items": [{
                            "id": new_todo_id,
                            "content": "Second Active Task",
                            "activeForm": "Executing second active task",
                            "status": TodoStatus.IN_PROGRESS.value,
                        }],
                    },
                },
                session=self.mock_session,
            )

        self.tool.save_todos.assert_not_awaited()
        self.assertNotIn(new_todo_id, [todo.id for todo in current_todos])

    async def test_invoke_insert_after_allows_cancelled_suffix(self):
        """Allow insertion when only cancelled tasks follow the insertion point."""
        current_todos = [
            TodoItem.create(content="Active Task", status=TodoStatus.IN_PROGRESS),
            TodoItem.create(content="Cancelled Task", status=TodoStatus.CANCELLED),
        ]
        self.tool.load_todos = AsyncMock(return_value=current_todos)
        new_todo_id = str(uuid.uuid4())

        result = await self.tool.invoke(
            {
                "action": "insert_after",
                "todo_data": {
                    "target_id": current_todos[0].id,
                    "items": [{
                        "id": new_todo_id,
                        "content": "Inserted Task",
                        "activeForm": "Executing inserted task",
                        "status": TodoStatus.PENDING.value,
                    }],
                },
            },
            session=self.mock_session,
        )

        self.assertIn("Successfully inserted 1 task(s)", result["message"])
        saved_todos = self.tool.save_todos.call_args[0][0]
        self.assertEqual([todo.id for todo in saved_todos], [
            current_todos[0].id,
            new_todo_id,
            current_todos[1].id,
        ])

    async def test_invoke_cancel_success(self):
        """Test successful cancellation of todo items"""
        # Construct input
        inputs = {
            "action": "cancel",
            "ids": [self.test_todo_ids[1]]  # Cancel Task 2
        }

        # Execute call
        result = await self.tool.invoke(inputs, session=self.mock_session)

        # Verify
        self.assertIn(f"Successfully cancelled 1 task(s) (IDs: {self.test_todo_ids[1]})", result["message"])
        # Verify Task 2 status changed to CANCELLED
        saved_todos = self.tool.save_todos.call_args[0][0]
        self.assertEqual(len(saved_todos), 3)  # Tasks still in list, just status changed
        cancelled_todo = next(t for t in saved_todos if t.id == self.test_todo_ids[1])
        self.assertEqual(cancelled_todo.status, TodoStatus.CANCELLED)

    async def test_invoke_cancel_nonexistent(self):
        """Test cancellation of nonexistent todo items"""
        # Construct input
        inputs = {
            "action": "cancel",
            "ids": ["nonexistent_id"]
        }

        # Execute call
        result = await self.tool.invoke(inputs, session=self.mock_session)

        # Verify
        self.assertIn("No tasks cancelled: None of the provided IDs (nonexistent_id) were found", result["message"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
