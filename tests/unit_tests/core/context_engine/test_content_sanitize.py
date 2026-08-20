# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Base64 payloads must never leak into text serialization paths.

Token estimation, compressor serialization, and compaction prompts all
flatten message content to text. Without sanitization, one embedded
screenshot turns into tens of thousands of tokens of base64 noise — inflating
estimates, blowing compaction budgets, and shipping garbage to the
summarization LLM. These tests pin every live seam that flattens content:
the shared ContextUtils estimator and the forked (default-preset)
processor pipeline.
"""

from openjiuwen.core.context_engine.content_sanitize import (
    IMAGE_DATA_URL_PLACEHOLDER,
    sanitize_content_for_text,
    sanitize_value_for_text,
)
from openjiuwen.core.context_engine.context.context_utils import ContextUtils
from openjiuwen.core.foundation.llm import UserMessage

_DATA_URL = "data:image/png;base64," + ("Q" * 120_000)
_MULTIMODAL_CONTENT = [
    {"type": "text", "text": "what is on screen?"},
    {"type": "image_url", "image_url": {"url": _DATA_URL}},
]


def test_sanitize_strips_data_urls_from_every_shape() -> None:
    # Bare data-URL string.
    assert sanitize_value_for_text(_DATA_URL) == IMAGE_DATA_URL_PLACEHOLDER
    # Embedded in surrounding text.
    embedded = sanitize_value_for_text(f"before {_DATA_URL} after")
    assert _DATA_URL not in embedded and "before" in embedded and "after" in embedded
    # OpenAI-style image_url dict and Anthropic-style base64 source.
    text = sanitize_content_for_text(
        [
            {"type": "image_url", "image_url": {"url": _DATA_URL}},
            {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "R" * 50_000}},
        ]
    )
    assert "Q" * 100 not in text and "R" * 100 not in text
    # Non-image values survive untouched.
    assert sanitize_value_for_text({"a": [1, "two"]}) == {"a": [1, "two"]}


def test_sanitize_leaves_non_image_data_urls_alone() -> None:
    """Only image payloads get the image placeholder: a non-image data URL
    under a generic "url" key must survive untouched rather than being
    mislabeled as an omitted image."""
    pdf_url = "data:application/pdf;base64," + ("P" * 64)
    assert sanitize_value_for_text({"url": pdf_url}) == {"url": pdf_url}


def test_sanitized_serialization_keeps_the_text_parts() -> None:
    text = sanitize_content_for_text(_MULTIMODAL_CONTENT)
    assert "what is on screen?" in text
    assert _DATA_URL not in text


def test_shared_estimator_ignores_base64_payload() -> None:
    """A screenshot message must estimate at placeholder scale, not payload
    scale — otherwise budget math triggers compression long before the window
    is actually full. ContextUtils.estimate_tokens backs the offloaders and
    budget processors shared by every pipeline."""
    payload_scale = len(_DATA_URL) // 3

    assert ContextUtils.estimate_tokens(_MULTIMODAL_CONTENT) < payload_scale // 10


def test_forked_pipeline_serialization_is_sanitized() -> None:
    """The forked pipeline is the harness default preset, so its own text
    flatteners are the live seams. Pins the forked copies: support-util
    (compaction prompts, dedupe keys), reinjection builders, and the recall
    archive whose text is BM25-indexed and re-rendered into context."""
    from openjiuwen.core.context_engine.processor.forked.compressor.recall.archive import _content_to_text
    from openjiuwen.core.context_engine.processor.forked.compressor.reinjection import builders
    from openjiuwen.core.context_engine.processor.forked.compressor.support import util as forked_util

    message = UserMessage(content=_MULTIMODAL_CONTENT)
    payload_scale = len(_DATA_URL) // 3

    assert _DATA_URL not in forked_util.message_to_text(message)
    assert forked_util.estimate_content_tokens(_MULTIMODAL_CONTENT) < payload_scale // 10
    assert _DATA_URL not in builders.message_to_text(message)
    archived = _content_to_text(_MULTIMODAL_CONTENT)
    assert _DATA_URL not in archived and "what is on screen?" in archived
    assert _content_to_text(None) == ""
