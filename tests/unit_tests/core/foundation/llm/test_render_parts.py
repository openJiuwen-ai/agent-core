# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Tests for ``BaseModelClient._render_parts``, the provider conversion seam.

``_convert_messages_to_dict`` normalizes each item of list content and hands
the result to ``_render_parts``, which every client may override to speak its
own content dialect. The default is the OpenAI shape.

The load-bearing assertion in this module is
``test_single_text_part_renders_as_bare_string``: providers key their prompt
cache on the exact request prefix, so a lone text part must go on the wire as a
bare ``str``, exactly as it did before this seam existed.
"""

import base64

import pytest

from openjiuwen.core.foundation.llm import (
    AssistantMessage,
    ImagePart,
    SystemMessage,
    TextPart,
    ToolMessage,
    UserMessage,
)
from openjiuwen.core.foundation.llm.model_clients.base_model_client import BaseModelClient
from openjiuwen.core.foundation.llm.schema.tool_call import ToolCall

_PNG_MIME = "image/png"
_PAYLOAD = base64.b64encode(b"\x89PNG\r\n\x1a\nfake-pixels").decode("ascii")
_DATA_URL = f"data:{_PNG_MIME};base64,{_PAYLOAD}"


def _render(message) -> object:
    """Convert one message and return just its rendered ``content``."""
    return BaseModelClient._convert_messages_to_dict([message])[0]["content"]


class TestSingleTextCollapse:
    """A lone text part must never become a one-element list."""

    def test_single_text_part_renders_as_bare_string(self):
        assert _render(UserMessage(content=[TextPart(text="x")])) == "x"

    def test_single_str_item_renders_as_bare_string(self):
        assert _render(UserMessage(content=["x"])) == "x"

    def test_single_openai_text_block_renders_as_bare_string(self):
        assert _render(UserMessage(content=[{"type": "text", "text": "x"}])) == "x"

    def test_str_content_is_untouched(self):
        """Plain ``str`` content never reaches ``_render_parts`` at all."""
        assert _render(UserMessage(content="x")) == "x"

    def test_empty_parts_renders_as_empty_string(self):
        assert _render(UserMessage(content=[])) == ""

    def test_single_image_part_stays_a_list(self):
        """The collapse is text-only; an image has no bare-string form."""
        rendered = _render(UserMessage(content=[ImagePart(mime_type=_PNG_MIME, data=_PAYLOAD)]))

        assert isinstance(rendered, list)
        assert len(rendered) == 1


class TestDefaultOpenAIShape:
    def test_multiple_parts_render_as_list(self):
        rendered = _render(UserMessage(content=[TextPart(text="a"), TextPart(text="b")]))

        assert rendered == [
            {"type": "text", "text": "a"},
            {"type": "text", "text": "b"},
        ]

    def test_image_part_renders_as_image_url_block(self):
        message = UserMessage(content=[TextPart(text="look"), ImagePart(mime_type=_PNG_MIME, data=_PAYLOAD)])

        assert _render(message)[1] == {"type": "image_url", "image_url": {"url": _DATA_URL}}

    def test_url_backed_image_keeps_the_url(self):
        message = UserMessage(
            content=[
                TextPart(text="look"),
                ImagePart(mime_type="image/jpeg", url="https://example.invalid/a.jpg"),
            ]
        )

        assert _render(message)[1]["image_url"] == {"url": "https://example.invalid/a.jpg"}

    def test_default_detail_is_omitted(self):
        """Emitting ``detail: "auto"`` would change payloads that omit it today."""
        message = UserMessage(content=[TextPart(text="look"), ImagePart(mime_type=_PNG_MIME, data=_PAYLOAD)])

        assert "detail" not in _render(message)[1]["image_url"]

    def test_non_default_detail_is_emitted(self):
        message = UserMessage(
            content=[
                TextPart(text="look"),
                ImagePart(mime_type=_PNG_MIME, data=_PAYLOAD, detail="low"),
            ]
        )

        assert _render(message)[1]["image_url"]["detail"] == "low"

    def test_default_render_is_openai_shape(self):
        """An OpenAI-shaped payload survives the round trip byte for byte."""
        blocks = [
            {"type": "text", "text": "describe this"},
            {"type": "image_url", "image_url": {"url": _DATA_URL}},
        ]

        assert _render(UserMessage(content=blocks)) == blocks

    def test_unknown_dict_passes_through_default_render(self):
        """An unrecognized dialect must reach the wire, not be dropped."""
        unknown = {"type": "video_url", "video_url": {"url": "https://example.invalid/v.mp4"}}

        assert _render(UserMessage(content=[TextPart(text="a"), unknown])) == [
            {"type": "text", "text": "a"},
            unknown,
        ]

    def test_lone_unknown_dict_is_not_collapsed(self):
        unknown = {"type": "video_url"}

        assert _render(UserMessage(content=[unknown])) == [unknown]


class TestOtherMessageFields:
    """Rendering must not disturb anything else ``_convert_messages_to_dict`` emits."""

    def test_roles_are_preserved(self):
        messages = [
            SystemMessage(content="sys"),
            UserMessage(content=["hi"]),
            AssistantMessage(content="yo"),
        ]

        converted = BaseModelClient._convert_messages_to_dict(messages)

        assert [m["role"] for m in converted] == ["system", "user", "assistant"]

    def test_tool_calls_still_convert(self):
        message = AssistantMessage(
            content=[TextPart(text="calling")],
            tool_calls=[ToolCall(id="c1", type="function", name="search", arguments="{}")],
        )

        converted = BaseModelClient._convert_messages_to_dict([message])[0]

        assert converted["content"] == "calling"
        assert converted["tool_calls"] == [
            {"id": "c1", "type": "function", "function": {"name": "search", "arguments": "{}"}}
        ]

    def test_tool_message_keeps_tool_call_id(self):
        message = ToolMessage(content=[TextPart(text="result")], tool_call_id="c1")

        converted = BaseModelClient._convert_messages_to_dict([message])[0]

        assert converted == {"role": "tool", "content": "result", "tool_call_id": "c1"}

    def test_raw_dict_messages_bypass_rendering(self):
        """Pre-built dict messages are forwarded verbatim, as they always were."""
        raw = [{"role": "user", "content": [{"type": "text", "text": "a"}]}]

        assert BaseModelClient._convert_messages_to_dict(raw) == raw

    def test_str_messages_still_become_a_user_message(self):
        assert BaseModelClient._convert_messages_to_dict("hi") == [{"role": "user", "content": "hi"}]

    def test_empty_messages_still_raise(self):
        with pytest.raises(Exception):
            BaseModelClient._convert_messages_to_dict([])
