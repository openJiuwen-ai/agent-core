# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unit tests for DeepSeekModelClient message conversion and CoT gating.

Regression coverage for the A-2 fix: the DeepSeek client must not emit
``reasoning_content`` (even as an empty string) in prior assistant turns by
default. Only when ``include_reasoning_content`` is explicitly enabled does it
pad assistant messages lacking the field with an empty string.
"""

from openjiuwen.core.foundation.llm.model_clients.deepseek_model_client import DeepSeekModelClient
from openjiuwen.core.foundation.llm.schema.message import AssistantMessage, ToolMessage
from openjiuwen.core.foundation.llm.schema.tool_call import ToolCall


def _assistant_with_tool_call() -> AssistantMessage:
    return AssistantMessage(
        content="",
        tool_calls=[ToolCall(id="call_1", type="function", name="search", arguments="{}")],
    )


def _assistant_tool_call_with_reasoning() -> AssistantMessage:
    return AssistantMessage(
        content="",
        reasoning_content="hidden chain of thought",
        tool_calls=[ToolCall(id="call_1", type="function", name="search", arguments="{}")],
    )


def test_deepseek_default_strips_reasoning_content_entirely():
    """Default (False): assistant turns carry no reasoning_content field at all,
    not even the previously-addeded empty string that the DeepSeek client used to
    pad unconditionally."""
    converted = DeepSeekModelClient._convert_messages_to_dict(
        [_assistant_with_tool_call(), _assistant_tool_call_with_reasoning()]
    )
    for msg in converted:
        assert "reasoning_content" not in msg


def test_deepseek_explicit_false_strips_reasoning_content():
    converted = DeepSeekModelClient._convert_messages_to_dict(
        [_assistant_tool_call_with_reasoning()],
        include_reasoning_content=False,
    )
    assert "reasoning_content" not in converted[0]


def test_deepseek_enabled_echoes_non_empty_reasoning_content():
    converted = DeepSeekModelClient._convert_messages_to_dict(
        [_assistant_tool_call_with_reasoning()],
        include_reasoning_content=True,
    )
    assert converted[0]["reasoning_content"] == "hidden chain of thought"


def test_deepseek_enabled_pads_assistant_missing_reasoning_content():
    converted = DeepSeekModelClient._convert_messages_to_dict(
        [_assistant_with_tool_call()],
        include_reasoning_content=True,
    )
    assert converted[0]["reasoning_content"] == ""


def test_deepseek_tool_message_never_carries_reasoning_content():
    msg = ToolMessage(content="42", tool_call_id="call_1")
    converted = DeepSeekModelClient._convert_messages_to_dict([msg], include_reasoning_content=True)
    assert "reasoning_content" not in converted[0]
