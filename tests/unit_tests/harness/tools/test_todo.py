# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
import json
import unittest
import uuid
from unittest.mock import patch, MagicMock, mock_open, AsyncMock, PropertyMock

from openjiuwen.core.common.exception.errors import FrameworkError, ValidationError
from openjiuwen.core.common.logging import tool_logger
from openjiuwen.core.sys_operation import SysOperation
from openjiuwen.core.sys_operation.fs import BaseFsOperation
from openjiuwen.harness.schema.task import (
    STATUS_ICONS,
    TodoItem,
    TodoStatus,
)
from openjiuwen.harness.tools import (
    TodoTool,
    TodoCreateTool,
    TodoListTool,
    TodoModifyTool,
)
from openjiuwen.harness.tools.todo import TodoGetTool


class TestTodoItem(unittest.TestCase):
    """Test core functionality of TodoItem data class."""

    def setUp(self):
        self.test_content = "Test task content"
        self.test_active_form = "Executing test task"

    def test_todo_item_create(self):
        """TodoItem.create sets content, activeForm, status correctly."""
        todo = TodoItem.create(content=self.test_content, active_form=self.test_active_form)
        self.assertEqual(todo.content, self.test_content)
        self.assertEqual(todo.activeForm, self.test_active_form)
        self.assertEqual(todo.status, TodoStatus.PENDING)
        self.assertIsNone(todo.selected_model_id)

    def test_todo_item_to_dict(self):
        """TodoItem.to_dict includes all fields."""
        todo = TodoItem.create(content=self.test_content, active_form=self.test_active_form)
        d = todo.to_dict()
        self.assertEqual(d["content"], self.test_content)
        self.assertEqual(d["activeForm"], self.test_active_form)
        self.assertEqual(d["status"], TodoStatus.PENDING.value)
        self.assertEqual(d["id"], todo.id)

    def test_todo_item_from_dict(self):
        """TodoItem.from_dict reconstructs the item correctly."""
        test_id = str(uuid.uuid4())
        todo_dict = {
            "id": test_id,
            "content": self.test_content,
            "activeForm": self.test_active_form,
            "description": "",
            "status": TodoStatus.IN_PROGRESS.value,
            "depends_on": [],
            "result_summary": None,
            "meta_data": None,
            "selected_model_id": "fast",
        }
        todo = TodoItem.from_dict(todo_dict)
        self.assertEqual(todo.id, test_id)
        self.assertEqual(todo.content, self.test_content)
        self.assertEqual(todo.status, TodoStatus.IN_PROGRESS)
        self.assertEqual(todo.selected_model_id, "fast")

    def test_todo_item_create_with_model_id(self):
        """TodoItem.create stores selected_model_id."""
        todo = TodoItem.create(content="task", selected_model_id="smart")
        self.assertEqual(todo.selected_model_id, "smart")


