"""Unit tests for openjiuwen.harness.cli.ui.todo_render."""

from __future__ import annotations

from openjiuwen.harness.cli.ui.todo_render import (
    parse_todo_result,
    render_todo_item,
    render_todo_list,
    render_todo_summary,
)


class TestRenderTodoItem:
    """Tests for single todo item rendering."""

    def test_completed_shows_checkmark(self) -> None:
        """completed → ☑ (green)."""
        result = render_todo_item("任务A", "completed")
        assert "☑" in result
        assert "green" in result
        assert "任务A" in result

    def test_in_progress_shows_half(self) -> None:
        """in_progress → ◐ (yellow)."""
        result = render_todo_item("任务B", "in_progress")
        assert "◐" in result
        assert "yellow" in result

    def test_pending_shows_empty(self) -> None:
        """pending → ☐ (dim)."""
        result = render_todo_item("任务C", "pending")
        assert "☐" in result
        assert "dim" in result

    def test_cancelled_shows_crossed(self) -> None:
        """cancelled → ☒."""
        result = render_todo_item("任务D", "cancelled")
        assert "☒" in result

    def test_unknown_status_defaults_to_pending(self) -> None:
        """Unknown status falls back to ☐."""
        result = render_todo_item("任务E", "unknown_status")
        assert "☐" in result


class TestRenderTodoList:
    """Tests for todo list rendering."""

    def test_mixed_status_list(self) -> None:
        """Mixed status items render with correct checkboxes."""
        items = [
            {"content": "完成的", "status": "completed"},
            {"content": "进行中", "status": "in_progress"},
            {"content": "待处理", "status": "pending"},
        ]
        lines = render_todo_list(items)
        assert len(lines) == 3
        assert "☑" in lines[0]
        assert "◐" in lines[1]
        assert "☐" in lines[2]
        # All have ⎿ prefix
        for line in lines:
            assert "⎿" in line

    def test_empty_list(self) -> None:
        """Empty list returns empty lines."""
        assert render_todo_list([]) == []

    def test_activeform_fallback(self) -> None:
        """Uses activeForm if content is absent."""
        items = [{"activeForm": "进行中的活动", "status": "in_progress"}]
        lines = render_todo_list(items)
        assert "进行中的活动" in lines[0]


class TestRenderTodoSummary:
    """Tests for progress summary line."""

    def test_mixed_summary(self) -> None:
        """✓2 ◐1 ☐1 format."""
        items = [
            {"status": "completed"},
            {"status": "completed"},
            {"status": "in_progress"},
            {"status": "pending"},
        ]
        result = render_todo_summary(items)
        assert "✓2" in result
        assert "◐1" in result
        assert "☐1" in result

    def test_empty_summary(self) -> None:
        """Empty list → 'No tasks'."""
        assert render_todo_summary([]) == "No tasks"

    def test_all_completed(self) -> None:
        """Only completed items → only ✓."""
        items = [
            {"status": "completed"},
            {"status": "completed"},
        ]
        result = render_todo_summary(items)
        assert "✓2" in result
        assert "◐" not in result
        assert "☐" not in result


class TestParseTodoResult:
    """Tests for parsing SDK todo tool results."""

    def test_parse_json_list(self) -> None:
        """Direct JSON list is parsed."""
        result = '[{"content": "task1", "status": "pending"}]'
        items = parse_todo_result(result)
        assert items is not None
        assert len(items) == 1
        assert items[0]["content"] == "task1"

    def test_parse_json_dict_with_items(self) -> None:
        """JSON dict with 'items' key is parsed."""
        result = '{"items": [{"content": "a", "status": "completed"}]}'
        items = parse_todo_result(result)
        assert items is not None
        assert len(items) == 1

    def test_parse_create_tool_repr(self) -> None:
        """Parse TodoCreateTool's str(dict) repr format."""
        raw_dict = {
            "message": (
                "Successfully created 3 task(s):\n"
                "  [>] task_id: abc-123 , content: 设计UI\n"
                "  [ ] task_id: def-456 , content: 实现表单\n"
                "  [ ] task_id: ghi-789 , content: 添加验证\n"
                "\nNext step: execute"
            )
        }
        result = str(raw_dict)
        items = parse_todo_result(result)
        assert items is not None
        assert len(items) == 3
        assert items[0]["content"] == "设计UI"
        assert items[0]["status"] == "in_progress"
        assert items[1]["status"] == "pending"
        assert items[2]["status"] == "pending"

    def test_parse_list_tool_repr(self) -> None:
        """Parse TodoListTool's str(dict) repr format."""
        raw_dict = {
            "message": (
                "Todo List (Total: 3 items):\n"
                "\n"
                "[>] In Progress Task\n"
                " [abc-123] 设计UI\n"
                "\n"
                "[ ] Pending Tasks\n"
                " [def-456] 实现表单\n"
                " [ghi-789] 添加验证"
            )
        }
        result = str(raw_dict)
        items = parse_todo_result(result)
        assert items is not None
        assert len(items) == 3
        assert items[0]["status"] == "in_progress"
        assert items[1]["status"] == "pending"
        assert items[2]["status"] == "pending"

    def test_parse_list_tool_with_completed(self) -> None:
        """Parse TodoListTool with completed items."""
        raw_dict = {
            "message": (
                "Todo List (Total: 2 items):\n"
                "\n"
                "[√] Completed Tasks\n"
                " [abc-123] 已完成\n"
                "\n"
                "[>] In Progress Task\n"
                " [def-456] 进行中"
            )
        }
        result = str(raw_dict)
        items = parse_todo_result(result)
        assert items is not None
        assert len(items) == 2
        assert items[0]["status"] == "completed"
        assert items[1]["status"] == "in_progress"

    def test_parse_empty_string(self) -> None:
        """Empty string returns None."""
        assert parse_todo_result("") is None

    def test_parse_plain_text(self) -> None:
        """Plain text without todo markers returns None."""
        assert parse_todo_result("just some text") is None

    def test_parse_modify_tool_repr(self) -> None:
        """TodoModifyTool result (plain message) returns None."""
        raw_dict = {"message": "Successfully updated 1 task(s)"}
        result = str(raw_dict)
        items = parse_todo_result(result)
        # Modify results don't contain structured items
        assert items is None
