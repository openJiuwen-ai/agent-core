from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openjiuwen.core.foundation.llm import (
    OPENJIUWEN_MESSAGE_SOURCE_KIND_METADATA,
    AssistantMessage,
    UsageMetadata,
    UserMessage,
)
from openjiuwen.core.single_agent.agents.react_agent import (
    EMPTY_RESPONSE_MSG,
    ReActAgent,
    ReActAgentConfig,
)
from openjiuwen.core.single_agent.schema.agent_card import AgentCard


def _agent_with_model(*responses: AssistantMessage, max_iterations: int = 5):
    agent = ReActAgent(card=AgentCard(name="empty-response-test"))
    agent.configure(ReActAgentConfig(model_name="fake", max_iterations=max_iterations))
    messages = []
    context = MagicMock()
    context.get_messages.side_effect = lambda **_: list(messages)
    context.add_messages = AsyncMock(side_effect=lambda msg: messages.append(msg))

    async def window(**kwargs):
        return MagicMock(
            get_messages=lambda: kwargs["system_messages"] + list(messages),
            get_tools=lambda: kwargs["tools"],
        )

    context.get_context_window = AsyncMock(side_effect=window)
    engine = MagicMock()
    engine.create_context = AsyncMock(return_value=context)
    engine.save_contexts = AsyncMock()
    agent.context_engine = engine
    session = MagicMock()
    session.get_state.return_value = None
    session.write_stream = AsyncMock()
    model = MagicMock()
    model.invoke = AsyncMock(side_effect=list(responses))
    return agent, session, model, messages


def _empty(reasoning: str = "The table is ready: ...") -> AssistantMessage:
    return AssistantMessage(
        content="",
        reasoning_content=reasoning,
        finish_reason="stop",
        usage_metadata=UsageMetadata(input_tokens=100, output_tokens=40, total_tokens=140),
    )


def _answer(text: str) -> AssistantMessage:
    return AssistantMessage(
        content=text,
        finish_reason="stop",
        usage_metadata=UsageMetadata(input_tokens=100, output_tokens=10, total_tokens=110),
    )


def _notices(messages) -> list:
    return [m for m in messages if isinstance(m, UserMessage) and str(m.content).startswith("[EMPTY_RESPONSE]")]


@pytest.mark.asyncio
async def test_empty_stop_turn_is_retried_once_and_the_answer_returned() -> None:
    agent, session, model, messages = _agent_with_model(_empty(), _answer("Here is the table."))

    with patch.object(agent, "_get_llm", return_value=model):
        result = await agent.invoke({"query": "compile the table"}, session=session)

    assert result["result_type"] == "answer"
    assert result["output"] == "Here is the table."
    assert model.invoke.await_count == 2
    notices = _notices(messages)
    assert len(notices) == 1
    assert notices[0].metadata[OPENJIUWEN_MESSAGE_SOURCE_KIND_METADATA] == "empty_response"


@pytest.mark.asyncio
async def test_repeated_empty_turn_ends_with_an_error_not_an_empty_answer() -> None:
    agent, session, model, messages = _agent_with_model(_empty(), _empty())

    with patch.object(agent, "_get_llm", return_value=model):
        result = await agent.invoke({"query": "compile the table"}, session=session)

    assert result["result_type"] == "error"
    assert result["output"] == EMPTY_RESPONSE_MSG
    assert result["finish_reason"] == "stop"
    assert result["reasoning_present"] is True
    assert model.invoke.await_count == 2
    assert len(_notices(messages)) == 1


@pytest.mark.asyncio
async def test_empty_turn_on_the_last_iteration_errors_without_a_notice() -> None:
    agent, session, model, messages = _agent_with_model(_empty(), max_iterations=1)

    with patch.object(agent, "_get_llm", return_value=model):
        result = await agent.invoke({"query": "compile the table"}, session=session)

    assert result["result_type"] == "error"
    assert result["output"] == EMPTY_RESPONSE_MSG
    assert model.invoke.await_count == 1
    assert not _notices(messages)


@pytest.mark.asyncio
async def test_whitespace_only_content_counts_as_empty() -> None:
    blank = AssistantMessage(content="  \n", finish_reason="stop")
    agent, session, model, _ = _agent_with_model(blank, blank)

    with patch.object(agent, "_get_llm", return_value=model):
        result = await agent.invoke({"query": "hi"}, session=session)

    assert result["result_type"] == "error"
    assert result["reasoning_present"] is False


@pytest.mark.asyncio
async def test_non_empty_answer_is_returned_without_retry() -> None:
    agent, session, model, messages = _agent_with_model(_answer("Done."))

    with patch.object(agent, "_get_llm", return_value=model):
        result = await agent.invoke({"query": "hi"}, session=session)

    assert result == {"output": "Done.", "result_type": "answer"}
    assert model.invoke.await_count == 1
    assert not _notices(messages)
