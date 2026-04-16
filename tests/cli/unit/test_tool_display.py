"""Unit tests for openjiuwen.harness.cli.ui.tool_display."""

from __future__ import annotations

from openjiuwen.harness.cli.ui.tool_display import (
    format_tool_args,
    format_tool_result,
    format_write_preview,
    get_display_name,
)


class TestGetDisplayName:
    """Tests for tool name mapping."""

    def test_read_file(self) -> None:
        assert get_display_name("read_file") == "Read"

    def test_write_file(self) -> None:
        assert get_display_name("write_file") == "Write"

    def test_edit_file(self) -> None:
        assert get_display_name("edit_file") == "Edit"

    def test_bash(self) -> None:
        assert get_display_name("bash") == "Bash"

    def test_grep(self) -> None:
        assert get_display_name("grep") == "Grep"

    def test_glob(self) -> None:
        assert get_display_name("glob") == "Glob"

    def test_todo_create(self) -> None:
        assert get_display_name("todo_create") == "TodoWrite"

    def test_web_search(self) -> None:
        assert get_display_name("web_free_search") == "WebSearch"

    def test_unknown_tool(self) -> None:
        """Unknown tools get title-cased."""
        assert get_display_name("my_custom_tool") == "My Custom Tool"


class TestFormatToolArgs:
    """Tests for tool argument formatting."""

    def test_read_file_path(self) -> None:
        """read_file shows file_path."""
        result = format_tool_args(
            "read_file", {"file_path": "/src/main.py"}
        )
        assert "main.py" in result

    def test_read_file_with_limit(self) -> None:
        """read_file shows limit when present."""
        result = format_tool_args(
            "read_file",
            {"file_path": "/src/main.py", "limit": 10},
        )
        assert "limit=10" in result

    def test_bash_truncates_long(self) -> None:
        """bash truncates commands > 60 chars."""
        long_cmd = "a" * 80
        result = format_tool_args("bash", {"command": long_cmd})
        assert len(result) <= 63  # 57 + "..."
        assert result.endswith("...")

    def test_bash_short_command(self) -> None:
        """bash shows full short command."""
        result = format_tool_args(
            "bash", {"command": "git status"}
        )
        assert result == "git status"

    def test_grep_pattern_and_path(self) -> None:
        """grep shows pattern and path."""
        result = format_tool_args(
            "grep", {"pattern": "def hello", "path": "src/"}
        )
        assert '"def hello"' in result
        assert "src/" in result

    def test_glob_pattern(self) -> None:
        result = format_tool_args("glob", {"pattern": "**/*.py"})
        assert result == "**/*.py"

    def test_todo_no_args(self) -> None:
        """todo tools show empty args."""
        result = format_tool_args("todo_create", {"tasks": "a;b"})
        assert result == ""

    def test_string_args_parsed(self) -> None:
        """JSON string args are parsed."""
        result = format_tool_args(
            "read_file", '{"file_path": "/test.py"}'
        )
        assert "test.py" in result


class TestFormatToolResult:
    """Tests for tool result summarization."""

    def test_read_file_lines(self) -> None:
        result = format_tool_result(
            "read_file", "line1\nline2\nline3\n"
        )
        assert "Read 3 lines" in result

    def test_read_file_single_line(self) -> None:
        result = format_tool_result("read_file", "line1")
        assert result == "Read 1 lines"

    def test_read_file_single_line_with_trailing_newline(self) -> None:
        result = format_tool_result("read_file", "line1\n")
        assert result == "Read 1 lines"

    def test_bash_single_line(self) -> None:
        """Short bash output shown directly."""
        result = format_tool_result("bash", "hello world")
        assert result == "hello world"

    def test_bash_multi_line(self) -> None:
        """Multi-line bash result truncated."""
        output = "line1\nline2\nline3\nline4"
        result = format_tool_result("bash", output)
        assert "+3 lines" in result

    def test_grep_matches(self) -> None:
        result = format_tool_result(
            "grep", "file1.py:10:match\nfile2.py:20:match\n"
        )
        assert "Found 2 matches" in result

    def test_grep_no_matches(self) -> None:
        result = format_tool_result("grep", "")
        assert "Done" in result  # empty result → "Done"

    def test_glob_files(self) -> None:
        result = format_tool_result(
            "glob", "a.py\nb.py\nc.py\n"
        )
        assert "Found 3 files" in result

    def test_empty_result(self) -> None:
        assert format_tool_result("bash", "") == "Done"


class TestFormatWritePreview:
    """Tests for write content preview."""

    def test_short_content(self) -> None:
        """Content ≤ 5 lines shows all."""
        content = "line1\nline2\nline3"
        result = format_write_preview(content)
        assert "1 line1" in result
        assert "2 line2" in result
        assert "3 line3" in result
        assert "…" not in result

    def test_long_content_truncated(self) -> None:
        """Content > 5 lines shows first 5 + count."""
        content = "\n".join(f"line{i}" for i in range(10))
        result = format_write_preview(content)
        assert "… +5 lines" in result
