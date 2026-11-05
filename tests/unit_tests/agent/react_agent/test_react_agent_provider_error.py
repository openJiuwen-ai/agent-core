#!/usr/bin/env python
# coding: utf-8
"""Provider error responses must enter the rail retry path."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import FrameworkError
from openjiuwen.core.foundation.llm import AssistantMessage, ToolCall, ToolMessage, UsageMetadata
from openjiuwen.core.foundation.tool import LocalFunction, ToolCard
from openjiuwen.core.runner import Runner
from openjiuwen.core.single_agent.agents.react_agent import ReActAgent, ReActAgentConfig
from openjiuwen.core.single_agent.schema.agent_card import AgentCard
from tests.unit_tests.fixtures.mock_llm import MockLLMModel, create_text_response


def test_model_response_error_finish_reason_raises() -> None:
    message = AssistantMessage(
        content="",
        finish_reason="error",
        usage_metadata=UsageMetadata(code=504, err_msg="upstream timeout"),
    )

    with pytest.raises(FrameworkError, match="code=504") as exc_info:
        ReActAgent._raise_for_model_response_error(message)

    assert exc_info.value.status is StatusCode.MODEL_CALL_FAILED
    assert exc_info.value.details == {
        "provider_code": 504,
        "finish_reason": "error",
        "provider_message": "upstream timeout",
    }


def test_successful_model_response_does_not_raise() -> None:
    message = AssistantMessage(
        content="done",
        finish_reason="stop",
        usage_metadata=UsageMetadata(code=0),
    )

    ReActAgent._raise_for_model_response_error(message)


@pytest.mark.parametrize("finish_reason", ["length", "max_tokens", "LENGTH"])
@pytest.mark.parametrize("content", ["", " \n\t"])
def test_output_limit_without_answer_raises(finish_reason, content) -> None:
    """A call cut off by the output limit before any answer or tool call is a failure, not an empty answer."""
    message = AssistantMessage(
        content=content,
        reasoning_content="Let me think about this step by step..." * 50,
        finish_reason=finish_reason,
        usage_metadata=UsageMetadata(input_tokens=100, output_tokens=8192, total_tokens=8292),
    )

    with pytest.raises(FrameworkError, match="output limit") as exc_info:
        ReActAgent._raise_for_model_response_error(message)

    assert exc_info.value.status is StatusCode.MODEL_CALL_FAILED
    assert exc_info.value.details["finish_reason"] == finish_reason.lower()
    assert exc_info.value.details["output_tokens"] == 8192


@pytest.mark.parametrize(
    "message",
    [
        # Truncated answer text: the caller still gets the partial answer.
        AssistantMessage(content="A partial answer", finish_reason="length"),
        # A tool call was produced: the loop goes on to execute it.
        AssistantMessage(
            content="",
            finish_reason="length",
            tool_calls=[ToolCall(type="function", id="call-1", name="lookup", arguments="{}")],
        ),
    ],
)
def test_output_limit_with_answer_or_tool_call_does_not_raise(message) -> None:
    ReActAgent._raise_for_model_response_error(message)


@pytest.mark.asyncio
async def test_invoke_does_not_return_empty_answer_when_output_limit_hit() -> None:
    """End to end: the turn fails visibly instead of returning a 'successful' empty answer (#1809)."""
    agent = ReActAgent(card=AgentCard(name="output-limit-test"))
    agent.configure(ReActAgentConfig(model_name="fake"))
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
    model.invoke = AsyncMock(
        return_value=AssistantMessage(
            content="",
            reasoning_content="Let me analyse the requirements first..." * 50,
            finish_reason="length",
            usage_metadata=UsageMetadata(input_tokens=100, output_tokens=8192, total_tokens=8292),
        )
    )

    with patch.object(agent, "_get_llm", return_value=model):
        with pytest.raises(FrameworkError, match="output limit") as exc_info:
            await agent.invoke({"query": "write a 2000-word report"}, session=session)

    assert exc_info.value.status is StatusCode.MODEL_CALL_FAILED


@pytest.fixture
def fetch_records_calls():
    calls = []
    tool = LocalFunction(
        card=ToolCard(
            id="fetch_records_2193",
            name="fetch_records",
            description="Fetch records by id",
            input_params={
                "type": "object",
                "properties": {"ids": {"type": "array", "items": {"type": "integer"}}},
                "required": ["ids"],
            },
        ),
        func=lambda ids: calls.append(ids) or f"fetched {len(ids)} records",
    )
    Runner.resource_mgr.add_tool(tool)
    yield tool.card, calls
    Runner.resource_mgr.remove_tool(tool.card.id)


async def _run_tool_turn(card: ToolCard, ai_message: AssistantMessage) -> dict[str, str]:
    """Run one tool-calling turn and return the tool messages the model saw next, by tool_call_id."""
    agent = ReActAgent(card=AgentCard(name="truncated-tool-args-test"))
    agent.configure(ReActAgentConfig(model_name="fake"))
    agent.ability_manager.add(card)
    llm = MockLLMModel()
    llm.set_responses([ai_message, create_text_response("done")])

    with patch.object(agent, "_get_llm", return_value=llm):
        await agent.invoke({"conversation_id": "truncated-tool-args", "query": "fetch the records"})

    return {m.tool_call_id: m.content for m in llm.call_history[-1] if isinstance(m, ToolMessage)}


def _fetch_call(call_id: str, arguments: str) -> ToolCall:
    return ToolCall(type="function", id=call_id, name="fetch_records", arguments=arguments)


@pytest.mark.asyncio
@pytest.mark.parametrize("finish_reason", ["length", "max_tokens", "LENGTH"])
async def test_tool_call_cut_off_by_output_limit_is_not_run(fetch_records_calls, finish_reason) -> None:
    """Bracket repair must not turn arguments cut off by the output limit into a partial call (#2193)."""
    card, calls = fetch_records_calls
    message = AssistantMessage(
        content="",
        finish_reason=finish_reason,
        tool_calls=[_fetch_call("call-1", '{"ids": [101, 102, 103')],
    )

    tool_messages = await _run_tool_turn(card, message)

    assert calls == []
    assert "cut off" in tool_messages["call-1"]
    assert "not run" in tool_messages["call-1"]
    assert message.tool_calls[0].arguments == "{}"


@pytest.mark.asyncio
async def test_complete_tool_call_in_truncated_response_still_runs(fetch_records_calls) -> None:
    card, calls = fetch_records_calls
    message = AssistantMessage(
        content="",
        finish_reason="length",
        tool_calls=[
            _fetch_call("call-1", '{"ids": [1, 2]}'),
            _fetch_call("call-2", '{"ids": [101, 102, 103'),
        ],
    )

    tool_messages = await _run_tool_turn(card, message)

    assert calls == [[1, 2]]
    assert tool_messages["call-1"] == "fetched 2 records"
    assert "cut off" in tool_messages["call-2"]


@pytest.mark.asyncio
async def test_malformed_tool_args_are_still_repaired_without_output_limit(fetch_records_calls) -> None:
    card, calls = fetch_records_calls
    message = AssistantMessage(
        content="",
        finish_reason="tool_calls",
        tool_calls=[_fetch_call("call-1", '{"ids": [101, 102, 103')],
    )

    tool_messages = await _run_tool_turn(card, message)

    assert calls == [[101, 102, 103]]
    assert tool_messages["call-1"] == "fetched 3 records"
