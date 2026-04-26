# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Wire-format allowlist tests for BaseModelClient message conversion."""
from __future__ import annotations

import pytest

from openjiuwen.core.foundation.llm import (
    AssistantMessage,
    SystemMessage,
    ToolCall,
    ToolMessage,
    UserMessage,
)
from openjiuwen.core.foundation.llm.model_clients.base_model_client import BaseModelClient


@pytest.mark.unit
class TestLLMWireFormatAllowlist:
    def test_basemessage_conversion_strips_metadata(self):
        msgs = [
            SystemMessage(content="sys", metadata={"active_skill_pin": True}),
            UserMessage(content="hi", metadata={"context_message_id": "abc"}),
            AssistantMessage(content="reply", metadata={"foo": "bar"}),
            ToolMessage(
                content="result",
                tool_call_id="t-1",
                metadata={"is_skill_body": True, "skill_name": "x"},
            ),
        ]
        out = BaseModelClient._convert_messages_to_dict(msgs)
        for d in out:
            assert "metadata" not in d
            assert "context_message_id" not in d
            assert "usage_metadata" not in d
            assert "parser_content" not in d
            assert "finish_reason" not in d

    def test_dict_input_strips_internal_fields(self):
        dirty = [
            {
                "role": "user",
                "content": "hello",
                "metadata": {"active_skill_pin": True},
                "context_message_id": "abc",
                "parser_content": "junk",
                "finish_reason": "stop",
            }
        ]
        out = BaseModelClient._convert_messages_to_dict(dirty)
        assert out[0] == {"role": "user", "content": "hello", "reasoning_content": ""} or out[0] == {
            "role": "user",
            "content": "hello",
        }

    def test_assistant_with_tool_calls_kept(self):
        msg = AssistantMessage(
            content="ok",
            tool_calls=[ToolCall(id="c1", type="function", name="f", arguments="{}")],
        )
        out = BaseModelClient._convert_messages_to_dict([msg])
        assert out[0]["role"] == "assistant"
        assert out[0]["tool_calls"][0]["id"] == "c1"
        assert "metadata" not in out[0]

    def test_tool_message_keeps_tool_call_id(self):
        msg = ToolMessage(content="r", tool_call_id="abc")
        out = BaseModelClient._convert_messages_to_dict([msg])
        assert out[0]["tool_call_id"] == "abc"
        assert out[0]["role"] == "tool"

    def test_unknown_top_level_field_dropped(self):
        dirty = [{"role": "user", "content": "hi", "future_field": "x"}]
        out = BaseModelClient._convert_messages_to_dict(dirty)
        assert "future_field" not in out[0]
