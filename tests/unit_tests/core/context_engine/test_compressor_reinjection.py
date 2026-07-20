# coding: utf-8

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from openjiuwen.core.context_engine.base import ContextWindow
from openjiuwen.core.context_engine.processor.forked.compressor.current_round_compressor import (
    CurrentRoundCompressor,
    CurrentRoundCompressorConfig,
)
from openjiuwen.core.context_engine.processor.forked.compressor.dialogue_compressor import (
    DialogueCompressor,
    DialogueCompressorConfig,
)
from openjiuwen.core.context_engine.processor.forked.compressor.support.compression_executor import (
    CompressionError,
    CompressionErrorKind,
    CompressionResult,
)
from openjiuwen.core.context_engine.processor.forked.compressor.round_level_compressor import (
    RoundLevelCompressor,
    RoundLevelCompressorConfig,
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
    executor.invoke = AsyncMock(return_value=CompressionResult(AssistantMessage(content=summary)))
    compressor._compression_executor = executor
    return executor


def _compression_error(kind: CompressionErrorKind) -> CompressionError:
    return CompressionError(
        kind=kind,
        message=kind.value,
        original_error=RuntimeError(kind.value),
    )


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
async def test_compression_stores_only_state_snapshot_block():
    compressor = DialogueCompressor(DialogueCompressorConfig())
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
async def test_compression_falls_back_to_raw_response_without_state_snapshot():
    compressor = DialogueCompressor(DialogueCompressorConfig())
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
async def test_current_reinjects_current_execution_state_after_compression(tmp_path):
    compressor = CurrentRoundCompressor(CurrentRoundCompressorConfig(keep_recent_messages=1))
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
    assert reinjected.content.startswith("<recovered_context>\n<instruction>")
    assert '<section name="plan_mode">' in reinjected.content
    assert '<section name="plan">' in reinjected.content
    assert "Active Plan" in reinjected.content
    assert '<section name="task_status">' in reinjected.content
    assert '<section name="todo">' in reinjected.content
    assert "Run tests" in reinjected.content
    assert "tool_result_hint" not in reinjected.content
    assert updated_window.context_messages[-1].content == "protected tail"


@pytest.mark.asyncio
async def test_dialogue_reinjects_only_external_materials_after_compression():
    compressor = DialogueCompressor(DialogueCompressorConfig())
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
    # Dialogue reinjects supporting context from earlier dialogue when relevant.
    assert '<section name="read_file">' in reinjected.content
    assert "Recently read file: /repo/src/app.py" in reinjected.content
    assert "Partial read: false" not in reinjected.content
    # DialogueCompressor.reinject_builder_names is
    # ["skills", "read_file", "plan_mode", "plan"], so plan_mode is emitted
    # here. plan is not, because this test does not stage a plan file
    # (workspace_root is empty) and build_plan_reinjected_content returns "".
    # Dialogue does not carry task/todo state, and never emits the tool-result
    # hint section.
    assert '<section name="plan_mode">' in reinjected.content
    assert '<section name="plan">' not in reinjected.content
    assert '<section name="task_status">' not in reinjected.content
    assert '<section name="todo">' not in reinjected.content
    assert "tool_result_hint" not in reinjected.content
    assert updated_window.context_messages[-1].content == "Current task"


@pytest.mark.asyncio
async def test_dialogue_uses_real_user_message_boundary():
    compressor = DialogueCompressor(DialogueCompressorConfig())
    _attach_executor(compressor, "historical compact state")
    context = _context({})
    window = _context_window(
        [
            UserMessage(content="Historical request"),
            AssistantMessage(content="historical padding " * 600),
            UserMessage(content='你收到一条消息： {"type": "user input", "content": "Current task"}'),
            UserMessage(content="<memory_block_current>\ncurrent task snapshot\n</memory_block_current>"),
            UserMessage(content='<recovered_context>\n<section name="plan_mode">active</section>\n</recovered_context>'),
            UserMessage(content="<system-reminder>runtime state</system-reminder>"),
        ]
    )

    event, updated_window = await compressor.on_get_context_window(context, window)

    assert event is not None
    contents = [message.content for message in updated_window.context_messages]
    assert "historical compact state" in contents[0]
    assert contents[1].startswith('你收到一条消息： {"type": "user input"')
    assert contents[2].startswith("<memory_block_current>")
    assert contents[3].startswith("<recovered_context>")
    assert contents[4].startswith("<system-reminder>")


@pytest.mark.asyncio
async def test_dialogue_skips_when_target_is_below_min_context_ratio():
    compressor = DialogueCompressor(DialogueCompressorConfig())
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
async def test_current_compresses_internal_user_messages_after_real_user_boundary():
    compressor = CurrentRoundCompressor(CurrentRoundCompressorConfig(keep_recent_messages=0))
    _attach_executor(compressor, "current compact state")
    context = _context({})
    window = _context_window(
        [
            UserMessage(content="Historical request"),
            AssistantMessage(content="Historical answer"),
            UserMessage(content='你收到一条消息： {"type": "user input", "content": "Current task"}'),
            UserMessage(content="<memory_block_current>\n" + ("old current snapshot " * 600) + "\n</memory_block_current>"),
            UserMessage(content="<recovered_context>\n" + ("runtime state " * 200) + "\n</recovered_context>"),
            UserMessage(content="<system-reminder>" + ("runtime state " * 200) + "</system-reminder>"),
        ]
    )

    event, updated_window = await compressor.on_get_context_window(context, window)

    assert event is not None
    contents = [message.content for message in updated_window.context_messages]
    assert contents[2].startswith('你收到一条消息： {"type": "user input"')
    assert "current compact state" in contents[3]
    assert not any("old current snapshot" in content for content in contents)
    assert not any("<recovered_context>" in content for content in contents)
    assert not any("<system-reminder>" in content for content in contents)


def test_current_default_targets_tool_results_before_trailing_internal_user():
    compressor = CurrentRoundCompressor(CurrentRoundCompressorConfig())
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
async def test_round_reinjects_all_state_except_tool_result_hint_after_compression(tmp_path):
    compressor = RoundLevelCompressor(RoundLevelCompressorConfig(keep_recent_messages=1))
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
    assert '<section name="plan_mode">' in reinjected.content
    assert '<section name="plan">' in reinjected.content
    assert "Active Plan" in reinjected.content
    assert '<section name="task_status">' in reinjected.content
    assert "Team collaboration state:" in reinjected.content
    assert "- Team: verification-team" in reinjected.content
    assert "Current members:" in reinjected.content
    assert "Open tasks:" in reinjected.content
    assert "Team signals:" in reinjected.content
    assert "- Unread team messages exist." in reinjected.content
    assert "verification-team" in reinjected.content
    assert "Inspect traces" in reinjected.content
    assert "use team messaging tools" not in reinjected.content
    assert '<section name="todo">' in reinjected.content
    assert "Run tests" in reinjected.content
    assert "tool_result_hint" not in reinjected.content
    assert updated_window.context_messages[-1].content == "protected tail"


@pytest.mark.asyncio
async def test_context_overflow_retry_increases_exclude_recent_messages():
    compressor = DialogueCompressor(DialogueCompressorConfig())
    executor = MagicMock()
    executor.invoke = AsyncMock(
        side_effect=[
            _compression_error(CompressionErrorKind.CONTEXT_OVERFLOW),
            CompressionResult(AssistantMessage(content="retried compact state")),
        ]
    )
    compressor._compression_executor = executor
    context = _context({})
    window = _context_window(
        [
            UserMessage(content="Historical request"),
            AssistantMessage(content="a" * 1200),
            AssistantMessage(content="b" * 1200),
            AssistantMessage(content="c" * 1200),
            UserMessage(content="Current task"),
        ]
    )

    event, updated_window = await compressor.on_get_context_window(context, window)

    assert event is not None
    assert "retried compact state" in updated_window.context_messages[0].content
    assert executor.invoke.await_count == 2
    first_request = executor.invoke.await_args_list[0].args[0]
    second_request = executor.invoke.await_args_list[1].args[0]
    assert second_request.exclude_recent_messages > first_request.exclude_recent_messages


@pytest.mark.asyncio
async def test_context_overflow_retry_stops_after_budget_retries_without_mutating_context():
    compressor = DialogueCompressor(DialogueCompressorConfig())
    executor = MagicMock()
    executor.invoke = AsyncMock(side_effect=_compression_error(CompressionErrorKind.CONTEXT_OVERFLOW))
    compressor._compression_executor = executor
    context = _context({})
    original_messages = [
        UserMessage(content="Historical request"),
        AssistantMessage(content="a" * 120),
        AssistantMessage(content="b" * 120),
        AssistantMessage(content="c" * 120),
        UserMessage(content="Current task"),
    ]
    window = _context_window(original_messages)

    event, updated_window = await compressor.on_get_context_window(context, window)

    assert event is None
    assert updated_window.context_messages == original_messages
    assert executor.invoke.await_count == 4
    assert context.set_messages.call_count == 0


@pytest.mark.asyncio
async def test_transient_compression_error_retries_without_changing_request():
    compressor = DialogueCompressor(DialogueCompressorConfig())
    executor = MagicMock()
    executor.invoke = AsyncMock(
        side_effect=[
            _compression_error(CompressionErrorKind.TIMEOUT),
            CompressionResult(AssistantMessage(content="retried compact state")),
        ]
    )
    compressor._compression_executor = executor
    context = _context({})
    window = _context_window(
        [
            UserMessage(content="Historical request"),
            AssistantMessage(content="historical padding " * 600),
            UserMessage(content="Current task"),
        ]
    )

    event, _ = await compressor.on_get_context_window(context, window)

    assert event is not None
    assert executor.invoke.await_count == 2
    first_request = executor.invoke.await_args_list[0].args[0]
    second_request = executor.invoke.await_args_list[1].args[0]
    assert second_request.exclude_recent_messages == first_request.exclude_recent_messages


@pytest.mark.asyncio
async def test_non_retryable_compression_error_does_not_retry():
    compressor = DialogueCompressor(DialogueCompressorConfig())
    executor = MagicMock()
    executor.invoke = AsyncMock(side_effect=_compression_error(CompressionErrorKind.AUTHENTICATION))
    compressor._compression_executor = executor
    context = _context({})
    window = _context_window(
        [
            UserMessage(content="Historical request"),
            AssistantMessage(content="historical padding " * 600),
            UserMessage(content="Current task"),
        ]
    )

    event, updated_window = await compressor.on_get_context_window(context, window)

    assert event is None
    assert updated_window is window
    assert executor.invoke.await_count == 1
    assert context.set_messages.call_count == 0
