# coding: utf-8

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from openjiuwen.core.context_engine.base import ContextWindow
from openjiuwen.core.context_engine.processor.compressor.forked.current import (
    ForkedCurrentRoundCompressor,
    ForkedCurrentRoundCompressorConfig,
)
from openjiuwen.core.context_engine.processor.compressor.forked.dialogue import (
    ForkedDialogueCompressor,
    ForkedDialogueCompressorConfig,
)
from openjiuwen.core.context_engine.processor.compressor.forked.executor import ForkedCompressionResult
from openjiuwen.core.context_engine.processor.compressor.forked.round import (
    ForkedRoundLevelCompressor,
    ForkedRoundLevelCompressorConfig,
)
from openjiuwen.core.foundation.llm import AssistantMessage, ToolCall, ToolMessage, UserMessage


def _tool_call(call_id: str, name: str, arguments: str) -> ToolCall:
    return ToolCall(id=call_id, name=name, type="function", arguments=arguments)


def _context(session_state: dict, *, workspace_root: str = "", session_id: str = "session-1"):
    session = MagicMock()
    session.get_state.return_value = session_state
    session.get_session_id.return_value = session_id
    context = MagicMock()
    context.get_session_ref.return_value = session
    context.session_id.return_value = session_id
    context.token_counter.return_value = None
    context.workspace_dir.return_value = workspace_root
    return context


def _context_window(messages):
    return ContextWindow(system_messages=[], context_messages=messages, tools=[])


def _attach_executor(compressor, summary: str = "compact summary"):
    executor = MagicMock()
    executor.invoke = AsyncMock(return_value=ForkedCompressionResult(AssistantMessage(content=summary)))
    compressor._forked_executor = executor
    return executor


def _write_todo_file(workspace_root, session_id: str = "session-1"):
    session_dir = workspace_root / session_id
    session_dir.mkdir()
    (session_dir / "todo.json").write_text(
        json.dumps([{"id": "todo-1", "content": "Run tests", "status": "pending"}]),
        encoding="utf-8",
    )


@pytest.mark.asyncio
async def test_forked_compression_stores_only_state_snapshot_block():
    compressor = ForkedDialogueCompressor(ForkedDialogueCompressorConfig())
    _attach_executor(
        compressor,
        "\n".join(
            [
                "<coverage_check>",
                "internal audit should not be persisted",
                "</coverage_check>",
                "<state_snapshot>",
                "durable state only",
                "</state_snapshot>",
            ]
        ),
    )
    context = _context({})
    window = _context_window(
        [
            UserMessage(content="Historical request"),
            AssistantMessage(content="historical padding " * 600),
            UserMessage(content="Current task"),
        ]
    )

    event, updated_window = await compressor.on_get_context_window(context, window)

    assert event is not None
    memory = updated_window.context_messages[0]
    assert isinstance(memory, UserMessage)
    assert "durable state only" in memory.content
    assert "coverage_check" not in memory.content
    assert "internal audit should not be persisted" not in memory.content
    assert "state_snapshot" not in memory.content


@pytest.mark.asyncio
async def test_forked_compression_falls_back_to_raw_response_without_state_snapshot():
    compressor = ForkedDialogueCompressor(ForkedDialogueCompressorConfig())
    _attach_executor(compressor, "raw compact state")
    context = _context({})
    window = _context_window(
        [
            UserMessage(content="Historical request"),
            AssistantMessage(content="historical padding " * 600),
            UserMessage(content="Current task"),
        ]
    )

    event, updated_window = await compressor.on_get_context_window(context, window)

    assert event is not None
    assert "raw compact state" in updated_window.context_messages[0].content


