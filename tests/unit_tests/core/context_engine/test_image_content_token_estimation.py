# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Regression tests: image blocks must be priced by pixels, never by base64 chars.

Before the fix every fallback estimator sanitized ``image_url`` blocks into a
short placeholder and charged ``len(text) // 3`` — a full-size screenshot cost
roughly 40 tokens instead of ~1.5k. Compressors and the window budget guard used
those estimates, so a conversation could overflow the model window before any
compression threshold fired.
"""

from __future__ import annotations

import base64
import binascii

import pytest

from openjiuwen.core.context_engine.context.context_utils import ContextUtils
from openjiuwen.core.context_engine.processor.budget_guard import count_messages_tokens as budget_count
from openjiuwen.core.context_engine.processor.compressor.util import count_messages_tokens as plain_count
from openjiuwen.core.context_engine.processor.forked.compressor.support.util import (
    count_messages_tokens as forked_count,
)
from openjiuwen.core.context_engine.token.image_tokens import (
    DEFAULT_IMAGE_PLACEHOLDER_TOKENS,
    estimate_content_image_tokens,
    estimate_image_block_tokens,
    is_image_block,
)
from openjiuwen.core.context_engine.token.string_length_counter import StringLengthCounter
from openjiuwen.core.foundation.llm import UserMessage


def _png_data_uri(width: int, height: int) -> str:
    """Minimal PNG header carrying real IHDR dimensions (no pixel payload needed)."""

    ihdr = width.to_bytes(4, "big") + height.to_bytes(4, "big") + bytes([8, 2, 0, 0, 0])
    chunk = b"IHDR" + ihdr
    crc = binascii.crc32(chunk) & 0xFFFFFFFF
    header = b"\x89PNG\r\n\x1a\n" + (13).to_bytes(4, "big") + chunk + crc.to_bytes(4, "big")
    return "data:image/png;base64," + base64.b64encode(header).decode()


def _image_block(width: int = 512, height: int = 512, detail: str | None = None) -> dict:
    image_url: dict = {"url": _png_data_uri(width, height)}
    if detail is not None:
        image_url["detail"] = detail
    return {"type": "image_url", "image_url": image_url}


def _image_message(width: int = 512, height: int = 512) -> UserMessage:
    return UserMessage(
        content=[
            {"type": "text", "text": "what is in this screenshot?"},
            _image_block(width, height),
        ]
    )


class _NoCounterContext:
    """ModelContext stand-in whose token_counter() is None (the fallback path)."""

    def token_counter(self):
        return None


def test_is_image_block_matches_all_producer_shapes() -> None:
    assert is_image_block({"type": "image_url", "image_url": {"url": "data:image/png;base64,AA"}})
    assert is_image_block({"type": "image", "source": {"data": "AA"}})
    assert is_image_block({"type": "input_image", "image_url": "data:image/png;base64,AA"})
    assert is_image_block({"input_image": {"url": "data:image/png;base64,AA"}})
    assert is_image_block({"image_url": "data:image/png;base64,AA"})
    assert is_image_block({"image": "AA"})
    assert not is_image_block({"type": "text", "text": "hi"})
    assert not is_image_block("hi")


def test_image_block_tokens_follow_pixels_not_base64_length() -> None:
    small = estimate_image_block_tokens(_image_block(512, 512))
    large = estimate_image_block_tokens(_image_block(2048, 1536))

    # 512x512 is ~361 visual tokens (Claude patch pricing); the sanitized base64
    # placeholder used to be charged ~40 tokens for every image regardless of size.
    assert small >= 300
    assert large > small
    assert large <= 1568  # provider cap, never unbounded


def test_image_block_tokens_fallbacks_are_stable() -> None:
    assert estimate_image_block_tokens({"type": "image_url", "image_url": {"url": "https://x/y.png"}}) == (
        DEFAULT_IMAGE_PLACEHOLDER_TOKENS
    )
    assert estimate_image_block_tokens(_image_block(detail="low")) == 85
    assert estimate_image_block_tokens({"type": "text", "text": "hi"}) == 0


def test_estimate_content_image_tokens_sums_blocks() -> None:
    content = [_image_block(512, 512), {"type": "text", "text": "x"}, _image_block(1024, 1024)]
    assert estimate_content_image_tokens(content) == (
        estimate_image_block_tokens(content[0]) + estimate_image_block_tokens(content[2])
    )
    assert estimate_content_image_tokens("plain text") == 0


def test_context_utils_estimate_tokens_adds_image_tokens() -> None:
    text_only = ContextUtils.estimate_tokens([{"type": "text", "text": "what is in this screenshot?"}])
    with_image = ContextUtils.estimate_tokens(_image_message().content)

    assert with_image >= text_only + 300
    # Plain string content keeps the legacy char/3 behaviour.
    assert ContextUtils.estimate_tokens("hello world") == max(len("hello world") // 3, 1)


@pytest.mark.parametrize("count", [plain_count, forked_count])
def test_compressor_fallback_counts_images(count) -> None:
    with_image = count([_image_message()], None)
    text_only = count([UserMessage(content="what is in this screenshot?")], None)

    assert with_image >= text_only + 300


def test_budget_guard_fallback_counts_images() -> None:
    context = _NoCounterContext()
    assert budget_count(context, [_image_message()]) >= 300


def test_string_length_counter_counts_images() -> None:
    counter = StringLengthCounter()
    assert counter.count_messages([_image_message()]) >= 300
    # A text message is unaffected by the image-specific add-on.
    assert counter.count_messages([UserMessage(content="hello")]) == counter.count(
        "<|start|>user\nhello<|end|>"
    ) + 3
