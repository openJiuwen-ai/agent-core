# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Cancel cleanup must preserve the user query for the next turn."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openjiuwen.core.foundation.llm import AssistantMessage, ToolMessage, UserMessage
from openjiuwen.core.foundation.llm.schema.tool_call import ToolCall
from openjiuwen.core.single_agent.agents.react_agent import ReActAgent, ReActAgentConfig
from openjiuwen.core.single_agent.schema.agent_card import AgentCard

from tests.unit_tests.fixtures.mock_llm import MockLLMModel


def _tool_call(call_id: str, name: str = "search") -> ToolCall:
    return ToolCall(id=call_id, type="function", name=name, arguments="{}")


def test_sanitize_keeps_user_message_and_adds_cancel_marker():
    messages = [UserMessage(content="帮我查询热搜前50")]
    kept = ReActAgent._sanitize_cancelled_turn_messages(messages)

    assert len(kept) == 2
    assert isinstance(kept[0], UserMessage)
    assert kept[0].content == "帮我查询热搜前50"
    assert isinstance(kept[1], AssistantMessage)
    assert "cancelled" in kept[1].content.lower()


def test_sanitize_drops_incomplete_tool_block_but_keeps_user():
    messages = [
        UserMessage(content="查热搜"),
        AssistantMessage(content="", tool_calls=[_tool_call("tc1")]),
        # missing ToolMessage for tc1
    ]
    kept = ReActAgent._sanitize_cancelled_turn_messages(messages)

    assert isinstance(kept[0], UserMessage)
    assert kept[0].content == "查热搜"
    assert isinstance(kept[1], AssistantMessage)
    assert kept[1].tool_calls is None
    assert "cancelled" in kept[1].content.lower()


def test_sanitize_keeps_completed_tool_pair():
    messages = [
        UserMessage(content="查热搜"),
        AssistantMessage(content="", tool_calls=[_tool_call("tc1")]),
        ToolMessage(content="ok", tool_call_id="tc1"),
        AssistantMessage(content="这是部分回答"),
    ]
    kept = ReActAgent._sanitize_cancelled_turn_messages(messages)

    assert len(kept) == 4
    assert isinstance(kept[0], UserMessage)
    assert isinstance(kept[1], AssistantMessage) and kept[1].tool_calls
    assert isinstance(kept[2], ToolMessage)
    assert isinstance(kept[3], AssistantMessage)
    assert kept[3].content == "这是部分回答"


@pytest.mark.asyncio
async def test_cancel_saves_cleaned_context_to_external_session():
    """Cancelled turns must persist the cleaned context to session state.

    With an external (long-lived) session, ``need_cleanup`` is False, so the
    ``finally`` block never saves. Without an explicit save in the cancel
    branch, the next turn's ``create_context`` rebuilds the message buffer
    from the pre-cancel snapshot in ``session.state`` and drops the user
    query (context rollback).
    """
    card = AgentCard(name="cancel_agent", description="cancel save test")
    config = ReActAgentConfig().configure_model("gpt-4").configure_max_iterations(3)

    user_msg = UserMessage(content="查一下热搜")
    mock_context_window = MagicMock(
        get_messages=MagicMock(return_value=[]),
        get_tools=MagicMock(return_value=None),
    )
    mock_context = MagicMock()
    mock_context.add_messages = AsyncMock()
    mock_context.get_context_window = AsyncMock(return_value=mock_context_window)
    mock_context.get_messages = MagicMock(return_value=[user_msg])
    mock_context.set_messages = MagicMock()

    mock_context_engine = MagicMock()
    mock_context_engine.create_context = AsyncMock(return_value=mock_context)
    mock_context_engine.get_context = MagicMock(return_value=mock_context)
    mock_context_engine.save_contexts = AsyncMock()

    agent = ReActAgent(card=card)
    agent.configure(config)
    agent.context_engine = mock_context_engine

    # 模拟外部取消：LLM 调用点抛出 CancelledError，与外部 cancel 注入路径一致。
    mock_llm = MockLLMModel()
    mock_llm.invoke = AsyncMock(side_effect=asyncio.CancelledError())

    session = MagicMock()
    session.get_state.return_value = None
    session.write_stream = AsyncMock()

    with patch.object(agent, "_get_llm", return_value=mock_llm):
        with pytest.raises(asyncio.CancelledError):
            await agent.invoke(
                {"conversation_id": "sess", "query": "查一下热搜"},
                session=session,
            )

    # 清理保留了 UserMessage（并补了 cancelled 标记）
    kept = mock_context.set_messages.call_args.args[0]
    assert any(isinstance(m, UserMessage) and m.content == "查一下热搜" for m in kept)
    # 关键回归断言：即使 need_cleanup=False，取消后也要把清理后的 context
    # 写回 session state，否则下一轮会被旧快照 rebuild 覆盖。
    mock_context_engine.save_contexts.assert_awaited()


@pytest.mark.asyncio
async def test_stream_cancel_saves_context_to_external_session():
    """The stream wrapper must also persist context when cancellation escapes invoke."""
    agent = ReActAgent(card=AgentCard(name="stream_cancel_agent", description="stream cancel save test"))
    agent.context_engine = MagicMock()
    agent.context_engine.save_contexts = AsyncMock()
    agent.is_agent_session = False
    agent.invoke = AsyncMock(side_effect=asyncio.CancelledError())
    session = MagicMock()

    with pytest.raises(asyncio.CancelledError):
        async for _ in agent._inner_stream(session=session, inputs={"query": "查一下热搜"}, need_cleanup=False):
            pass

    agent.context_engine.save_contexts.assert_awaited_once_with(session)


@pytest.mark.asyncio
async def test_shielded_context_save_continues_after_repeated_cancel():
    """A repeated caller cancellation must not cancel the context save itself."""
    save_started = asyncio.Event()
    allow_save_to_finish = asyncio.Event()
    save_finished = asyncio.Event()

    async def save_contexts(_session):
        save_started.set()
        await allow_save_to_finish.wait()
        save_finished.set()

    agent = ReActAgent(card=AgentCard(name="shield_agent", description="shield save test"))
    agent.context_engine = MagicMock()
    agent.context_engine.save_contexts = AsyncMock(side_effect=save_contexts)
    session = MagicMock()

    save_task = asyncio.create_task(agent._save_contexts_on_cancel(session))
    await save_started.wait()
    save_task.cancel()
    await save_task

    allow_save_to_finish.set()
    await asyncio.wait_for(save_finished.wait(), timeout=1)
