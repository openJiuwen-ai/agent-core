# coding: utf-8
import copy
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from openjiuwen.core.foundation.llm.schema.message import AssistantMessage, ToolMessage, UserMessage
from openjiuwen.core.foundation.llm.schema.tool_call import ToolCall
from openjiuwen.core.single_agent.agents.react_agent import ReActAgent
from openjiuwen.core.single_agent.interrupt.handler import (
    ToolInterruptHandler,
    ResumeContext,
    RESUME_USER_INPUT_KEY,
    RESUME_START_ITERATION_KEY,
)
from openjiuwen.core.single_agent.interrupt.state import (
    ToolInterruptionState,
    ToolInterruptEntry,
)


def _build_reload_tool_call(tool_call_id: str) -> ToolCall:
    return ToolCall(
        id=tool_call_id,
        type="function",
        name="reload_original_context_messages",
        arguments='{"offload_handle": "h1", "offload_type": "in_memory"}',
    )


def _build_state(tool_call_id: str = "reload1") -> ToolInterruptionState:
    return ToolInterruptionState(
        ai_message=AssistantMessage(content=""),
        iteration=0,
        interrupted_tools={
            tool_call_id: ToolInterruptEntry(
                tool_call=_build_reload_tool_call(tool_call_id),
                interrupt_requests={},
                is_sub_agent=False,
            ),
        },
    )


def _build_context_with_tool_result(tool_call_id: str) -> MagicMock:
    context = MagicMock()
    context.get_messages.return_value = [
        ToolMessage(content="reload messages with handle=h1:\nmessage 1: ...", tool_call_id=tool_call_id),
        UserMessage(content="continue"),
    ]
    context.add_messages = AsyncMock()
    return context


def _build_context_without_tool_result() -> MagicMock:
    context = MagicMock()
    context.get_messages.return_value = [
        UserMessage(content="hello"),
    ]
    context.add_messages = AsyncMock()
    return context


def _build_context_with_error() -> MagicMock:
    context = MagicMock()
    context.get_messages.side_effect = Exception("get_messages failed")
    context.add_messages = AsyncMock()
    return context


@pytest.mark.asyncio
async def test_skip_reload_when_result_already_committed():
    """Context has a ToolMessage with matching tool_call_id → skip reload,
    add synthetic ToolMessage."""
    handler = ToolInterruptHandler(agent=MagicMock())
    state = _build_state(tool_call_id="reload1")
    context = _build_context_with_tool_result(tool_call_id="reload1")
    ctx = MagicMock()
    ctx.extra = {RESUME_USER_INPUT_KEY: "hello"}
    resume_ctx = ResumeContext(
        state=state,
        user_input="hello",
        ctx=ctx,
        context=context,
        session=MagicMock(),
        execute_tool_call=None,
    )

    result = await handler.handle_resume(resume_ctx)

    assert result is None
    context.add_messages.assert_called_once()
    call_args, _ = context.add_messages.call_args
    added_msg = call_args[0]
    assert isinstance(added_msg, ToolMessage)
    assert added_msg.tool_call_id == "reload1"
    assert "skipped" in (added_msg.content or "")
    assert ctx.extra[RESUME_START_ITERATION_KEY] == 1


@pytest.mark.asyncio
async def test_execute_reload_when_result_missing():
    """Context has no ToolMessage with matching tool_call_id → execute reload
    to provide offloaded content."""
    handler = ToolInterruptHandler(agent=MagicMock())
    state = _build_state(tool_call_id="reload1")
    context = _build_context_without_tool_result()
    ctx = MagicMock()
    ctx.extra = {RESUME_USER_INPUT_KEY: "hello"}
    execute_mock = AsyncMock(return_value=[])
    resume_ctx = ResumeContext(
        state=state,
        user_input="hello",
        ctx=ctx,
        context=context,
        session=MagicMock(),
        execute_tool_call=execute_mock,
    )

    with patch(
        "openjiuwen.core.single_agent.interrupt.handler.AbilityManager"
    ) as mock_ability_manager:
        mock_ability_manager.tool_batch_scope.return_value.__aenter__ = AsyncMock()
        mock_ability_manager.tool_batch_scope.return_value.__aexit__ = AsyncMock()

        result = await handler.handle_resume(resume_ctx)

    assert result is None
    execute_mock.assert_called_once()
    _, call_kwargs = execute_mock.call_args
    executed_tools = call_kwargs.get("tools_to_execute") if "tools_to_execute" in call_kwargs \
        else execute_mock.call_args[0][1]
    assert len(executed_tools) == 1
    assert executed_tools[0].name == "reload_original_context_messages"
    context.add_messages.assert_not_called()
    assert ctx.extra[RESUME_START_ITERATION_KEY] == 1


