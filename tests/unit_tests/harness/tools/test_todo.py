# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
import unittest
import uuid

from openjiuwen.harness.schema.task import TodoItem, TodoStatus


class TestTodoItem(unittest.TestCase):
    """Test core functionality of TodoItem data class."""

    def setUp(self):
        self.test_content = "Test task content"
        self.test_active_form = "Executing test task"

    def test_todo_item_create(self):
        """TodoItem sets content, activeForm, status correctly."""
        todo = TodoItem(
            id="test_task",
            content=self.test_content,
            activeForm=self.test_active_form,
        )
        self.assertEqual(todo.content, self.test_content)
        self.assertEqual(todo.activeForm, self.test_active_form)
        self.assertEqual(todo.status, TodoStatus.PENDING)
        self.assertIsNone(todo.selected_model_id)

    def test_todo_item_to_dict(self):
        """TodoItem.to_dict includes all fields."""
        todo = TodoItem(
            id="test_task",
            content=self.test_content,
            activeForm=self.test_active_form,
        )
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
        """TodoItem stores selected_model_id."""
        todo = TodoItem(id="test_task", content="task", selected_model_id="smart")
        self.assertEqual(todo.selected_model_id, "smart")
