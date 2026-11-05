# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unit tests for image-aware compression token counting and base64 stripping.

Covers the two compression-chain changes:
- ``util._len4`` prices image blocks by dimension instead of the ~6-token
  placeholder, so the compression trigger's usage fast path can fire on
  image-heavy contexts.
- ``CompressionExecutor.build_messages`` replaces image base64 payloads with
  placeholders on a per-message copy, so the compression model call no longer
  carries dead-weight base64 (gateway 413, saved=0%).
"""

import copy

import pytest

from openjiuwen.core.context_engine.processor.forked.compressor.support.compression_executor import (
    CompressionExecutor,
    CompressionRequest,
)
from openjiuwen.core.context_engine.processor.forked.compressor.support.util import _len4
from openjiuwen.core.foundation.llm import UserMessage


def _image_url_block(data_url: str = "data:image/png;base64,iVBORw0KGgo=") -> dict:
    """An OpenAI-style image_url block (parseable PNG header, ~1x1 px)."""

    return {"type": "image_url", "image_url": {"url": data_url}}


def _read_file_image_block() -> dict:
    """A read_file-style image block (data_url key, dims unread -> 1445 fallback)."""

    return {
        "type": "image",
        "source": "read_file",
        "source_path": "/tmp/x.png",
        "mime_type": "image/png",
        "data_url": "data:image/png;base64,iVBORw0KGgo=",
    }


# ---------------------------------------------------------------------------
# _len4 image-aware token counting
# ---------------------------------------------------------------------------


def test_len4_pure_text_unchanged():
    """A pure-text message keeps the original len(sanitize)//4 estimate."""

    from openjiuwen.core.context_engine.content_sanitize import (
        sanitize_content_for_text,
    )

    msg = UserMessage(content="hello world " * 10)
    assert _len4(msg) == len(sanitize_content_for_text(msg.content)) // 4


def test_len4_image_block_adds_dimension_tokens():
    """An image_url block adds dimension-based tokens on top of the text estimate."""

    text_only = UserMessage(content="ctx text")
    text_tokens = _len4(text_only)

    multimodal = UserMessage(
        content=[{"type": "text", "text": "ctx text"}, _image_url_block()],
    )
    multimodal_tokens = _len4(multimodal)

    # The image block contributes at least the 1445 fallback (when dims are unread)
    assert multimodal_tokens >= text_tokens + 1445


def test_len4_read_file_image_block_uses_fallback():
    """A read_file image block (data_url key, dims unread) is priced at the 1445 fallback."""

    multimodal = UserMessage(
        content=[{"type": "text", "text": "ctx"}, _read_file_image_block()],
    )
    assert _len4(multimodal) >= 1445


def test_len4_non_list_content_skips_image_scanning():
    """str content (with a data URL) follows the original len//4 path; no extra add."""

    msg = UserMessage(content="see data:image/png;base64,AAAA")
    assert _len4(msg) > 0


def test_estimate_content_tokens_matches_len4_image_pricing():
    """The no-token-counter fallback and _len4 price image blocks consistently (C-1)."""

    from openjiuwen.core.context_engine.processor.forked.compressor.support.util import (
        estimate_content_tokens,
    )

    text_msg = UserMessage(content="ctx text")
    multimodal = UserMessage(
        content=[{"type": "text", "text": "ctx text"}, _image_url_block()],
    )

    assert estimate_content_tokens(multimodal.content) >= estimate_content_tokens(text_msg.content) + 1445
    # Both estimators agree on the image-block add, modulo the char-based floor of 1.
    assert estimate_content_tokens(multimodal.content) >= _len4(multimodal)


# ---------------------------------------------------------------------------
# CompressionExecutor.build_messages base64 stripping
# ---------------------------------------------------------------------------


def test_build_messages_removes_image_blocks():
    """Image blocks are dropped (not padded with placeholders) on the forwarded messages."""

    request = CompressionRequest(
        prompt="summarize",
        context_messages=[
            UserMessage(
                content=[{"type": "text", "text": "keep me"}, _image_url_block()],
            )
        ],
    )
    messages = CompressionExecutor.build_messages(request)

    # The last message is the compression prompt; the first is the context.
    forwarded = messages[0].content
    assert isinstance(forwarded, list)
    # Text block preserved; image block removed entirely (no placeholder stuffing).
    assert [block["type"] for block in forwarded] == ["text"]
    assert "keep me" in forwarded[0]["text"]
    assert all("data:image/" not in str(block) for block in forwarded)


def test_build_messages_removes_read_file_image_blocks():
    """read_file style image blocks (data_url key) are also dropped entirely."""

    request = CompressionRequest(
        prompt="summarize",
        context_messages=[
            UserMessage(content=[{"type": "text", "text": "keep"}, _read_file_image_block()]),
        ],
    )
    forwarded = CompressionExecutor.build_messages(request)[0].content
    assert isinstance(forwarded, list)
    assert [block.get("type") for block in forwarded] == ["text"]


def test_build_messages_strips_inline_base64_in_text_and_string_content():
    """Residual inline data URLs in text/list and string content are collapsed (413 guard)."""

    list_request = CompressionRequest(
        prompt="summarize",
        context_messages=[
            UserMessage(content=[{"type": "text", "text": "see data:image/png;base64,AAAAAA=="}]),
        ],
    )
    list_forwarded = str(CompressionExecutor.build_messages(list_request)[0].content)
    assert "base64,AAAAAA==" not in list_forwarded
    assert "data:image/png" not in list_forwarded

    string_request = CompressionRequest(
        prompt="summarize",
        context_messages=[UserMessage(content="raw data:image/png;base64,BBBBBB==")],
    )
    string_forwarded = str(CompressionExecutor.build_messages(string_request)[0].content)
    assert "base64,BBBBBB==" not in string_forwarded
    assert "data:image/png" not in string_forwarded


def test_build_messages_does_not_mutate_original_request():
    """Original request messages are never mutated by build_messages (deep baseline)."""

    msg = UserMessage(
        content=[{"type": "text", "text": "keep me"}, _image_url_block()],
    )
    original_content = copy.deepcopy(msg.content)
    request = CompressionRequest(prompt="summarize", context_messages=[msg])

    CompressionExecutor.build_messages(request)

    assert msg.content == original_content
    assert any("data:image/png;base64" in str(p) for p in msg.content)


def test_build_messages_keeps_text_only_messages_as_is():
    """A message with no image payload keeps its original object reference."""

    msg = UserMessage(content="plain text only")
    request = CompressionRequest(prompt="summarize", context_messages=[msg])

    messages = CompressionExecutor.build_messages(request)

    assert messages[0] is msg


def test_build_messages_preserves_compression_prompt():
    """The compression prompt is still appended as the final user message."""

    request = CompressionRequest(prompt="Summarize this history")
    messages = CompressionExecutor.build_messages(request)

    assert messages[-1].content == "Summarize this history"


def test_build_messages_respects_exclude_recent_messages():
    """exclude_recent_messages drops the tail of context_messages before stripping."""

    request = CompressionRequest(
        prompt="summarize",
        context_messages=[
            UserMessage(content="old"),
            UserMessage(content="recent"),
        ],
        exclude_recent_messages=1,
    )
    messages = CompressionExecutor.build_messages(request)

    contents = [str(m.content) for m in messages[:-1]]
    assert "old" in contents
    assert "recent" not in contents
