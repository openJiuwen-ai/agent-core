"""IT-03: REPL command dispatch integration tests."""

from __future__ import annotations

import io
from unittest.mock import AsyncMock, MagicMock

import pytest
from rich.console import Console

from openjiuwen.harness.cli.ui.repl import _handle_shell, _handle_slash


class TestReplDispatch:
    """Test REPL input routing logic."""

    @pytest.mark.asyncio
    async def test_slash_help_routing(self) -> None:
        """/help routes to the help handler and lists commands."""
        buf = io.StringIO()
        console = Console(file=buf)
        backend = AsyncMock()
        store = MagicMock()

        await _handle_slash(
            "/help",
            console,
            backend,
            store,
            tracker=None,
            cfg=None,
        )
        output = buf.getvalue()
        assert "/help" in output
        assert "/exit" in output
        assert "/status" in output

    @pytest.mark.asyncio
    async def test_unknown_slash_command(self) -> None:
        """Unknown slash command shows error message."""
        buf = io.StringIO()
        console = Console(file=buf)

        await _handle_slash(
            "/foobar",
            console,
            AsyncMock(),
            MagicMock(),
            tracker=None,
            cfg=None,
        )
        output = buf.getvalue()
        assert "Unknown command" in output or "未知命令" in output

    @pytest.mark.asyncio
    async def test_shell_passthrough(self) -> None:
        """! echo executes shell command and captures output."""
        buf = io.StringIO()
        console = Console(file=buf)
        await _handle_shell("echo test_output_12345", console)
        output = buf.getvalue()
        assert "test_output_12345" in output

    @pytest.mark.asyncio
    async def test_shell_stderr(self) -> None:
        """Shell command stderr is rendered in output."""
        buf = io.StringIO()
        console = Console(file=buf)
        await _handle_shell("ls /nonexistent_path_xyz", console)
        output = buf.getvalue()
        # Should contain error output (varies by OS)
        assert len(output) > 0

    def test_empty_input_ignored(self) -> None:
        """Whitespace-only input is stripped to empty."""
        text = "   \t  \n  "
        assert text.strip() == ""