class TestTodoTool(unittest.IsolatedAsyncioTestCase):
    """Test TodoTool base class persistence operations."""

    def setUp(self):
        self.test_todos = [
            TodoItem.create(content="Task 1", status=TodoStatus.IN_PROGRESS),
            TodoItem.create(content="Task 2", status=TodoStatus.PENDING),
        ]
        self.mock_operation = MagicMock(spec=SysOperation)
        self.mock_fs = MagicMock(spec=BaseFsOperation)
        self.mock_operation.fs.return_value = self.mock_fs
        self.mock_fs.read_file = AsyncMock()
        self.mock_fs.write_file = AsyncMock()
        self.logger_mocks = {
            "info": patch.object(tool_logger, "info", MagicMock()).start(),
            "error": patch.object(tool_logger, "error", MagicMock()).start(),
            "warning": patch.object(tool_logger, "warning", MagicMock()).start(),
        }

    def tearDown(self):
        for mock in self.logger_mocks.values():
            mock.stop()

    async def test_load_todos_success(self):
        """load_todos deserializes JSON file into TodoItem list."""
        mock_read_result = MagicMock(
            code=0,
            data=MagicMock(content=json.dumps([todo.to_dict() for todo in self.test_todos]))
        )
        self.mock_fs.read_file.return_value = mock_read_result
        tool = TodoTool(MagicMock(), self.mock_operation)
        await tool.set_file("test_session")
        with patch("os.path.abspath", return_value="/mock/path"), \
             patch("os.path.isfile", return_value=True):
            loaded = await tool.load_todos()
        self.assertEqual(len(loaded), 2)
        self.assertEqual(loaded[0].content, "Task 1")
        self.assertEqual(loaded[1].status, TodoStatus.PENDING)

    async def test_load_todos_read_fail(self):
        """load_todos raises FrameworkError on read failure."""
        self.mock_fs.read_file.return_value = MagicMock(code=1, data=None)
        tool = TodoTool(MagicMock(), self.mock_operation)
        await tool.set_file("test_session")
        with patch("os.path.abspath", return_value="/mock/path"), \
             patch("os.path.isfile", return_value=True):
            with self.assertRaises(FrameworkError) as cm:
                await tool.load_todos()
        self.assertIn("todo tool loads failed", str(cm.exception))

    async def test_save_todos_success(self):
        """save_todos serializes TodoItem list to JSON file."""
        self.mock_fs.write_file.return_value = MagicMock(code=0)
        tool = TodoTool(MagicMock(), self.mock_operation)
        await tool.set_file("test_session")
        await tool.save_todos(self.test_todos)
        self.mock_fs.write_file.assert_called_once()
        call_args = self.mock_fs.write_file.call_args
        self.assertIn("Task 1", call_args[0][1])
        self.assertEqual(call_args[1].get("mode"), "text")

    async def test_save_todos_write_fail(self):
        """save_todos raises FrameworkError on write failure."""
        self.mock_fs.write_file.return_value = MagicMock(code=1)
        tool = TodoTool(MagicMock(), self.mock_operation)
        await tool.set_file("test_session")
        with self.assertRaises(FrameworkError) as cm:
            await tool.save_todos(self.test_todos)
        self.assertIn("Failed to save todo list, because write_file fail", str(cm.exception))


class TestTodoCreateTool(unittest.IsolatedAsyncioTestCase):
    """Test TodoCreateTool functionality."""

    def setUp(self):
        self.mock_operation = MagicMock(spec=SysOperation)
        self.mock_fs = MagicMock(spec=BaseFsOperation)
        self.mock_operation.fs.return_value = self.mock_fs
        self.tool = TodoCreateTool(operation=self.mock_operation)
        self.tool.load_todos = AsyncMock(return_value=[])
        self.tool.save_todos = AsyncMock()
        self.logger_mocks = {
            "info": patch.object(tool_logger, "info", MagicMock()).start(),
            "error": patch.object(tool_logger, "error", MagicMock()).start(),
            "warning": patch.object(tool_logger, "warning", MagicMock()).start(),
        }

    def tearDown(self):
        patch.stopall()

    async def test_invoke_create_json_array(self):
        """Creates todos from JSON array."""
        result = await self.tool.invoke({
            "tasks": [
                {"content": "Task 1", "activeForm": "Doing Task 1", "description": "Desc 1"},
                {"content": "Task 2", "activeForm": "Doing Task 2", "description": "Desc 2"},
                {"content": "Task 3", "activeForm": "Doing Task 3", "description": "Desc 3"}
            ]
        })
        self.assertIn("Successfully created 3 task(s)", result["message"])
        saved = self.tool.save_todos.call_args[0][0]
        self.assertEqual(len(saved), 3)
        self.assertEqual(saved[0].status, TodoStatus.IN_PROGRESS)
        self.assertEqual(saved[1].status, TodoStatus.PENDING)

    async def test_invoke_create_with_chinese_content(self):
        """Chinese content in task fields works correctly."""
        inputs = {
            "tasks": [
                {"content": "需求分析", "activeForm": "正在分析需求", "description": "明确项目目标、用户需求及功能边界"},
                {"content": "技术选型", "activeForm": "正在选型", "description": "评估并选择合适的技术栈"},
                {"content": "实施方案", "activeForm": "正在制定方案", "description": "制定开发计划、分配任务"},
            ]
        }
        result = await self.tool.invoke(inputs)
        self.assertIn("Successfully created 3 task(s)", result["message"])
        saved = self.tool.save_todos.call_args[0][0]
        self.assertEqual(len(saved), 3)
        self.assertIn("目标、用户需求及功能边界", saved[0].description)
        self.assertIn("开发计划、分配任务", saved[2].description)

    async def test_invoke_create_json_array(self):
        """Creates todos from JSON array with selected_model_id."""
        tasks = [
            {"content": "Translate doc", "activeForm": "Translating doc", "description": "Translate document to English", "selected_model_id": "fast"},
            {"content": "Analyze code", "activeForm": "Analyzing code", "description": "Analyze code architecture", "selected_model_id": "smart"},
        ]
        result = await self.tool.invoke({"tasks": tasks})
        self.assertIn("Successfully created 2 task(s)", result["message"])
        saved = self.tool.save_todos.call_args[0][0]
        self.assertEqual(saved[0].selected_model_id, "fast")
        self.assertEqual(saved[1].selected_model_id, "smart")
        self.assertEqual(saved[0].status, TodoStatus.IN_PROGRESS)

    async def test_invoke_create_empty_tasks(self):
        """Raises error for empty task list."""
        with self.assertRaises(Exception):
            await self.tool.invoke({"tasks": []})

    async def test_invoke_missing_tasks_param(self):
        """Raises error when tasks parameter is missing."""
        with self.assertRaises(Exception):
            await self.tool.invoke({})

    async def test_invoke_create_invalid_json_string(self):
        """Raises error for non-array input (string format no longer supported)."""
        with self.assertRaises(Exception):
            await self.tool.invoke({"tasks": "not a valid json array"})

    async def test_invoke_create_missing_required_field(self):
        """Raises error when required field (content, activeForm, or description) is missing."""
        with self.assertRaises(Exception):
            await self.tool.invoke({"tasks": [{"activeForm": "Doing"}]})
        with self.assertRaises(Exception):
            await self.tool.invoke({"tasks": [{"content": "Task 1"}]})
        with self.assertRaises(Exception):
            await self.tool.invoke({"tasks": [{"content": "Task 1", "activeForm": "Doing"}]})


