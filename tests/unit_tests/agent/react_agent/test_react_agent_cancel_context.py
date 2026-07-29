# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Cancel cleanup must preserve the user query for the next turn."""

from openjiuwen.core.foundation.llm import AssistantMessage, ToolMessage, UserMessage
from openjiuwen.core.foundation.llm.schema.tool_call import ToolCall
from openjiuwen.core.single_agent.agents.react_agent import ReActAgent


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
