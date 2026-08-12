# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""The skill-branch producers emit typed parts (Stage 3, commit 2)."""

import base64

from openjiuwen.core.foundation.llm import ImagePart, TextPart, UserMessage
from openjiuwen.core.foundation.llm.model_clients.anthropic_model_client import (
    AnthropicModelClient,
    _convert_message_schemas,
)
from openjiuwen.core.foundation.llm.model_clients.base_model_client import BaseModelClient
from openjiuwen.harness.tools.mobile_gui.skill_branch.runner import (
    _build_live_screenshot_blocks,
    _image_part_from_data_url,
)

_JPEG_B64 = base64.b64encode(b"\xff\xd8\xff\xe0fake-jpeg").decode("ascii")


class TestLiveScreenshotBlocks:
    def test_bare_base64_becomes_an_image_part(self):
        blocks = _build_live_screenshot_blocks(_JPEG_B64)

        assert isinstance(blocks[0], TextPart)
        assert isinstance(blocks[1], ImagePart)
        assert blocks[1].mime_type == "image/jpeg"
        assert blocks[1].data == _JPEG_B64

    def test_detail_is_low_to_keep_screenshots_cheap(self):
        assert _build_live_screenshot_blocks(_JPEG_B64)[1].detail == "low"

    def test_data_url_input_keeps_its_declared_mime_type(self):
        blocks = _build_live_screenshot_blocks(f"data:image/png;base64,{_JPEG_B64}")

        assert blocks[1].mime_type == "image/png"

    def test_missing_screenshot_yields_a_text_part_only(self):
        blocks = _build_live_screenshot_blocks("")

        assert len(blocks) == 1
        assert isinstance(blocks[0], TextPart)


class TestLenientParsing:
    """Screenshots must survive payloads that strict base64 would reject."""

    def test_unpadded_payload_is_not_dropped(self):
        part = _image_part_from_data_url("data:image/png;base64,abc")

        assert part.data == "abc"

    def test_missing_mime_type_falls_back(self):
        assert _image_part_from_data_url("data:;base64,abc").mime_type == "image/png"

    def test_non_base64_data_url_degrades_to_a_url(self):
        """``ImagePart`` requires exactly one of data/url, so an empty ``data``
        would raise. A percent-encoded payload becomes a ``url`` instead."""
        part = _image_part_from_data_url("data:image/svg+xml,%3Csvg%2F%3E")

        assert part.data is None
        assert part.url == "data:image/svg+xml,%3Csvg%2F%3E"
        assert part.mime_type == "image/svg+xml"


class TestRendering:
    """``detail:"low"`` must reach OpenAI, and the image must reach Anthropic."""

    def test_openai_render_carries_detail_low(self):
        message = UserMessage(content=_build_live_screenshot_blocks(_JPEG_B64))

        content = BaseModelClient._convert_messages_to_dict([message])[0]["content"]

        assert content[1]["image_url"]["detail"] == "low"
        assert content[1]["image_url"]["url"] == f"data:image/jpeg;base64,{_JPEG_B64}"

    def test_anthropic_render_is_a_source_block(self):
        message = UserMessage(content=_build_live_screenshot_blocks(_JPEG_B64))

        payload = AnthropicModelClient._convert_messages_to_dict([message])
        _, messages = _convert_message_schemas(payload)

        assert messages[0]["content"][1] == {
            "type": "image",
            "source": {"type": "base64", "media_type": "image/jpeg", "data": _JPEG_B64},
        }
