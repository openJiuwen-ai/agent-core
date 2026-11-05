#!/usr/bin/env python
# coding: utf-8
"""Provider error responses must enter the rail retry path."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import FrameworkError
from openjiuwen.core.foundation.llm import AssistantMessage, ToolCall, UsageMetadata
from openjiuwen.core.single_agent.agents.react_agent import ReActAgent, ReActAgentConfig
from openjiuwen.core.single_agent.schema.agent_card import AgentCard


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
