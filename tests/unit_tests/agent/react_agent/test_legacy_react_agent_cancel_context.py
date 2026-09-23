# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Cancellation must not leave unpaired tool calls in legacy ReAct history."""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from openjiuwen.core.foundation.llm import AssistantMessage, BaseModelInfo, ModelConfig, ToolMessage, UserMessage
from openjiuwen.core.foundation.llm.schema.tool_call import ToolCall
from openjiuwen.core.single_agent.legacy import LegacyReActAgent, create_react_agent_config


@pytest.mark.asyncio
@pytest.mark.parametrize("tool_count", [1, 2])
@pytest.mark.parametrize("session_created", [False, True])
@pytest.mark.parametrize("prior_complete", [False, True])
async def test_cancel_during_tool_execution_keeps_next_model_history_valid(
    tool_count, session_created, prior_complete,
):
    config = create_react_agent_config(
        agent_id="legacy_cancel_test",
        agent_version="0.0.1",
        description="cancel test",
        model=ModelConfig(
            model_provider="OpenAI",
            model_info=BaseModelInfo(model="gpt-4", api_base="mock_url", api_key="mock_key"),
        ),
        prompt_template=[{"role": "system", "content": "test"}],
    )
    agent = LegacyReActAgent(config)
    messages = []
    if prior_complete:
        messages.extend([
            UserMessage(content="previous question"),
            AssistantMessage(content="", tool_calls=[
                ToolCall(id="previous_call", type="function", name="search", arguments="{}"),
            ]),
            ToolMessage(content="previous result", tool_call_id="previous_call"),
            AssistantMessage(content="previous answer"),
        ])
    prior_messages = list(messages)
    context = MagicMock()

    async def add_messages(message):
        messages.extend(message if isinstance(message, list) else [message])

    def set_messages(updated, with_history=True):
        messages[:] = updated

    context.add_messages = AsyncMock(side_effect=add_messages)
    context.get_messages.side_effect = lambda size=None, with_history=True: list(messages[-size:] if size else messages)
    context.set_messages.side_effect = set_messages

    saved = []

    async def save_contexts(_session):
        saved.append(list(messages))

    engine = MagicMock()
    engine.create_context = AsyncMock(return_value=context)
    engine.get_context.return_value = context
    engine.save_contexts = AsyncMock(side_effect=save_contexts)
    agent._context_engine = engine

    tool_calls = [
        ToolCall(id=f"call_{index}", type="function", name="search", arguments="{}")
        for index in range(tool_count)
    ]
    llm = MagicMock()
    llm.invoke = AsyncMock(side_effect=[
        AssistantMessage(content="", tool_calls=tool_calls),
        AssistantMessage(content="continued"),
    ])
    agent._get_llm = lambda: llm

    started = asyncio.Event()

    async def execute_tool(tool_call, _session):
        if tool_count == 2 and tool_call.id == "call_0":
            await context.add_messages(ToolMessage(content="first result", tool_call_id=tool_call.id))
            return "first result"
        started.set()
        await asyncio.Event().wait()

    agent._execute_tool_call = AsyncMock(side_effect=execute_tool)
    session = MagicMock()
    session.get_session_id.return_value = "legacy_cancel_session"
    session.get_state.return_value = None
    session.commit = AsyncMock()
    session.close_stream = AsyncMock()

    inputs = {"conversation_id": "legacy_cancel_session", "query": "search"}
    first_turn = asyncio.create_task(
        agent._inner_invoke(session=session, inputs=inputs, session_created=True)
        if session_created else agent.invoke(inputs, session=session)
    )
    await asyncio.wait_for(started.wait(), timeout=5)
    assert any(isinstance(message, AssistantMessage) and message.tool_calls for message in messages)

    first_turn.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first_turn

    assert saved and saved[-1] == messages
    assert messages[:len(prior_messages)] == prior_messages
    assert [type(message) for message in messages[len(prior_messages):]] == [UserMessage]
    if session_created:
        session.close_stream.assert_awaited_once()
        session.commit.assert_awaited_once()
    else:
        session.close_stream.assert_not_awaited()
        session.commit.assert_not_awaited()

    result = await agent.invoke(
        {"conversation_id": "legacy_cancel_session", "query": "continue"}, session=session,
    )
    assert result == {"output": "continued", "result_type": "answer"}
    next_model_messages = llm.invoke.await_args_list[-1].args[0]
    assert [
        call["id"] for message in next_model_messages
        for call in message.get("tool_calls", [])
    ] == (["previous_call"] if prior_complete else [])
    assert [
        message["tool_call_id"] for message in next_model_messages
        if message.get("role") == "tool"
    ] == (["previous_call"] if prior_complete else [])
