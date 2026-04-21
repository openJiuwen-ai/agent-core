"""Unit tests for openjiuwen.harness.cli.ui.renderer."""

from __future__ import annotations

import io
import sys
from typing import Any, AsyncIterator
from unittest.mock import AsyncMock

import pytest
from rich.console import Console

from openjiuwen.harness.cli.ui.renderer import render_stream


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class FakeChunk:
    """Minimal OutputSchema stand-in for testing."""

    def __init__(
        self,
        type: str,
        index: int = 0,
        payload: Any = None,
    ) -> None:
        self.type = type
        self.index = index
        self.payload = payload or {}


async def _async_iter(
    items: list[FakeChunk],
) -> AsyncIterator[FakeChunk]:
    for item in items:
        yield item


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestRenderStream:
    """Tests for stream rendering logic."""

    def test_write_terminal_uses_stdout_encoding_with_os_write(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Terminal writes should respect the active stdout encoding."""

        module = sys.modules[render_stream.__module__]
        payloads: list[tuple[int, bytes]] = []

        class FakeStdout:
            encoding = "utf-8"
            errors = "strict"

        monkeypatch.setattr(
            module,
            "sys",
            type("FakeSys", (), {"stdout": FakeStdout()})(),
        )
        monkeypatch.setattr(
            module.os,
            "write",
            lambda fd, data: payloads.append((fd, data)),
        )

        module._write_terminal("中文")

        assert payloads == [(1, "中文".encode("utf-8"))]

    @pytest.mark.asyncio
    async def test_llm_output_accumulated(self) -> None:
        """llm_output chunks are accumulated into the result."""
        chunks = [
            FakeChunk("llm_output", 0, {"content": "Hello "}),
            FakeChunk("llm_output", 1, {"content": "World"}),
        ]
        console = Console(file=io.StringIO())
        result = await render_stream(
            _async_iter(chunks), console
        )
        assert result.text == "Hello World"

    @pytest.mark.asyncio
    async def test_reasoning_not_in_result(self) -> None:
        """llm_reasoning is not included in the result."""
        chunks = [
            FakeChunk(
                "llm_reasoning", 0, {"content": "thinking..."}
            ),
            FakeChunk(
                "llm_output", 1, {"content": "Answer"}
            ),
        ]
        console = Console(file=io.StringIO())
        result = await render_stream(
            _async_iter(chunks), console
        )
        assert result.text == "Answer"
        assert "thinking" not in result.text

    @pytest.mark.asyncio
    async def test_answer_chunk_fallback(self) -> None:
        """answer chunk is used as fallback when no llm_output."""
        chunks = [
            FakeChunk(
                "answer", 0, {"output": "Final answer"}
            ),
        ]
        console = Console(file=io.StringIO())
        result = await render_stream(
            _async_iter(chunks), console
        )
        assert "Final answer" in result.text

    @pytest.mark.asyncio
    async def test_answer_not_duplicated(self) -> None:
        """answer chunk is skipped when llm_output was received."""
        chunks = [
            FakeChunk(
                "llm_output", 0, {"content": "Hello"}
            ),
            FakeChunk(
                "answer", 1, {"output": "Hello"}
            ),
        ]
        console = Console(file=io.StringIO())
        result = await render_stream(
            _async_iter(chunks), console
        )
        # Should appear only once, not "HelloHello"
        assert result.text == "Hello"

    @pytest.mark.asyncio
    async def test_reasoning_hidden_by_default(self) -> None:
        """Reasoning is not displayed by default."""
        chunks = [
            FakeChunk(
                "llm_reasoning", 0, {"content": "thinking..."}
            ),
            FakeChunk(
                "llm_output", 1, {"content": "Answer"}
            ),
        ]
        buf = io.StringIO()
        console = Console(file=buf)
        result = await render_stream(
            _async_iter(chunks), console
        )
        assert result.text == "Answer"
        # Reasoning should NOT appear in console output
        assert "thinking" not in buf.getvalue()

    @pytest.mark.asyncio
    async def test_reasoning_shown_when_enabled(self) -> None:
        """Reasoning is displayed when show_reasoning=True."""
        chunks = [
            FakeChunk(
                "llm_reasoning", 0, {"content": "thinking..."}
            ),
            FakeChunk(
                "llm_output", 1, {"content": "Answer"}
            ),
        ]
        buf = io.StringIO()
        console = Console(file=buf)
        result = await render_stream(
            _async_iter(chunks), console,
            show_reasoning=True,
        )
        assert result.text == "Answer"
        assert "thinking" in buf.getvalue()

    @pytest.mark.asyncio
    async def test_empty_stream_warning(self) -> None:
        """Empty stream produces a warning."""
        buf = io.StringIO()
        console = Console(file=buf)
        result = await render_stream(
            _async_iter([]), console
        )
        assert result.text == ""
        output = buf.getvalue()
        assert "No output" in output or "no output" in output.lower()

    @pytest.mark.asyncio
    async def test_invisible_stream_reports_chunk_types(self) -> None:
        """Unknown non-rendered chunks should produce a diagnostic warning."""
        chunks = [
            FakeChunk(
                "workflow_final",
                0,
                {"content": "internal only"},
            ),
        ]
        buf = io.StringIO()
        console = Console(file=buf)
        result = await render_stream(
            _async_iter(chunks), console
        )
        assert result.text == ""
        output = buf.getvalue()
        assert "No visible output received" in output
        assert "workflow_final" in output

    @pytest.mark.asyncio
    async def test_controller_task_failed_rendered(self) -> None:
        """controller_output task_failed should be surfaced to the user."""
        chunks = [
            FakeChunk(
                "controller_output",
                0,
                (
                    "type=<EventType.TASK_FAILED: 'task_failed'> "
                    'data=[TextDataFrame(type=\'text\', text="model call failed: 401")]'
                ),
            ),
        ]
        buf = io.StringIO()
        console = Console(file=buf)
        result = await render_stream(
            _async_iter(chunks), console
        )
        assert result.text == ""
        output = buf.getvalue()
        assert "model call failed: 401" in output

    @pytest.mark.asyncio
    async def test_message_chunk_rendered(self) -> None:
        """message chunks are rendered with gear icon."""
        chunks = [
            FakeChunk("message", 0, "Reading file..."),
        ]
        buf = io.StringIO()
        console = Console(file=buf)
        await render_stream(_async_iter(chunks), console)
        output = buf.getvalue()
        assert "Reading file" in output

    @pytest.mark.asyncio
    async def test_message_chunk_starts_on_new_line_after_llm_output(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """message chunks should not be appended to streamed text."""
        chunks = [
            FakeChunk(
                "llm_output", 0, {"content": "Line without newline"}
            ),
            FakeChunk("message", 1, {"content": "CI 门禁检查"}),
        ]
        terminal: list[str] = []
        monkeypatch.setattr(
            "openjiuwen.harness.cli.ui.renderer._write_terminal",
            terminal.append,
        )
        buf = io.StringIO()
        console = Console(file=buf, force_terminal=False)

        await render_stream(_async_iter(chunks), console)

        assert terminal == ["\033[92m● \033[0m", "Line without newline", "\n"]
        output = buf.getvalue()
        assert "CI 门禁检查" in output

    @pytest.mark.asyncio
    async def test_interaction_callback(self) -> None:
        """__interaction__ triggers the on_interaction callback."""
        payload = type(
            "InteractionOutput", (), {"id": "q1", "value": "Yes?"}
        )()
        chunks = [
            FakeChunk("__interaction__", 0, payload),
        ]
        callback = AsyncMock(return_value="Yes")
        console = Console(file=io.StringIO())
        await render_stream(
            _async_iter(chunks),
            console,
            on_interaction=callback,
        )
        callback.assert_awaited_once_with("q1", "Yes?")

    @pytest.mark.asyncio
    async def test_tool_call_rendered(self) -> None:
        """tool_call chunks render with ● prefix."""
        chunks = [
            FakeChunk(
                "tool_call",
                0,
                {
                    "tool_name": "read_file",
                    "tool_args": {"file_path": "/src/main.py"},
                },
            ),
        ]
        buf = io.StringIO()
        console = Console(file=buf)
        await render_stream(_async_iter(chunks), console)
        output = buf.getvalue()
        assert "Read" in output
        assert "main.py" in output

    @pytest.mark.asyncio
    async def test_tool_result_rendered(self) -> None:
        """tool_result chunks render with ⎿ prefix."""
        chunks = [
            FakeChunk(
                "tool_result",
                0,
                {
                    "tool_name": "read_file",
                    "tool_args": {},
                    "tool_result": "line1\nline2\nline3\n",
                },
            ),
        ]
        buf = io.StringIO()
        console = Console(file=buf)
        await render_stream(_async_iter(chunks), console)
        output = buf.getvalue()
        assert "Read 3 lines" in output

    @pytest.mark.asyncio
    async def test_tool_result_rendered_uses_structured_line_count(self) -> None:
        """read_file summaries should prefer structured line_count metadata."""
        chunks = [
            FakeChunk(
                "tool_result",
                0,
                {
                    "tool_name": "read_file",
                    "tool_args": {},
                    "tool_result": "File unchanged since last read. Reuse the previously returned content.",
                    "line_count": 50,
                },
            ),
        ]
        buf = io.StringIO()
        console = Console(file=buf)
        await render_stream(_async_iter(chunks), console)
        output = buf.getvalue()
        assert "Read 50 lines" in output

    @pytest.mark.asyncio
    async def test_todo_tool_renders_checkboxes(self) -> None:
        """todo_create tool_result renders checkbox items."""
        raw_dict = {
            "message": (
                "Successfully created 2 task(s):\n"
                "  [>] task_id: abc , content: Task A\n"
                "  [ ] task_id: def , content: Task B"
            )
        }
        chunks = [
            FakeChunk(
                "tool_result",
                0,
                {
                    "tool_name": "todo_create",
                    "tool_args": {},
                    "tool_result": str(raw_dict),
                },
            ),
        ]
        buf = io.StringIO()
        console = Console(file=buf)
        await render_stream(_async_iter(chunks), console)
        output = buf.getvalue()
        assert "◐" in output or "Task A" in output

    @pytest.mark.asyncio
    async def test_todo_modify_rerenders_cached_todos(self) -> None:
        """todo_modify refreshes the visible todo list from cached state."""
        create_raw_dict = {
            "message": (
                "Successfully created 2 task(s):\n"
                "  [>] task_id: a , content: Task A\n"
                "  [ ] task_id: b , content: Task B"
            )
        }
        modify_raw_dict = {
            "message": "Successfully updated 1 task(s)"
        }
        chunks = [
            FakeChunk(
                "tool_result",
                0,
                {
                    "tool_name": "todo_create",
                    "tool_args": {},
                    "tool_result": str(create_raw_dict),
                },
            ),
            FakeChunk(
                "tool_result",
                1,
                {
                    "tool_name": "todo_modify",
                    "tool_args": {
                        "action": "append",
                        "todos": [
                            {
                                "id": "c",
                                "content": "Task C",
                                "activeForm": "Task C",
                                "status": "pending",
                            }
                        ],
                    },
                    "tool_result": str(modify_raw_dict),
                },
            ),
        ]
        buf = io.StringIO()
        console = Console(file=buf)
        await render_stream(_async_iter(chunks), console)
        output = buf.getvalue()
        assert "Task A" in output
        assert "Task B" in output
        assert "Task C" in output

    @pytest.mark.asyncio
    async def test_green_bullet_on_llm_output(self) -> None:
        """LLM output starts with green ● prefix."""
        chunks = [
            FakeChunk("llm_output", 0, {"content": "Hello"}),
        ]
        console = Console(file=io.StringIO())
        # The green bullet is written to sys.stdout, not console
        # Just ensure no error and result is correct
        result = await render_stream(
            _async_iter(chunks), console
        )
        assert result.text == "Hello"
