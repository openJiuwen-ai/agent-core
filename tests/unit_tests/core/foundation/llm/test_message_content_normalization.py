# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Tests for reading ``BaseMessage.content`` as typed parts.

The central invariant here is that normalization is **lazy**: ``content`` keeps
whatever the caller supplied, and only ``.parts``/``.text`` present it as
``ContentPart``. Every existing consumer type-tests ``content`` items for
``dict``/``str``, so coercing on construction would break all of them at once
(see ``docs/dev/message-content-parts-refactor.md`` §3, Stage 1).
"""

import base64
import json
from unittest.mock import patch

from openjiuwen.core.foundation.llm import (
    AssistantMessage,
    ImagePart,
    TextPart,
    ToolMessage,
    UserMessage,
)
from openjiuwen.core.foundation.llm.schema import content_part as content_part_module
from openjiuwen.core.foundation.llm.schema.tool_call import ToolCall

_PNG_MIME = "image/png"
_PAYLOAD = base64.b64encode(b"\x89PNG\r\n\x1a\nfake-pixels").decode("ascii")
_DATA_URL = f"data:{_PNG_MIME};base64,{_PAYLOAD}"


def _openai_image_block(url: str = _DATA_URL) -> dict:
    """The block ``react_agent`` and the mobile GUI skill runner emit."""
    return {"type": "image_url", "image_url": {"url": url}}


def _filesystem_image_item() -> dict:
    """The block ``harness/tools/filesystem.py`` attaches to ``read_file`` output."""
    return {
        "type": "image",
        "source": "read_file",
        "source_path": "/tmp/shot.png",
        "mime_type": _PNG_MIME,
        "data_url": _DATA_URL,
    }


class TestStorageIsNotCoerced:
    """Stage 1 is additive: nothing about how ``content`` is stored changes."""

    def test_str_content_unchanged(self):
        assert UserMessage(content="hi").content == "hi"

    def test_str_elements_stay_str(self):
        message = UserMessage(content=["a", "b"])

        assert message.content == ["a", "b"]
        assert all(isinstance(item, str) for item in message.content)

    def test_known_dicts_stay_dicts(self):
        """A recognized dialect must not be silently rewritten in place."""
        block = _openai_image_block()
        message = UserMessage(content=[block])

        assert message.content == [block]
        assert isinstance(message.content[0], dict)

    def test_part_instances_pass_through(self):
        part = TextPart(text="q")
        message = UserMessage(content=[part])

        assert message.content == [part]
        assert isinstance(message.content[0], TextPart)


class TestPartsProperty:
    def test_str_element_becomes_text_part(self):
        assert UserMessage(content=["a"]).parts == [TextPart(text="a")]

    def test_openai_text_dict_becomes_text_part(self):
        message = UserMessage(content=[{"type": "text", "text": "hello"}])

        assert message.parts == [TextPart(text="hello")]

    def test_openai_image_url_dict_becomes_image_part(self):
        message = UserMessage(content=[_openai_image_block()])

        part = message.parts[0]
        assert isinstance(part, ImagePart)
        assert part.mime_type == _PNG_MIME
        assert part.data == _PAYLOAD
        assert part.url is None

    def test_openai_remote_image_url_keeps_the_url(self):
        message = UserMessage(content=[_openai_image_block("https://example.invalid/a.jpg")])

        part = message.parts[0]
        assert isinstance(part, ImagePart)
        assert part.url == "https://example.invalid/a.jpg"
        assert part.mime_type == "image/jpeg"
        assert part.data is None

    def test_openai_image_detail_is_preserved(self):
        block = {"type": "image_url", "image_url": {"url": _DATA_URL, "detail": "low"}}

        assert UserMessage(content=[block]).parts[0].detail == "low"

    def test_filesystem_data_url_dict_becomes_image_part(self):
        message = UserMessage(content=[_filesystem_image_item()])

        part = message.parts[0]
        assert isinstance(part, ImagePart)
        assert part.mime_type == _PNG_MIME
        assert part.data == _PAYLOAD

    def test_parts_property_on_str_content(self):
        assert UserMessage(content="hi").parts == [TextPart(text="hi")]

    def test_part_instances_are_not_rewrapped(self):
        part = ImagePart(mime_type=_PNG_MIME, data=_PAYLOAD)

        assert UserMessage(content=[part]).parts[0] is part

    def test_dumped_parts_are_read_back_as_parts(self):
        """Normalization must close over its own serialized shape.

        Persistence stores ``model_dump()`` output, so a part that cannot be
        recognized from its own dump silently downgrades to an opaque dict on
        reload.
        """
        original = UserMessage(
            content=[TextPart(text="q"), ImagePart(mime_type=_PNG_MIME, data=_PAYLOAD, width=8, height=8)]
        )

        restored = UserMessage.model_validate(original.model_dump())

        assert restored.parts == original.parts

    def test_unknown_dict_is_dropped_from_parts(self):
        message = UserMessage(content=[{"type": "video_url", "video_url": {"url": "x"}}])

        assert message.parts == []

    def test_mixed_content_preserves_order(self):
        message = UserMessage(content=["intro", _openai_image_block(), {"type": "text", "text": "outro"}])

        assert [part.type for part in message.parts] == ["text", "image", "text"]


class TestUnknownDicts:
    def test_unknown_dict_preserved_verbatim(self):
        """Normalization must never raise or mutate an unrecognized dialect."""
        unknown = {"type": "video_url", "video_url": {"url": "https://example.invalid/v.mp4"}}
        message = UserMessage(content=[unknown])

        # Still a plain dict, key for key. (Pydantic copies dicts on validation,
        # as it always has, so this is equality rather than identity.)
        assert message.content == [unknown]
        assert isinstance(message.content[0], dict)

    def test_malformed_image_url_is_preserved(self):
        malformed = {"type": "image_url", "image_url": {"url": "data:image/png;base64,!!!"}}
        message = UserMessage(content=[malformed])

        assert message.content == [malformed]
        assert message.parts == []

    def test_unknown_dict_logs_debug(self):
        message = UserMessage(content=[{"type": "video_url"}])

        with patch.object(content_part_module.llm_logger, "debug") as mock_debug:
            message.parts

        mock_debug.assert_called_once()

    def test_known_dict_does_not_log(self):
        message = UserMessage(content=[{"type": "text", "text": "hi"}])

        with patch.object(content_part_module.llm_logger, "debug") as mock_debug:
            message.parts

        mock_debug.assert_not_called()


class TestTextProperty:
    def test_text_property_on_str_content(self):
        assert UserMessage(content="hello").text == "hello"

    def test_text_property_concatenates_text_parts(self):
        message = UserMessage(content=["a", _openai_image_block(), {"type": "text", "text": "b"}])

        assert message.text == "a\nb"

    def test_text_property_ignores_unknown_dicts(self):
        message = UserMessage(content=[{"type": "video_url"}, "kept"])

        assert message.text == "kept"

    def test_text_property_on_image_only_content(self):
        assert UserMessage(content=[_openai_image_block()]).text == ""

    def test_text_property_on_empty_content(self):
        assert UserMessage().text == ""


class TestSerialization:
    def test_model_dump_serializes_parts(self):
        message = UserMessage(content=[TextPart(text="q"), ImagePart(mime_type=_PNG_MIME, data=_PAYLOAD)])

        dumped = message.model_dump()

        assert dumped["content"][0] == {"type": "text", "text": "q"}
        assert dumped["content"][1]["type"] == "image"
        assert dumped["content"][1]["mime_type"] == _PNG_MIME

    def test_model_dump_json_is_serializable_with_parts(self):
        """``session/vcs`` calls ``json.dumps`` with no ``default=``."""
        message = AssistantMessage(content=[TextPart(text="q")])

        assert json.loads(json.dumps(message.model_dump(mode="json")))["content"] == [{"type": "text", "text": "q"}]

    def test_model_dump_json_matches_model_dump(self):
        """The two output paths diverged while ``model_dump`` was hand-written."""
        message = AssistantMessage(
            content="answer",
            tool_calls=[ToolCall(id="call_1", type="function", name="search", arguments='{"q": "x"}')],
            reasoning_content="because",
        )

        assert json.loads(message.model_dump_json()) == message.model_dump(mode="json")

    def test_assistant_message_dump_preserves_tool_calls(self):
        """Regression guard on the shape the old hand-written override emitted."""
        message = AssistantMessage(
            content="",
            tool_calls=[ToolCall(id="call_1", type="function", name="search", arguments='{"q": "x"}')],
        )

        assert message.model_dump()["tool_calls"] == [
            {
                "id": "call_1",
                "type": "function",
                "function": {"name": "search", "arguments": '{"q": "x"}'},
            }
        ]

    def test_assistant_message_dump_keeps_response_item_id(self):
        message = AssistantMessage(
            content="",
            tool_calls=[ToolCall(id="c1", type="function", name="f", arguments="{}", response_item_id="resp_1")],
        )

        assert message.model_dump()["tool_calls"][0]["response_item_id"] == "resp_1"

    def test_assistant_message_dump_stays_compact(self):
        """Empty and ``None`` fields are dropped, as they always have been."""
        dumped = AssistantMessage(content="hi").model_dump()

        assert dumped == {"role": "assistant", "content": "hi", "finish_reason": "null"}

    def test_assistant_message_dump_roundtrips(self):
        message = AssistantMessage(
            content="answer",
            tool_calls=[ToolCall(id="call_1", type="function", name="search", arguments="{}")],
        )

        restored = AssistantMessage.model_validate(message.model_dump())

        assert restored.tool_calls[0].name == "search"
        assert restored.model_dump() == message.model_dump()

    def test_assistant_message_dump_honors_exclude(self):
        """``exclude`` was silently ignored by the old override."""
        message = AssistantMessage(content="hi", reasoning_content="because")

        assert "reasoning_content" not in message.model_dump(exclude={"reasoning_content"})

    def test_tool_message_dump_is_unchanged(self):
        dumped = ToolMessage(content="result", tool_call_id="call_1").model_dump()

        assert dumped["content"] == "result"
        assert dumped["tool_call_id"] == "call_1"
