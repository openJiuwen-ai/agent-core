# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Cancel cleanup must preserve the user query for the next turn."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openjiuwen.core.foundation.llm import AssistantMessage, ToolMessage, UserMessage
from openjiuwen.core.foundation.llm.schema.tool_call import ToolCall
from openjiuwen.core.runner import Runner
from openjiuwen.core.runner.callback.errors import AbortError
from openjiuwen.core.session.agent import create_agent_session
from openjiuwen.core.session.interaction.base import AgentInterrupt
from openjiuwen.core.single_agent.agents.react_agent import ReActAgent, ReActAgentConfig
from openjiuwen.core.single_agent.schema.agent_card import AgentCard

from tests.unit_tests.fixtures.mock_llm import MockLLMModel, create_text_response


def _tool_call(call_id: str, name: str = "search") -> ToolCall:
    return ToolCall(id=call_id, type="function", name=name, arguments="{}")


class _FailingAsyncIterator:
    def __aiter__(self):
        return self

    async def __anext__(self):
        raise RuntimeError("insufficient_quota")


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
    session.commit.assert_called_once_with()


@pytest.mark.asyncio
async def test_model_error_keeps_user_turn_and_commits_external_agent_session():
    agent = ReActAgent(card=AgentCard(name="error_agent", description="error save test"))
    agent.configure(ReActAgentConfig().configure_model("gpt-4").configure_max_iterations(3))
    context = MagicMock()
    context.add_messages = AsyncMock()
    context.get_context_window = AsyncMock(return_value=MagicMock(get_messages=lambda: [], get_tools=lambda: None))
    context.get_messages.return_value = [UserMessage(content="查一下热搜")]
    agent.context_engine = MagicMock()
    agent.context_engine.create_context = AsyncMock(return_value=context)
    agent.context_engine.get_context.return_value = context
    agent.context_engine.save_contexts = AsyncMock()
    llm = MockLLMModel()
    llm.invoke = AsyncMock(side_effect=RuntimeError("insufficient_quota"))
    session = MagicMock()
    session.get_state.return_value = None
    session.write_stream = AsyncMock()

    with patch.object(agent, "_get_llm", return_value=llm):
        with pytest.raises(RuntimeError, match="insufficient_quota"):
            await agent.invoke({"conversation_id": "sess", "query": "查一下热搜"}, session=session)

    kept = context.set_messages.call_args.args[0]
    assert kept[0].content == "查一下热搜"
    assert "unexpected error" in kept[-1].content
    agent.context_engine.save_contexts.assert_awaited_once_with(session)
    session.commit.assert_called_once_with()


@pytest.mark.asyncio
@pytest.mark.parametrize("control_error", [AbortError("approval required"), AgentInterrupt("approval required")])
async def test_control_flow_interrupt_does_not_mark_context_as_unexpected_error(control_error):
    agent = ReActAgent(card=AgentCard(name="interrupt_agent", description="control flow test"))
    agent.configure(ReActAgentConfig().configure_model("gpt-4").configure_max_iterations(3))
    context = MagicMock()
    context.add_messages = AsyncMock()
    context.get_context_window = AsyncMock(return_value=MagicMock(get_messages=lambda: [], get_tools=lambda: None))
    context.get_messages.return_value = [UserMessage(content="需要审批")]
    agent.context_engine = MagicMock()
    agent.context_engine.create_context = AsyncMock(return_value=context)
    agent.context_engine.get_context.return_value = context
    agent.context_engine.save_contexts = AsyncMock()
    llm = MockLLMModel()
    llm.invoke = AsyncMock(side_effect=control_error)
    session = MagicMock()
    session.get_state.return_value = None
    session.write_stream = AsyncMock()

    with patch.object(agent, "_get_llm", return_value=llm):
        with pytest.raises(type(control_error)):
            await agent.invoke({"query": "需要审批"}, session=session)

    context.set_messages.assert_not_called()
    agent.context_engine.save_contexts.assert_not_awaited()
    session.commit.assert_not_called()


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
async def test_stream_error_checkpoints_safe_context_and_emits_error():
    agent = ReActAgent(card=AgentCard(name="stream_error_agent", description="stream error save test"))
    context = MagicMock()
    context.get_messages.return_value = [UserMessage(content="查一下热搜")]
    agent.context_engine = MagicMock()
    agent.context_engine.get_context.return_value = context
    agent.context_engine.save_contexts = AsyncMock()
    agent.is_agent_session = False
    agent.invoke = AsyncMock(side_effect=RuntimeError("insufficient_quota"))
    session = MagicMock()
    session.write_stream = AsyncMock()

    async for _ in agent._inner_stream(session=session, inputs={"query": "查一下热搜"}, need_cleanup=False):
        pass

    kept = context.set_messages.call_args.args[0]
    assert kept[0].content == "查一下热搜"
    assert "unexpected error" in kept[-1].content
    agent.context_engine.save_contexts.assert_awaited_once_with(session)
    error_schema = session.write_stream.call_args.args[0]
    assert error_schema.payload["result_type"] == "error"
    assert "insufficient_quota" in error_schema.payload["output"]


