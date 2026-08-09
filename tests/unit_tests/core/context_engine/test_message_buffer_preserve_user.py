# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Tests for preserving evicted user anchors in ContextMessageBuffer."""
from __future__ import annotations

from openjiuwen.core.context_engine.context.message_buffer import ContextMessageBuffer
from openjiuwen.core.foundation.llm import AssistantMessage, ToolMessage, UserMessage


def _build_long_tool_turn(user_content: str, tail_size: int) -> list:
    messages = [UserMessage(content=user_content)]
    for index in range(tail_size):
        if index % 2 == 0:
            messages.append(AssistantMessage(content=f"step {index}"))
        else:
            messages.append(ToolMessage(content=f"result {index}", tool_call_id=f"t{index}"))
    return messages


def test_get_back_preserves_evicted_user_anchor():
    buffer = ContextMessageBuffer(_build_long_tool_turn("冒烟测试", 250), max_buffer_size=200)

    visible = buffer.get_back()

    assert visible[0].role == "user"
    assert visible[0].content == "冒烟测试"
    assert len(visible) == 201
    assert visible[-1].role == "tool"


def test_resize_preserves_evicted_user_anchor():
    buffer = ContextMessageBuffer(_build_long_tool_turn("task anchor", 500), max_buffer_size=200)
    buffer._if_need_resize()

    assert buffer._context_messages[0].role == "user"
    assert buffer._context_messages[0].content == "task anchor"
    assert any(message.role == "tool" for message in buffer._context_messages)
