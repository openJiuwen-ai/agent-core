"""Unit tests for openjiuwen.harness.cli.rails.tool_tracker."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openjiuwen.harness.cli.rails.tool_tracker import (
    ToolTrackingRail,
)


class _FakeReadResult:
    def __init__(self, content: str, line_count: int) -> None:
        self.data = {
            "content": content,
            "line_count": line_count,
        }

    def __str__(self) -> str:
        return "success=True data={'content': 'line1\\nline2'}"


@pytest.mark.asyncio
async def test_after_tool_call_read_file_prefers_content_and_line_count() -> None:
    session = SimpleNamespace(write_stream=AsyncMock())
    ctx = SimpleNamespace(
        session=session,
        inputs=SimpleNamespace(
            tool_name="read_file",
            tool_args={"file_path": "/tmp/a.txt"},
            tool_result=_FakeReadResult(
                "     1\tline1\n     2\tline2",
                2,
            ),
        ),
    )

    await ToolTrackingRail().after_tool_call(ctx)

    session.write_stream.assert_awaited_once()
    event = session.write_stream.await_args.args[0]
    assert event.payload["tool_result"] == "     1\tline1\n     2\tline2"
    assert event.payload["line_count"] == 2


@pytest.mark.asyncio
async def test_after_tool_call_non_read_file_keeps_stringified_result() -> None:
    session = SimpleNamespace(write_stream=AsyncMock())
    ctx = SimpleNamespace(
        session=session,
        inputs=SimpleNamespace(
            tool_name="bash",
            tool_args={"command": "pwd"},
            tool_result=SimpleNamespace(stdout="/tmp"),
        ),
    )

    await ToolTrackingRail().after_tool_call(ctx)

    event = session.write_stream.await_args.args[0]
    assert "stdout='/tmp'" in event.payload["tool_result"]
    assert "line_count" not in event.payload