@pytest.mark.asyncio
async def test_abort_still_saves_when_context_cleanup_fails():
    agent = ReActAgent(card=AgentCard(name="cleanup_error_agent", description="abort fallback"))
    agent.context_engine = MagicMock()
    agent.context_engine.get_context.side_effect = RuntimeError("context unavailable")
    agent.context_engine.save_contexts = AsyncMock()
    session = MagicMock()

    persisted = await agent._persist_context_after_abort(
        session, "[Request failed with unexpected error]"
    )

    assert persisted is True
    agent.context_engine.save_contexts.assert_awaited_once_with(session)


@pytest.mark.asyncio
async def test_workflow_owned_session_is_not_committed_on_abort():
    agent = ReActAgent(card=AgentCard(name="workflow_agent", description="workflow ownership"))
    agent.context_engine = MagicMock()
    agent.context_engine.save_contexts = AsyncMock()
    session = MagicMock(spec=["get_session_id", "get_state", "write_stream"])

    assert agent._session_supports_agent_lifecycle(session) is False
    assert await agent._persist_context_after_abort(
        session, "[Request failed with unexpected error]",
        cleanup=False,
        commit_session=agent._session_supports_agent_lifecycle(session),
    ) is True
    assert not hasattr(session, "commit")
    agent.context_engine.save_contexts.assert_awaited_once_with(session)


@pytest.mark.asyncio
async def test_commit_failure_does_not_hide_original_model_error():
    agent = ReActAgent(card=AgentCard(name="commit_error_agent", description="commit failure"))
    agent.configure(ReActAgentConfig().configure_model("gpt-4").configure_max_iterations(3))
    context = MagicMock()
    context.add_messages = AsyncMock()
    context.get_context_window = AsyncMock(return_value=MagicMock(get_messages=lambda: [], get_tools=lambda: None))
    context.get_messages.return_value = [UserMessage(content="查一下热搜")]
    agent.context_engine = MagicMock()
    agent.context_engine.create_context = AsyncMock(return_value=context)
    agent.context_engine.get_context.return_value = context
    agent.context_engine.save_contexts = AsyncMock()
    llm = MockLLMModel()
    llm.invoke = AsyncMock(side_effect=RuntimeError("insufficient_quota"))
    session = MagicMock()
    session.get_state.return_value = None
    session.write_stream = AsyncMock()
    session.commit = AsyncMock(side_effect=RuntimeError("checkpoint unavailable"))

    with patch.object(agent, "_get_llm", return_value=llm):
        with pytest.raises(RuntimeError, match="insufficient_quota"):
            await agent.invoke({"query": "查一下热搜"}, session=session)

    agent.context_engine.save_contexts.assert_awaited_once_with(session)
    session.commit.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_repeated_cancel_waits_for_one_shielded_save():
    save_started = asyncio.Event()
    finish_save = asyncio.Event()

    async def save_contexts(_session):
        save_started.set()
        await finish_save.wait()

    agent = ReActAgent(card=AgentCard(name="repeat_cancel_agent", description="single save"))
    agent.context_engine = MagicMock()
    agent.context_engine.save_contexts = AsyncMock(side_effect=save_contexts)
    session = MagicMock()
    persist_task = asyncio.create_task(agent._persist_context_after_abort(
        session, "[Request cancelled by user]", cleanup=False,
    ))
    await save_started.wait()
    persist_task.cancel()
    await asyncio.sleep(0)
    persist_task.cancel()
    finish_save.set()

    assert await asyncio.wait_for(persist_task, timeout=1) is True
    agent.context_engine.save_contexts.assert_awaited_once_with(session)