@pytest.mark.asyncio
async def test_execute_reload_when_get_messages_raises():
    """context.get_messages() raises → degrade to execute reload."""
    handler = ToolInterruptHandler(agent=MagicMock())
    state = _build_state(tool_call_id="reload1")
    context = _build_context_with_error()
    ctx = MagicMock()
    ctx.extra = {RESUME_USER_INPUT_KEY: "hello"}
    execute_mock = AsyncMock(return_value=[])
    resume_ctx = ResumeContext(
        state=state,
        user_input="hello",
        ctx=ctx,
        context=context,
        session=MagicMock(),
        execute_tool_call=execute_mock,
    )

    with patch(
        "openjiuwen.core.single_agent.interrupt.handler.AbilityManager"
    ) as mock_ability_manager:
        mock_ability_manager.tool_batch_scope.return_value.__aenter__ = AsyncMock()
        mock_ability_manager.tool_batch_scope.return_value.__aexit__ = AsyncMock()

        result = await handler.handle_resume(resume_ctx)

    assert result is None
    execute_mock.assert_called_once()
    context.add_messages.assert_not_called()
    assert ctx.extra[RESUME_START_ITERATION_KEY] == 1


def test_get_agent_tag_returns_card_id():
    """_get_agent_tag() derives the tag from agent.card.id."""
    agent = ReActAgent.__new__(ReActAgent)
    agent.card = MagicMock()
    agent.card.id = "agent_resume_test"
    assert agent._get_agent_tag() == "agent_resume_test"

    agent2 = ReActAgent.__new__(ReActAgent)
    assert agent2._get_agent_tag() == ""


@pytest.mark.asyncio
async def test_executor_created_with_tag_on_resume():
    """When _execute_tool_call creates a fresh StreamingToolExecutor
    (executor=None path), it passes tag=agent.card.id to the constructor."""
    EXPECTED_TAG = "agent_resume_test"

    agent = ReActAgent.__new__(ReActAgent)
    agent.card = MagicMock()
    agent.card.id = EXPECTED_TAG
    agent.ability_manager = MagicMock()
    agent.ability_manager.execute_single = AsyncMock()

    ctx = MagicMock()
    ctx.extra = {}

    tool_call = ToolCall(
        id="t1", type="function",
        name="reload_original_context_messages",
        arguments='{"offload_handle":"h1","offload_type":"in_memory"}',
    )

    with patch(
        "openjiuwen.core.single_agent.agents.react_agent.StreamingToolExecutor"
    ) as mock_exec_cls:
        mock_exec = MagicMock()
        mock_exec_cls.return_value = mock_exec
        mock_exec.is_added.return_value = False
        mock_exec.wait_all = AsyncMock(return_value=[])

        with patch.object(
            agent, "_consume_streaming_executor_results", return_value=[],
        ):
            await agent._execute_tool_call(
                ctx=ctx,
                tool_calls=[tool_call],
                session=MagicMock(),
                context=MagicMock(),
            )

    mock_exec_cls.assert_called_once()
    _, exec_kwargs = mock_exec_cls.call_args
    assert exec_kwargs.get("tag") == EXPECTED_TAG, (
        f"StreamingToolExecutor tag={exec_kwargs.get('tag')!r}, "
        f"expected {EXPECTED_TAG!r}"
    )
