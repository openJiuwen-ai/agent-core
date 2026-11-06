# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

from openjiuwen.core.foundation.llm import UserMessage
from openjiuwen.core.foundation.llm.utils.responses_utils import build_request_body, convert_messages

_DATA_URL = "data:image/png;base64,iVBORw0KGgo="


def _user_content(content) -> list[dict]:
    _, inputs = convert_messages([UserMessage(content=content)])
    assert len(inputs) == 1
    assert inputs[0]["role"] == "user"
    return inputs[0]["content"]


def test_image_url_part_becomes_input_image():
    """Chat-style image parts must reach the Responses API instead of being dropped."""
    parts = _user_content([
        {"type": "text", "text": "What colour is this image?"},
        {"type": "image_url", "image_url": {"url": _DATA_URL}},
    ])

    assert parts == [
        {"type": "input_text", "text": "What colour is this image?"},
        {"type": "input_image", "image_url": _DATA_URL},
    ]


def test_part_order_is_preserved():
    parts = _user_content([
        {"type": "image_url", "image_url": {"url": "https://example.com/a.png"}},
        {"type": "text", "text": "Compare the first image"},
        {"type": "text", "text": " with the second."},
        {"type": "image_url", "image_url": {"url": "https://example.com/b.png"}},
    ])

    assert parts == [
        {"type": "input_image", "image_url": "https://example.com/a.png"},
        {"type": "input_text", "text": "Compare the first image with the second."},
        {"type": "input_image", "image_url": "https://example.com/b.png"},
    ]


def test_image_detail_is_carried_over():
    parts = _user_content([{"type": "image_url", "image_url": {"url": _DATA_URL, "detail": "low"}}])

    assert parts == [{"type": "input_image", "image_url": _DATA_URL, "detail": "low"}]


def test_plain_string_image_url_is_accepted():
    parts = _user_content([{"type": "image_url", "image_url": _DATA_URL}])

    assert parts == [{"type": "input_image", "image_url": _DATA_URL}]


def test_dict_messages_keep_images():
    """Callers that pass raw dicts (e.g. the video and vision tools) go through the same path."""
    _, inputs = convert_messages([{
        "role": "user",
        "content": [
            {"type": "text", "text": "Describe it."},
            {"type": "image_url", "image_url": {"url": _DATA_URL}},
        ],
    }])

    assert inputs[0]["content"][1] == {"type": "input_image", "image_url": _DATA_URL}


def test_request_body_contains_input_image():
    body = build_request_body(
        model="gpt-5.6-luna",
        messages=[UserMessage(content=[
            {"type": "text", "text": "What colour is this image?"},
            {"type": "image_url", "image_url": {"url": _DATA_URL}},
        ])],
    )

    assert body["input"] == [{
        "role": "user",
        "content": [
            {"type": "input_text", "text": "What colour is this image?"},
            {"type": "input_image", "image_url": _DATA_URL},
        ],
    }]


def test_text_only_messages_are_unchanged():
    """Text parts are still merged into one ``input_text``, as before."""
    assert _user_content("Hello") == [{"type": "input_text", "text": "Hello"}]
    assert _user_content("") == [{"type": "input_text", "text": ""}]
    assert _user_content([
        {"type": "text", "text": "Hello, "},
        {"type": "text", "text": "world"},
    ]) == [{"type": "input_text", "text": "Hello, world"}]


def test_unrecognised_parts_are_still_dropped():
    """Only images are translated; other unknown parts keep today's behaviour."""
    parts = _user_content([
        {"type": "text", "text": "Watch this."},
        {"type": "video_url", "video_url": {"url": "https://example.com/v.mp4"}},
    ])

    assert parts == [{"type": "input_text", "text": "Watch this."}]


def test_malformed_image_part_is_dropped():
    """An image part without a usable URL is dropped rather than sent half-formed."""
    parts = _user_content([
        {"type": "text", "text": "Hi"},
        {"type": "image_url", "image_url": {"detail": "low"}},
        {"type": "image_url", "image_url": ""},
    ])

    assert parts == [{"type": "input_text", "text": "Hi"}]
