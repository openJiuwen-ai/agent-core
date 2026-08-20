# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Cancel cleanup must preserve the user query for the next turn."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openjiuwen.core.foundation.llm import AssistantMessage, ToolMessage, UserMessage
from openjiuwen.core.foundation.llm.schema.tool_call import ToolCall
from openjiuwen.core.single_agent.agents.react_agent import ReActAgent, ReActAgentConfig
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext
from openjiuwen.core.single_agent.schema.agent_card import AgentCard

from tests.unit_tests.fixtures.mock_llm import MockLLMModel


def _tool_call(call_id: str, name: str = "search") -> ToolCall:
    return ToolCall(id=call_id, type="function", name=name, arguments="{}")


class _EmptyAsyncIterator:
    def __aiter__(self):
        return self

    async def __anext__(self):
        raise StopAsyncIteration


class _FailingAsyncIterator:
    def __init__(self, message: str):
        self.message = message

    def __aiter__(self):
        return self

    async def __anext__(self):
        raise RuntimeError(self.message)


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
    session.commit.assert_called_once()


@pytest.mark.asyncio
async def test_unexpected_exception_saves_cleaned_context_to_external_session():
    """Unexpected model failures must preserve the current turn and checkpoint it."""
    card = AgentCard(name="error_agent", description="error save test")
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

    mock_llm = MockLLMModel()
    mock_llm.invoke = AsyncMock(side_effect=RuntimeError("insufficient_quota"))

    session = MagicMock()
    session.get_state.return_value = None
    session.write_stream = AsyncMock()

    with patch.object(agent, "_get_llm", return_value=mock_llm):
        with pytest.raises(RuntimeError, match="insufficient_quota"):
            await agent.invoke(
                {"conversation_id": "sess", "query": "查一下热搜"},
                session=session,
            )

    kept = mock_context.set_messages.call_args.args[0]
    assert any(isinstance(m, UserMessage) and m.content == "查一下热搜" for m in kept)
    assert any(
        isinstance(m, AssistantMessage)
        and "unexpected error" in m.content
        for m in kept
    )
    mock_context_engine.save_contexts.assert_awaited()
    session.commit.assert_called_once()


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
async def test_stream_unexpected_exception_saves_context_and_emits_error():
    """A stream-side model failure must checkpoint the cleaned current turn."""
    agent = ReActAgent(card=AgentCard(name="stream_error_agent", description="stream error save test"))
    user_msg = UserMessage(content="查一下热搜")
    context = MagicMock()
    context.get_messages = MagicMock(return_value=[user_msg])
    context.set_messages = MagicMock()
    agent.context_engine = MagicMock()
    agent.context_engine.get_context = MagicMock(return_value=context)
    agent.context_engine.save_contexts = AsyncMock()
    agent.is_agent_session = False
    agent.invoke = AsyncMock(side_effect=RuntimeError("insufficient_quota"))
    session = MagicMock()
    session.write_stream = AsyncMock()

    async for _ in agent._inner_stream(
            session=session,
            inputs={"query": "查一下热搜"},
            need_cleanup=False,
    ):
        pass

    kept = context.set_messages.call_args.args[0]
    assert any(isinstance(m, UserMessage) and m.content == "查一下热搜" for m in kept)
    assert session.write_stream.await_count == 1
    error_schema = session.write_stream.call_args.args[0]
    assert error_schema.payload["result_type"] == "error"
    assert "insufficient_quota" in error_schema.payload["output"]
    agent.context_engine.save_contexts.assert_awaited_once_with(session)
    # This stream was explicitly marked as workflow-owned, so the agent must
    # not commit the caller's session in the abort path.
    session.commit.assert_not_called()


