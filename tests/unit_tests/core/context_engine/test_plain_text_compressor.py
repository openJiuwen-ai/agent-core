from __future__ import annotations

from openjiuwen.core.context_engine.processor.offloader.rule_compression.compressors.plain_text_compressor import (
    PlainTextCompressor,
)
from openjiuwen.core.context_engine.processor.offloader.rule_compression.types import (
    ContentType,
    RuleContext,
)


def test_plain_text_compressor_requires_minimum_savings_ratio():
    content = "\n".join(f"unique line {index}" for index in range(200)) + "\n"
    ctx = RuleContext(
        max_tokens=100,
        count_tokens=lambda text: max(len(text) // 3, 1),
        min_savings_ratio=0.1,
    )

    result = PlainTextCompressor().compress(content, ctx)

    assert result.content == content
    assert result.content_type == ContentType.PLAIN_TEXT
    assert result.modified is False