class TestTodoListTool(unittest.IsolatedAsyncioTestCase):
    """Test TodoListTool functionality."""

    def setUp(self):
        self.mock_operation = MagicMock(spec=SysOperation)
        self.mock_fs = MagicMock(spec=BaseFsOperation)
        self.mock_operation.fs.return_value = self.mock_fs
        self.tool = TodoListTool(operation=self.mock_operation)
        self.test_todos = [
            TodoItem.create(content="In Progress Task", status=TodoStatus.IN_PROGRESS),
            TodoItem.create(content="Pending Task", status=TodoStatus.PENDING),
            TodoItem.create(content="Completed Task", status=TodoStatus.COMPLETED),
            TodoItem.create(content="Cancelled Task", status=TodoStatus.CANCELLED),
        ]
        self.tool.load_todos = AsyncMock(return_value=self.test_todos)
        self.logger_mocks = {
            "info": patch.object(tool_logger, "info", MagicMock()).start(),
            "error": patch.object(tool_logger, "error", MagicMock()).start(),
        }

    def tearDown(self):
        patch.stopall()

    async def test_invoke_list_success(self):
        """Returns only active (non-completed, non-cancelled) tasks in simplified format."""
        result = await self.tool.invoke({})
        self.assertIn("tasks", result)
        tasks = result["tasks"]
        # Only IN_PROGRESS and PENDING should be returned
        self.assertEqual(len(tasks), 2)
        contents = [t["content"] for t in tasks]
        self.assertIn("In Progress Task", contents)
        self.assertIn("Pending Task", contents)
        self.assertNotIn("Completed Task", contents)
        self.assertNotIn("Cancelled Task", contents)
        # Each item has id, content, status, depends_on
        self.assertIn("id", tasks[0])
        self.assertIn("depends_on", tasks[0])