@pytest.mark.asyncio
async def test_stream_abort_commits_owned_agent_session_once():
    """An owned agent-session abort commits once and skips finally duplication."""
    agent = ReActAgent(card=AgentCard(name="owned_stream_agent", description="stream commit ownership"))
    agent.context_engine = MagicMock()
    agent.context_engine.save_contexts = AsyncMock()
    agent.is_agent_session = True
    agent.invoke = AsyncMock(side_effect=RuntimeError("insufficient_quota"))
    session = MagicMock()
    session.write_stream = AsyncMock()
    session.close_stream = AsyncMock()

    async for _ in agent._inner_stream(
            session=session,
            inputs={"query": "查一下热搜"},
            need_cleanup=True,
    ):
        pass

    agent.context_engine.save_contexts.assert_awaited_once_with(session)
    session.commit.assert_called_once()
    session.close_stream.assert_called_once()


@pytest.mark.asyncio
async def test_real_stream_exception_persists_and_commits_once():
    """The stream wrapper owns lifecycle persistence for the real invoke path."""
    agent = ReActAgent(card=AgentCard(name="real_stream_agent", description="real stream lifecycle"))
    agent.configure(ReActAgentConfig().configure_model("gpt-4").configure_max_iterations(3))

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
    agent.context_engine = mock_context_engine

    mock_llm = MockLLMModel()
    mock_llm.stream = MagicMock(return_value=_FailingAsyncIterator("insufficient_quota"))

    session = MagicMock()
    session.get_session_id.return_value = "sess"
    session.get_state.return_value = None
    session.write_stream = AsyncMock()
    session.stream_iterator.return_value = _EmptyAsyncIterator()
    session.close_stream = AsyncMock()
    session.commit = AsyncMock()
    agent.is_agent_session = True

    with patch.object(agent, "_get_llm", return_value=mock_llm):
        async for _ in agent._inner_stream(
                session=session,
                inputs={"query": "查一下热搜"},
                need_cleanup=True,
        ):
            pass

    mock_context_engine.save_contexts.assert_awaited_once_with(session)
    session.commit.assert_awaited_once_with()
    session.close_stream.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_model_exception_recovery_uses_instance_hook():
    """An explicitly installed context recovery hook must be invoked."""
    agent = ReActAgent(card=AgentCard(name="instance_hook_agent", description="instance recovery hook"))
    context = MagicMock()
    context.context_id.return_value = "context"
    session = MagicMock()
    ctx = AgentCallbackContext(agent=agent, session=session)
    recovery = AsyncMock(return_value=True)
    agent.context_engine = MagicMock()
    agent.context_engine.recover_from_model_exception = recovery

    recovered = await agent._recover_from_model_exception(
        ctx,
        context=context,
        exception=RuntimeError("context length exceeded"),
    )

    assert recovered is True
    recovery.assert_awaited_once()


@pytest.mark.asyncio
async def test_model_exception_recovery_hook_retries_the_same_model_step_once():
    """A future context recovery hook can mutate context and request one retry."""
    agent = ReActAgent(card=AgentCard(name="recovery_hook_agent", description="recovery hook"))
    context = MagicMock()
    context.context_id.return_value = "context"
    context.get_messages.return_value = []
    session = MagicMock()
    ctx = AgentCallbackContext(agent=agent, session=session)
    recovery = AsyncMock(return_value=True)
    agent._recover_from_model_exception = recovery
    agent._railed_model_call = AsyncMock(
        side_effect=[
            RuntimeError("context length exceeded"),
            AssistantMessage(content="重试成功"),
        ]
    )

    result = await agent._call_model(ctx, context, tools=None)

    assert result.content == "重试成功"
    assert agent._railed_model_call.await_count == 2
    recovery.assert_awaited_once()
    assert ctx.extra["_model_exception_recovery_attempted"] is True


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


@pytest.mark.asyncio
async def test_save_contexts_on_cancel_keeps_legacy_cancel_swallowing_contract():
    """The compatibility wrapper must absorb cancellation from its delegate."""
    agent = ReActAgent(card=AgentCard(name="cancel_wrapper_agent", description="cancel wrapper"))
    session = MagicMock()
    agent._persist_context_after_abort = AsyncMock(side_effect=asyncio.CancelledError())

    await agent._save_contexts_on_cancel(session)

    agent._persist_context_after_abort.assert_awaited_once_with(session)
