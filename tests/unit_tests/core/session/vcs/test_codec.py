# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Tests for the vcs message codec (BaseMessage <-> json dict, by role)."""
import json

from openjiuwen.core.foundation.llm.schema.content_part import ImagePart, TextPart
from openjiuwen.core.foundation.llm.schema.message import (
    AssistantMessage,
    SystemMessage,
    ToolMessage,
    UserMessage,
)
from openjiuwen.core.foundation.llm.schema.tool_call import ToolCall
from openjiuwen.core.session.vcs.codec import (
    decode_context_state,
    decode_message,
    encode_context_state,
    encode_message,
)


def test_user_message_roundtrip():
    decoded = decode_message(encode_message(UserMessage(content="hello")))
    assert isinstance(decoded, UserMessage)
    assert decoded.role == "user"
    assert decoded.content == "hello"


def test_assistant_message_with_tool_calls_roundtrip():
    msg = AssistantMessage(
        content="",
        tool_calls=[ToolCall(id="call_1", type="function", name="search", arguments='{"q": "x"}')],
    )
    decoded = decode_message(encode_message(msg))
    assert isinstance(decoded, AssistantMessage)
    assert decoded.tool_calls is not None
    assert len(decoded.tool_calls) == 1
    assert decoded.tool_calls[0].name == "search"
    assert decoded.tool_calls[0].arguments == '{"q": "x"}'


def test_tool_message_roundtrip():
    decoded = decode_message(encode_message(ToolMessage(content="result", tool_call_id="call_1")))
    assert isinstance(decoded, ToolMessage)
    assert decoded.tool_call_id == "call_1"
    assert decoded.content == "result"


def test_system_message_roundtrip():
    decoded = decode_message(encode_message(SystemMessage(content="sys")))
    assert isinstance(decoded, SystemMessage)
    assert decoded.content == "sys"


def test_encoded_messages_are_json_native():
    encoded = encode_context_state({
        "messages": [UserMessage(content="hi"), AssistantMessage(content="yo")],
        "offload_messages": {},
    })
    assert all(isinstance(m, dict) for m in encoded["messages"])


def test_context_state_roundtrip():
    state = {
        "messages": [UserMessage(content="hi"), AssistantMessage(content="yo")],
        "offload_messages": {"h1": [UserMessage(content="big")]},
    }
    decoded = decode_context_state(encode_context_state(state))
    assert len(decoded["messages"]) == 2
    assert isinstance(decoded["messages"][0], UserMessage)
    assert decoded["messages"][1].content == "yo"
    assert decoded["offload_messages"]["h1"][0].content == "big"


def test_content_part_content_survives_json_dumps():
    """``vcs/backend.py`` calls ``json.dumps`` with no ``default=``.

    ``AssistantMessage`` used to override ``model_dump`` by hand, ignoring
    ``mode="json"``, so a ``ContentPart`` in ``content`` would have reached
    ``json.dumps`` as a pydantic model and raised ``TypeError``.
    """
    message = AssistantMessage(content=[TextPart(text="answer")])

    encoded = encode_message(message)

    assert json.loads(json.dumps(encoded))["content"] == [{"type": "text", "text": "answer"}]


def test_content_part_content_roundtrips():
    message = UserMessage(
        content=[TextPart(text="look"), ImagePart(mime_type="image/png", data="Zm9v")],
    )

    decoded = decode_message(encode_message(message))

    assert isinstance(decoded, UserMessage)
    assert decoded.parts == message.parts