@pytest.mark.asyncio
async def test_forked_current_reinjects_current_execution_state_after_compression(tmp_path):
    compressor = ForkedCurrentRoundCompressor(ForkedCurrentRoundCompressorConfig(keep_recent_messages=1))
    _attach_executor(compressor)
    _write_todo_file(tmp_path)
    context = _context(
        {
            "plan_mode": {"mode": "plan", "plan_slug": "active"},
            "background_tasks": [{"task_id": "bg-1", "description": "verify", "status": "running"}],
        },
        workspace_root=str(tmp_path),
    )
    window = _context_window(
        [
            UserMessage(content="Implement reinjection"),
            AssistantMessage(content="work " * 200),
            AssistantMessage(content="protected tail"),
        ]
    )

    event, updated_window = await compressor.on_get_context_window(context, window)

    assert event is not None
    reinjected = updated_window.context_messages[2]
    assert isinstance(reinjected, UserMessage)
    assert reinjected.content.startswith("[STATE_REINJECTION]\n[REINJECTED_STATE]")
    assert "[PLAN_MODE]" in reinjected.content
    assert "[TASK_STATUS]" in reinjected.content
    assert "[TODO]" in reinjected.content
    assert "Run tests" in reinjected.content
    assert "[TOOL_RESULT_HINT]" not in reinjected.content
    assert updated_window.context_messages[-1].content == "protected tail"


@pytest.mark.asyncio
async def test_forked_dialogue_reinjects_only_external_materials_after_compression():
    compressor = ForkedDialogueCompressor(ForkedDialogueCompressorConfig())
    _attach_executor(compressor)
    context = _context(
        {
            "plan_mode": {"mode": "plan", "plan_slug": "active"},
            "background_tasks": [{"task_id": "bg-1", "description": "verify", "status": "running"}],
            "todos": [{"id": "todo-1", "content": "Run tests", "status": "pending"}],
        }
    )
    window = _context_window(
        [
            UserMessage(content="Read file"),
            AssistantMessage(
                content="",
                tool_calls=[
                    _tool_call(
                        "tc-file",
                        "read_file",
                        '{"file_path": "/repo/src/app.py", "offset": 1, "limit": 20}',
                    )
                ],
            ),
            ToolMessage(
                content='{"content":"def main():\\n    return 1\\n","file_path":"/repo/src/app.py","line_count":2}',
                tool_call_id="tc-file",
            ),
            AssistantMessage(content="historical padding " * 600),
            UserMessage(content="Current task"),
        ]
    )

    event, updated_window = await compressor.on_get_context_window(context, window)

    assert event is not None
    reinjected = updated_window.context_messages[1]
    assert isinstance(reinjected, UserMessage)
    assert "[READ_FILE]" in reinjected.content
    assert "Recently read file: /repo/src/app.py" in reinjected.content
    assert "[PLAN_MODE]" not in reinjected.content
    assert "[PLAN]" not in reinjected.content
    assert "[TASK_STATUS]" not in reinjected.content
    assert "[TODO]" not in reinjected.content
    assert "[TOOL_RESULT_HINT]" not in reinjected.content
    assert updated_window.context_messages[-1].content == "Current task"


@pytest.mark.asyncio
async def test_forked_round_reinjects_all_state_except_tool_result_hint_after_compression(tmp_path):
    compressor = ForkedRoundLevelCompressor(ForkedRoundLevelCompressorConfig(keep_recent_messages=1))
    _attach_executor(compressor)
    _write_todo_file(tmp_path)
    context = _context(
        {
            "plan_mode": {"mode": "plan", "plan_slug": "active"},
            "background_tasks": [{"task_id": "bg-1", "description": "verify", "status": "running"}],
        },
        workspace_root=str(tmp_path),
    )
    window = _context_window(
        [
            UserMessage(content="Original request"),
            AssistantMessage(content="work " * 200),
            AssistantMessage(content="protected tail"),
        ]
    )

    event, updated_window = await compressor.on_get_context_window(context, window)

    assert event is not None
    reinjected = updated_window.context_messages[1]
    assert isinstance(reinjected, UserMessage)
    assert "[PLAN_MODE]" in reinjected.content
    assert "[TASK_STATUS]" in reinjected.content
    assert "[TODO]" in reinjected.content
    assert "Run tests" in reinjected.content
    assert "[TOOL_RESULT_HINT]" not in reinjected.content
    assert updated_window.context_messages[-1].content == "protected tail"
