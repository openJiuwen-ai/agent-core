# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""ReActAgent resume-continuation: pick a paused round's context back up in place.

``NativeHarness.resume`` drives a round over the preserved context of a round it
paused at a clean inner-iteration boundary. Such a round carries no new user turn
and an empty query, so ``_inner_invoke`` must skip both the empty-query guard and
the user-message append — while a normal invoke keeps doing both.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openjiuwen.core.foundation.llm import UserMessage
from openjiuwen.core.single_agent.agents.react_agent import (
    ReActAgent,
    ReActAgentConfig,
)
from openjiuwen.core.single_agent.schema.agent_card import AgentCard
from tests.unit_tests.fixtures.mock_llm import MockLLMModel, create_text_response


def _make_agent() -> tuple[ReActAgent, MagicMock]:
    """Build a ReActAgent whose context engine is fully mocked."""
    card = AgentCard(name="resume_agent", description="continuation test")
    config = ReActAgentConfig().configure_model("gpt-4").configure_max_iterations(3)

    mock_context_window = MagicMock(
        get_messages=MagicMock(return_value=[]),
        get_tools=MagicMock(return_value=None),
    )
    mock_context = MagicMock()
    mock_context.add_messages = AsyncMock()
    mock_context.get_context_window = AsyncMock(return_value=mock_context_window)

    mock_context_engine = MagicMock()
    mock_context_engine.save_contexts = AsyncMock()
    mock_context_engine.create_context = AsyncMock(return_value=mock_context)

    agent = ReActAgent(card=card)
    agent.configure(config)
    agent.context_engine = mock_context_engine
    return agent, mock_context


def _make_session() -> MagicMock:
    """Build a mock Session sufficient for a single invoke."""
    session = MagicMock()
    session.get_state.return_value = None
    session.write_stream = AsyncMock()
    return session


def _appended_user_turns(mock_context: MagicMock) -> list:
    """User messages appended to the context during the invoke."""
    return [
        call.args[0]
        for call in mock_context.add_messages.call_args_list
        if call.args and isinstance(call.args[0], UserMessage)
    ]


@pytest.mark.asyncio
async def test_resume_continuation_appends_no_user_turn() -> None:
    """A continuation invoke runs the loop without appending a user message."""
    agent, mock_context = _make_agent()
    mock_llm = MockLLMModel()
    mock_llm.set_responses([create_text_response("continued")])

    with patch.object(agent, "_get_llm", return_value=mock_llm):
        result = await agent.invoke(
            {
                "conversation_id": "sess",
                "query": "",
                "_resume_continuation": True,
            },
            session=_make_session(),
        )

    assert result["result_type"] == "answer"
    # The preserved context was resumed in place: no new user turn.
    assert _appended_user_turns(mock_context) == []


@pytest.mark.asyncio
async def test_normal_invoke_appends_the_user_turn() -> None:
    """Without the continuation flag, the query is appended as a user turn."""
    agent, mock_context = _make_agent()
    mock_llm = MockLLMModel()
    mock_llm.set_responses([create_text_response("hi")])

    with patch.object(agent, "_get_llm", return_value=mock_llm):
        await agent.invoke(
            {"conversation_id": "sess", "query": "hello"},
            session=_make_session(),
        )

    assert [m.content for m in _appended_user_turns(mock_context)] == ["hello"]


@pytest.mark.asyncio
async def test_empty_query_without_continuation_is_rejected() -> None:
    """The empty-query guard is relaxed only for a continuation round."""
    agent, _ = _make_agent()
    mock_llm = MockLLMModel()

    with patch.object(agent, "_get_llm", return_value=mock_llm):
        with pytest.raises(Exception, match="query"):
            await agent.invoke(
                {"conversation_id": "sess", "query": ""},
                session=_make_session(),
            )
