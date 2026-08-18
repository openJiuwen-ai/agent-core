# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Regression tests for request-serialization tool-arguments normalization.

Background: a dict assigned to ToolCall.arguments after construction silently
sticks (pydantic validate_assignment defaults to False), poisoning the
conversation history; the next request then carried a JSON *object* in
function.arguments and was rejected by strict providers (ModelArts.81001).

The normalization lives at the serialization boundary
(BaseModelClient._convert_messages_to_dict). Coercion at assignment time was
evaluated and reverted: the team debate rail deliberately passes
_DebateInvocationMeta through a shared arguments dict that the send_message
tool pops during execution, so eager json.dumps at assignment both crashes on
the non-serializable meta object and leaks meta into model-visible history.
"""
import json

from openjiuwen.core.foundation.llm.model_clients.base_model_client import (
    BaseModelClient,
)
from openjiuwen.core.foundation.llm.schema.message import AssistantMessage
from openjiuwen.core.foundation.llm.schema.tool_call import ToolCall


def _make_tool_call(arguments='{"to": "*", "content": "hi"}') -> ToolCall:
    return ToolCall(
        id="call_1",
        type="function",
        name="send_message",
        arguments=arguments,
    )


def _poisoned_tool_call(arguments) -> ToolCall:
    """A ToolCall carrying a non-str arguments, as produced by post-construction
    mutation (bypasses construction-time validation)."""
    return ToolCall.model_construct(
        id="call_x",
        type="function",
        name="send_message",
        arguments=arguments,
    )


class _NonSerializableMeta:
    """Stand-in for side-channel objects like _DebateInvocationMeta."""

    def __repr__(self):
        return "<Meta round-1>"


class TestConvertMessagesToDictNormalization:
    def test_dict_arguments_serialized_as_str(self, caplog):
        msg = AssistantMessage(
            content="",
            tool_calls=[_poisoned_tool_call({"to": "*", "content": "hi"})],
        )
        with caplog.at_level("WARNING"):
            result = BaseModelClient._convert_messages_to_dict([msg])
        args = result[0]["tool_calls"][0]["function"]["arguments"]
        assert isinstance(args, str)
        assert json.loads(args) == {"to": "*", "content": "hi"}

    def test_dict_with_non_serializable_values_does_not_crash(self):
        # Edge case: debate meta object still present (tool never popped it).
        # default=str keeps serialization alive instead of raising TypeError.
        poisoned = _poisoned_tool_call({"to": "*", "_team_debate_meta": _NonSerializableMeta()})
        msg = AssistantMessage(content="", tool_calls=[poisoned])
        result = BaseModelClient._convert_messages_to_dict([msg])
        args = result[0]["tool_calls"][0]["function"]["arguments"]
        assert isinstance(args, str)
        parsed = json.loads(args)
        assert parsed["to"] == "*"
        assert parsed["_team_debate_meta"] == "<Meta round-1>"

    def test_str_arguments_unchanged(self):
        raw = '{"to": "*", "content": "你好\n世界"}'
        msg = AssistantMessage(content="", tool_calls=[_make_tool_call(raw)])
        result = BaseModelClient._convert_messages_to_dict([msg])
        assert result[0]["tool_calls"][0]["function"]["arguments"] == raw

    def test_end_to_end_mutation_then_serialize(self):
        # Mirror the incident: a ToolCall already stored in history gets a
        # dict assigned during tool execution; the outgoing request payload
        # must still carry a JSON string.
        tc = _make_tool_call()
        history_msg = AssistantMessage(content="通知成员", tool_calls=[tc])
        tc.arguments = {"to": "*", "content": "请介绍思路", "summary": "广播"}
        result = BaseModelClient._convert_messages_to_dict([history_msg])
        args = result[0]["tool_calls"][0]["function"]["arguments"]
        assert isinstance(args, str)
        assert json.loads(args) == {"to": "*", "content": "请介绍思路", "summary": "广播"}
