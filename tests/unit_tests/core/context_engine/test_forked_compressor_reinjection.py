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


def _write_plan_file(workspace_root, slug: str = "active"):
    plans_dir = workspace_root / ".plans"
    plans_dir.mkdir()
    (plans_dir / f"{slug}.md").write_text(
        "# Active Plan\n\n- Verify reinjection state\n",
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
    _write_plan_file(tmp_path)
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
            AssistantMessage(content="work " * 5000),
            AssistantMessage(content="protected tail"),
        ]
    )

    event, updated_window = await compressor.on_get_context_window(context, window)

    assert event is not None
    reinjected = updated_window.context_messages[2]
    assert isinstance(reinjected, UserMessage)
    assert reinjected.content.startswith("[STATE_REINJECTION]\n[REINJECTED_STATE]")
    assert "[PLAN_MODE]" in reinjected.content
    assert "[PLAN]" in reinjected.content
    assert "Active Plan" in reinjected.content
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
async def test_forked_dialogue_uses_real_user_message_boundary():
    compressor = ForkedDialogueCompressor(ForkedDialogueCompressorConfig())
    _attach_executor(compressor, "historical compact state")
    context = _context({})
    window = _context_window(
        [
            UserMessage(content="Historical request"),
            AssistantMessage(content="historical padding " * 600),
            UserMessage(content='你收到一条消息： {"type": "user input", "content": "Current task"}'),
            UserMessage(content="<memory_block_current>\ncurrent task snapshot\n</memory_block_current>"),
            UserMessage(content="[STATE_REINJECTION]\n[REINJECTED_STATE]\n[PLAN_MODE] active"),
            UserMessage(content="<system-reminder>runtime state</system-reminder>"),
        ]
    )

    event, updated_window = await compressor.on_get_context_window(context, window)

    assert event is not None
    contents = [message.content for message in updated_window.context_messages]
    assert "historical compact state" in contents[0]
    assert contents[1].startswith('你收到一条消息： {"type": "user input"')
    assert contents[2].startswith("<memory_block_current>")
    assert contents[3].startswith("[STATE_REINJECTION]")
    assert contents[4].startswith("<system-reminder>")


@pytest.mark.asyncio
async def test_forked_dialogue_skips_when_target_is_below_min_context_ratio():
    compressor = ForkedDialogueCompressor(ForkedDialogueCompressorConfig())
    _attach_executor(compressor, "historical compact state")
    context = _context({})
    window = _context_window(
        [
            UserMessage(content="tiny history"),
            UserMessage(content='你收到一条消息： {"type": "user input", "content": "Current task"}'),
            AssistantMessage(content="protected current work " * 15000),
        ]
    )

    should_run = await compressor.trigger_get_context_window(context, window)

    assert should_run is False


@pytest.mark.asyncio
async def test_forked_dialogue_uses_absolute_tokens_threshold_before_ratio_threshold():
    compressor = ForkedDialogueCompressor(
        ForkedDialogueCompressorConfig(tokens_threshold=100, min_target_context_ratio=0.0)
    )
    _attach_executor(compressor, "historical compact state")
    context = _context({})
    window = _context_window(
        [
            UserMessage(content="Historical request"),
            AssistantMessage(content="historical padding " * 60),
            UserMessage(content='你收到一条消息： {"type": "user input", "content": "Current task"}'),
        ]
    )

    should_run = await compressor.trigger_get_context_window(context, window)

    assert should_run is True


@pytest.mark.asyncio
async def test_forked_current_compresses_internal_user_messages_after_real_user_boundary():
    compressor = ForkedCurrentRoundCompressor(ForkedCurrentRoundCompressorConfig(keep_recent_messages=0))
    _attach_executor(compressor, "current compact state")
    context = _context({})
    window = _context_window(
        [
            UserMessage(content="Historical request"),
            AssistantMessage(content="Historical answer"),
            UserMessage(content='你收到一条消息： {"type": "user input", "content": "Current task"}'),
            UserMessage(content="<memory_block_current>\n" + ("old current snapshot " * 600) + "\n</memory_block_current>"),
            UserMessage(content="[STATE_REINJECTION]\n[REINJECTED_STATE]\n" + ("runtime state " * 200)),
            UserMessage(content="<system-reminder>" + ("runtime state " * 200) + "</system-reminder>"),
        ]
    )

    event, updated_window = await compressor.on_get_context_window(context, window)

    assert event is not None
    contents = [message.content for message in updated_window.context_messages]
    assert contents[2].startswith('你收到一条消息： {"type": "user input"')
    assert "current compact state" in contents[3]
    assert not any("old current snapshot" in content for content in contents)
    assert not any("[STATE_REINJECTION]" in content for content in contents)
    assert not any("<system-reminder>" in content for content in contents)


def test_forked_current_default_targets_tool_results_before_trailing_internal_user():
    compressor = ForkedCurrentRoundCompressor(ForkedCurrentRoundCompressorConfig(tokens_threshold=100000))
    window_messages = [
        UserMessage(content='你收到一条消息： {"type": "user input", "content": "Read files"}'),
        AssistantMessage(
            content="",
            tool_calls=[
                _tool_call("tc-1", "read_file", '{"file_path": "a.txt"}'),
                _tool_call("tc-2", "read_file", '{"file_path": "b.txt"}'),
            ],
        ),
        ToolMessage(content="a" * 50000, tool_call_id="tc-1"),
        ToolMessage(content="b" * 50000, tool_call_id="tc-2"),
        UserMessage(content="<system-reminder>runtime-only prompt attachment</system-reminder>"),
    ]

    span = compressor._build_span(window_messages)

    assert span.has_target
    assert span.preserved_prefix == window_messages[:1]
    assert span.messages_to_compress == window_messages[1:]
    assert span.protected_tail == []


@pytest.mark.asyncio
async def test_forked_round_reinjects_all_state_except_tool_result_hint_after_compression(tmp_path):
    compressor = ForkedRoundLevelCompressor(ForkedRoundLevelCompressorConfig(keep_recent_messages=1))
    _attach_executor(compressor)
    _write_todo_file(tmp_path)
    _write_plan_file(tmp_path)
    context = _context(
        {
            "plan_mode": {"mode": "plan", "plan_slug": "active"},
            "background_tasks": [{"task_id": "bg-1", "description": "verify", "status": "running"}],
            "team_task_status": {
                "team_name": "verification-team",
                "members": [{"member_name": "reviewer", "role": "review", "status": "running"}],
                "open_tasks": [{"task_id": "7", "title": "Inspect traces", "status": "open", "assignee": "reviewer"}],
                "has_unread_messages": True,
            },
        },
        workspace_root=str(tmp_path),
    )
    window = _context_window(
        [
            UserMessage(content="Original request"),
            AssistantMessage(content="work " * 5000),
            AssistantMessage(content="protected tail"),
        ]
    )

    event, updated_window = await compressor.on_get_context_window(context, window)

    assert event is not None
    reinjected = updated_window.context_messages[1]
    assert isinstance(reinjected, UserMessage)
    assert "[PLAN_MODE]" in reinjected.content
    assert "[PLAN]" in reinjected.content
    assert "Active Plan" in reinjected.content
    assert "[TASK_STATUS]" in reinjected.content
    assert "verification-team" in reinjected.content
    assert "Inspect traces" in reinjected.content
    assert "[TODO]" in reinjected.content
    assert "Run tests" in reinjected.content
    assert "[TOOL_RESULT_HINT]" not in reinjected.content
    assert updated_window.context_messages[-1].content == "protected tail"
