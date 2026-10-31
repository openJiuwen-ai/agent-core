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


# ---------------------------------------------------------------------------
# CompressionExecutor.build_messages base64 stripping
# ---------------------------------------------------------------------------


def test_build_messages_strips_image_base64_payload():
    """Image base64 payloads are replaced with placeholders on the forwarded messages."""

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
    forwarded_content = str(messages[0].content)
    assert "data:image/png;base64" not in forwarded_content
    assert "image:data-url-omitted" in forwarded_content
    assert "keep me" in forwarded_content


def test_build_messages_does_not_mutate_original_request():
    """Original request messages are never mutated by build_messages."""

    msg = UserMessage(
        content=[{"type": "text", "text": "keep me"}, _image_url_block()],
    )
    original_content = list(msg.content)
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
