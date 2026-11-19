"""A host wraps the real invocation, without altering the unbound SDK path."""

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openjiuwen.core.foundation.tool.execution_context import bind_tool_execution_context, invoke_with_context


@pytest.mark.asyncio
async def test_unbound_call_preserves_inputs_identity_and_keywords():
    tool = SimpleNamespace(invoke=AsyncMock(return_value="ok"))
    inputs, session = {"tool_call_id": "untrusted"}, object()
    assert await invoke_with_context(tool, inputs, session=session, tool_call_id="actual", tool_name="bash") == "ok"
    tool.invoke.assert_awaited_once_with(inputs, session=session, tool_call_id="actual")


@pytest.mark.asyncio
async def test_context_survives_child_tasks_and_cleans_up_on_error():
    events = []

    @asynccontextmanager
    async def factory(**identity):
        events.append(identity)
        try:
            yield
        finally:
            events.append("closed")

    tool = SimpleNamespace(invoke=AsyncMock(side_effect=ValueError("tool error")))
    with bind_tool_execution_context(factory):
        with pytest.raises(ValueError, match="tool error"):
            await asyncio.create_task(invoke_with_context(tool, {}, session="s", tool_call_id="c", tool_name="bash"))
    assert events == [{"tool_name": "bash", "inputs": {}, "session": "s", "tool_call_id": "c"}, "closed"]
    tool.invoke = AsyncMock(return_value="normal")
    assert await invoke_with_context(tool, {}, session="s", tool_call_id="next", tool_name="bash") == "normal"
    assert len(events) == 2