class TestTodoModifyTool(unittest.IsolatedAsyncioTestCase):
    """Test TodoModifyTool functionality."""

    def setUp(self):
        self.mock_operation = MagicMock(spec=SysOperation)
        self.mock_fs = MagicMock(spec=BaseFsOperation)
        self.mock_operation.fs.return_value = self.mock_fs
        self.tool = TodoModifyTool(operation=self.mock_operation)
        self.test_todos = [
            TodoItem.create(content="Task 1", status=TodoStatus.IN_PROGRESS, active_form="Executing Task 1"),
            TodoItem.create(content="Task 2", status=TodoStatus.PENDING, active_form="Executing Task 2"),
            TodoItem.create(content="Task 3", status=TodoStatus.PENDING, active_form="Executing Task 3"),
        ]
        self.test_todo_ids = [todo.id for todo in self.test_todos]
        self.tool.load_todos = AsyncMock(return_value=self.test_todos.copy())
        self.tool.save_todos = AsyncMock()
        self.logger_mocks = {
            "info": patch.object(tool_logger, "info", MagicMock()).start(),
            "error": patch.object(tool_logger, "error", MagicMock()).start(),
            "warning": patch.object(tool_logger, "warning", MagicMock()).start(),
        }

    def tearDown(self):
        patch.stopall()

    async def test_invoke_delete_success(self):
        """Deletes specified task by ID."""
        result = await self.tool.invoke({"action": "delete", "ids": [self.test_todo_ids[1]]})
        self.assertIn(f"Successfully deleted 1 task(s)", result["message"])
        saved = self.tool.save_todos.call_args[0][0]
        self.assertEqual(len(saved), 2)
        self.assertEqual(saved[0].content, "Task 1")
        self.assertEqual(saved[1].content, "Task 3")

    async def test_invoke_delete_nonexistent(self):
        """Returns message when deleting nonexistent ID."""
        result = await self.tool.invoke({"action": "delete", "ids": ["nonexistent_id"]})
        self.assertIn("No tasks deleted", result["message"])

    async def test_invoke_update_success(self):
        """Updates task content and status."""
        update_data = [{"id": self.test_todo_ids[0], "content": "Updated Task 1",
                        "activeForm": "Executing Updated Task 1", "status": TodoStatus.COMPLETED.value}]
        result = await self.tool.invoke({"action": "update", "todos": update_data})
        self.assertIn("Successfully updated 1 task(s)", result["message"])
        saved = self.tool.save_todos.call_args[0][0]
        updated = next(t for t in saved if t.id == self.test_todo_ids[0])
        self.assertEqual(updated.content, "Updated Task 1")
        self.assertEqual(updated.status, TodoStatus.COMPLETED)

    async def test_invoke_update_partial_fields_success(self):
        """Partial update (status only) preserves existing content and activeForm."""
        result = await self.tool.invoke({
            "action": "update",
            "todos": [{"id": self.test_todo_ids[0], "status": TodoStatus.COMPLETED.value}],
        })
        self.assertIn("Successfully updated 1 task(s)", result["message"])
        saved = self.tool.save_todos.call_args[0][0]
        updated = next(t for t in saved if t.id == self.test_todo_ids[0])
        self.assertEqual(updated.status, TodoStatus.COMPLETED)
        self.assertEqual(updated.content, "Task 1")
        self.assertEqual(updated.activeForm, "Executing Task 1")

    async def test_invoke_update_selected_model_id(self):
        """Update can change selected_model_id."""
        result = await self.tool.invoke({
            "action": "update",
            "todos": [{"id": self.test_todo_ids[0], "selected_model_id": "smart",
                       "status": TodoStatus.PENDING.value}],
        })
        self.assertIn("Successfully updated 1 task(s)", result["message"])
        saved = self.tool.save_todos.call_args[0][0]
        updated = next(t for t in saved if t.id == self.test_todo_ids[0])
        self.assertEqual(updated.selected_model_id, "smart")

    async def test_invoke_append_success(self):
        """Appends new task to end of list."""
        new_id = str(uuid.uuid4())
        result = await self.tool.invoke({
            "action": "append",
            "todos": [{"id": new_id, "content": "New Task 4", "activeForm": "Executing New Task 4",
                       "description": "description of New Task 4", "status": TodoStatus.PENDING.value}],
        })
        self.assertIn("Successfully appended 1 task(s)", result["message"])
        saved = self.tool.save_todos.call_args[0][0]
        self.assertEqual(len(saved), 4)
        self.assertEqual(saved[-1].id, new_id)

    async def test_invoke_insert_after_success(self):
        """Inserts task after target."""
        new_id = str(uuid.uuid4())
        result = await self.tool.invoke({
            "action": "insert_after",
            "todo_data": {"target_id": self.test_todo_ids[0], "items": [
                {"id": new_id, "content": "Inserted Task", "activeForm": "Executing Inserted Task",
                 "description": "description of New Task 4", "status": TodoStatus.PENDING.value}
            ]},
        })
        self.assertIn(f"Successfully inserted 1 task(s) after target task", result["message"])
        saved = self.tool.save_todos.call_args[0][0]
        self.assertEqual(len(saved), 4)
        self.assertEqual(saved[1].id, new_id)

    async def test_invoke_insert_before_success(self):
        """Inserts task before target."""
        new_id = str(uuid.uuid4())
        result = await self.tool.invoke({
            "action": "insert_before",
            "todo_data": {"target_id": self.test_todo_ids[1], "items": [
                {"id": new_id, "content": "Inserted Before Task", "activeForm": "Executing",
                 "description": "description of New Task 4", "status": TodoStatus.PENDING.value}
            ]},
        })
        self.assertIn(f"Successfully inserted 1 task(s) before target task", result["message"])
        saved = self.tool.save_todos.call_args[0][0]
        self.assertEqual(len(saved), 4)
        self.assertEqual(saved[1].id, new_id)

    async def test_invoke_cancel_success(self):
        """Cancels specified task."""
        result = await self.tool.invoke({"action": "cancel", "ids": [self.test_todo_ids[1]]})
        self.assertIn(f"Successfully cancelled 1 task(s)", result["message"])
        saved = self.tool.save_todos.call_args[0][0]
        cancelled = next(t for t in saved if t.id == self.test_todo_ids[1])
        self.assertEqual(cancelled.status, TodoStatus.CANCELLED)

    async def test_invoke_cancel_nonexistent(self):
        """Returns message when cancelling nonexistent ID."""
        result = await self.tool.invoke({"action": "cancel", "ids": ["nonexistent_id"]})
        self.assertIn("No tasks cancelled", result["message"])


