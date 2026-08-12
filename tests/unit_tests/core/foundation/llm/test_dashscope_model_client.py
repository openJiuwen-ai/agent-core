# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unit tests for the DashScope content conversion.

Only the MultiModalConversation (Wanx) generation APIs use DashScope's
``{"text"}``/``{"image"}`` dialect. The chat path is OpenAI-compatible and
inherits ``BaseModelClient._render_parts`` unchanged — asserted here so a
future override does not silently break it.
"""

import base64

import pytest

from openjiuwen.core.common.exception.errors import ValidationError
from openjiuwen.core.foundation.llm import (
    ImagePart,
    ModelClientConfig,
    ModelRequestConfig,
    TextPart,
    UserMessage,
)
from openjiuwen.core.foundation.llm.model_clients.dashscope_model_client import (
    DashScopeModelClient,
    _to_dashscope_content,
)

_PNG_MIME = "image/png"
_PAYLOAD = base64.b64encode(b"\x89PNG\r\n\x1a\nfake-pixels").decode("ascii")
_DATA_URL = f"data:{_PNG_MIME};base64,{_PAYLOAD}"


def _content(content) -> list:
    return _to_dashscope_content(content)[0]


class TestToDashScopeContent:
    def test_str_content_becomes_one_text_entry(self):
        assert _to_dashscope_content("draw a cat") == ([{"text": "draw a cat"}], 1, 0)

    def test_text_part_renders_as_text_key(self):
        assert _content([TextPart(text="draw a cat")]) == [{"text": "draw a cat"}]

    def test_str_item_renders_as_text_key(self):
        assert _content(["draw a cat"]) == [{"text": "draw a cat"}]

    def test_image_part_renders_as_image_key(self):
        assert _content([ImagePart(mime_type=_PNG_MIME, data=_PAYLOAD)]) == [{"image": _DATA_URL}]

    def test_url_backed_image_part_keeps_the_url(self):
        part = ImagePart(mime_type=_PNG_MIME, url="https://example.invalid/a.png")

        assert _content([part]) == [{"image": "https://example.invalid/a.png"}]

    def test_openai_image_block_is_translated(self):
        """The Stage-0 defect: this used to raise ``ValidationError``."""
        block = {"type": "image_url", "image_url": {"url": _DATA_URL}}

        assert _content(["draw", block]) == [{"text": "draw"}, {"image": _DATA_URL}]

    def test_native_dialect_still_accepted(self):
        """DashScope's own shape carries no ``type`` key; normalization skips it."""
        native = [{"text": "draw"}, {"image": "https://example.invalid/a.png"}]

        assert _content(native) == native

    def test_counts_text_and_images(self):
        content = ["draw", ImagePart(mime_type=_PNG_MIME, data=_PAYLOAD), "in blue"]

        _, text_count, image_count = _to_dashscope_content(content)

        assert (text_count, image_count) == (2, 1)

    def test_order_is_preserved(self):
        content = [ImagePart(mime_type=_PNG_MIME, data=_PAYLOAD), "then text"]

        assert list(_content(content)[0]) == ["image"]
        assert list(_content(content)[1]) == ["text"]

    def test_unknown_dict_still_raises(self):
        with pytest.raises(ValidationError, match="must contain 'text' or 'image' key"):
            _to_dashscope_content([{"caption": "x"}])

    def test_non_str_non_dict_item_still_raises(self):
        with pytest.raises(ValidationError, match="must be string or dict"):
            _to_dashscope_content([42])

    def test_non_str_non_list_content_still_raises(self):
        with pytest.raises(ValidationError, match="must be string or list"):
            _to_dashscope_content(42)


class TestChatPathIsUnchanged:
    """DashScope chat is OpenAI-compatible; only the Wanx path is special."""

    def test_render_parts_is_inherited_from_the_openai_default(self):
        message = UserMessage(content=[TextPart(text="hi"), ImagePart(mime_type=_PNG_MIME, data=_PAYLOAD)])

        converted = DashScopeModelClient._convert_messages_to_dict([message])[0]

        assert converted["content"] == [
            {"type": "text", "text": "hi"},
            {"type": "image_url", "image_url": {"url": _DATA_URL}},
        ]


@pytest.mark.asyncio
async def test_image_message_does_not_raise_validation_error(monkeypatch):
    """End to end through ``generate_image``: an image input is now accepted."""
    from unittest.mock import MagicMock, patch

    client = DashScopeModelClient(
        ModelRequestConfig(model="qwen-image-max"),
        ModelClientConfig(
            client_provider="DashScope",
            api_key="mock-api-key",
            api_base="https://dashscope.example.invalid/api/v1",
        ),
    )

    response = MagicMock()
    response.status_code = 200
    response.output = {"choices": [{"message": {"content": [{"image": "https://example.invalid/out.png"}]}}]}

    message = UserMessage(content=["draw a cat like this", {"type": "image_url", "image_url": {"url": _DATA_URL}}])

    with patch(
        "openjiuwen.core.foundation.llm.model_clients.dashscope_model_client.MultiModalConversation"
    ) as mock_conversation:
        mock_conversation.call.return_value = response
        result = await client.generate_image([message])

    assert result.images == ["https://example.invalid/out.png"]
    sent = mock_conversation.call.call_args.kwargs["messages"][0]["content"]
    assert sent == [{"text": "draw a cat like this"}, {"image": _DATA_URL}]