@pytest.mark.asyncio
async def test_real_session_recovers_current_turn_after_model_error():
    """A failed request keeps its user turn in the state read by the next request."""
    await Runner.start()
    try:
        card = AgentCard(name="abort_recovery_agent", description="abort recovery")
        agent = ReActAgent(card=card)
        agent.configure(ReActAgentConfig().configure_model("gpt-4").configure_max_iterations(3))
        session = create_agent_session(session_id="abort_recovery_session", card=card)
        await session.pre_run(inputs={"query": "查一下热搜"})
        llm = MockLLMModel()
        llm.invoke = AsyncMock(side_effect=RuntimeError("insufficient_quota"))

        with patch.object(agent, "_get_llm", return_value=llm):
            with pytest.raises(RuntimeError, match="insufficient_quota"):
                await agent.invoke({"query": "查一下热搜"}, session=session)

        saved = session.get_state("context")
        assert "查一下热搜" in str(saved)
        assert "unexpected error" in str(saved)

        llm.invoke = AsyncMock(return_value=create_text_response("已恢复"))
        with patch.object(agent, "_get_llm", return_value=llm):
            result = await agent.invoke({"query": "继续"}, session=session)
        assert result["result_type"] == "answer"
        assert "已恢复" in result["output"]
    finally:
        await Runner.stop()


@pytest.mark.asyncio
async def test_owned_stream_error_saves_and_commits_once():
    agent = ReActAgent(card=AgentCard(name="owned_stream_agent", description="stream lifecycle test"))
    agent.context_engine = MagicMock()
    agent.context_engine.save_contexts = AsyncMock()
    agent.is_agent_session = True
    agent.invoke = AsyncMock(side_effect=RuntimeError("insufficient_quota"))
    session = MagicMock()
    session.write_stream = AsyncMock()
    session.close_stream = AsyncMock()
    session.commit = AsyncMock()

    async def empty_stream():
        if False:
            yield None

    session.stream_iterator.return_value = empty_stream()

    async for _ in agent._inner_stream(session=session, inputs={"query": "查一下热搜"}, need_cleanup=True):
        pass

    agent.context_engine.save_contexts.assert_awaited_once_with(session)
    session.close_stream.assert_awaited_once_with()
    session.commit.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_real_stream_model_error_persists_and_commits_once():
    agent = ReActAgent(card=AgentCard(name="real_stream_agent", description="real stream lifecycle"))
    agent.configure(ReActAgentConfig().configure_model("gpt-4").configure_max_iterations(3))
    context = MagicMock()
    context.add_messages = AsyncMock()
    context.get_context_window = AsyncMock(return_value=MagicMock(get_messages=lambda: [], get_tools=lambda: None))
    context.get_messages.return_value = [UserMessage(content="查一下热搜")]
    agent.context_engine = MagicMock()
    agent.context_engine.create_context = AsyncMock(return_value=context)
    agent.context_engine.get_context.return_value = context
    agent.context_engine.save_contexts = AsyncMock()
    llm = MockLLMModel()
    llm.stream = MagicMock(return_value=_FailingAsyncIterator())
    session = MagicMock()
    session.get_session_id.return_value = "sess"
    session.get_state.return_value = None
    session.write_stream = AsyncMock()
    session.close_stream = AsyncMock()
    session.commit = AsyncMock()

    async def empty_stream():
        if False:
            yield None

    session.stream_iterator.return_value = empty_stream()
    agent.is_agent_session = True

    with patch.object(agent, "_get_llm", return_value=llm):
        async for _ in agent._inner_stream(
                session=session, inputs={"query": "查一下热搜"}, need_cleanup=True
        ):
            pass

    agent.context_engine.save_contexts.assert_awaited_once_with(session)
    session.close_stream.assert_awaited_once_with()
    session.commit.assert_awaited_once_with()


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