class TestTodoGetTool(unittest.IsolatedAsyncioTestCase):
    """Test TodoGetTool functionality."""

    def setUp(self):
        self.mock_operation = MagicMock(spec=SysOperation)
        self.mock_fs = MagicMock(spec=BaseFsOperation)
        self.mock_operation.fs.return_value = self.mock_fs
        self.tool = TodoGetTool(operation=self.mock_operation)
        self.test_todos = [
            TodoItem.create(
                content="Task 1",
                status=TodoStatus.IN_PROGRESS,
                active_form="Executing Task 1",
                description="Detailed description for Task 1",
                selected_model_id="smart",
            ),
            TodoItem.create(
                content="Task 2",
                status=TodoStatus.PENDING,
                active_form="Executing Task 2",
                description="Detailed description for Task 2",
            ),
        ]
        self.test_todo_ids = [todo.id for todo in self.test_todos]
        self.tool.load_todos = AsyncMock(return_value=self.test_todos.copy())
        self.logger_mocks = {
            "info": patch.object(tool_logger, "info", MagicMock()).start(),
            "error": patch.object(tool_logger, "error", MagicMock()).start(),
        }

    def tearDown(self):
        patch.stopall()

    async def test_invoke_get_success(self):
        """Returns full details of a single task by ID."""
        result = await self.tool.invoke({"id": self.test_todo_ids[0]})
        self.assertIn("todo", result)
        todo = result["todo"]
        self.assertEqual(todo["id"], self.test_todo_ids[0])
        self.assertEqual(todo["content"], "Task 1")
        self.assertEqual(todo["status"], "in_progress")
        self.assertEqual(todo["description"], "Detailed description for Task 1")
        self.assertEqual(todo["selected_model_id"], "smart")

    async def test_invoke_get_not_found(self):
        """Raises error when task ID is not found."""
        with self.assertRaises(FrameworkError):
            await self.tool.invoke({"id": "nonexistent_id"})

    async def test_invoke_get_missing_id(self):
        """Raises error when id parameter is missing."""
        with self.assertRaises(ValidationError):
            await self.tool.invoke({})


if __name__ == "__main__":
    unittest.main(verbosity=2)
