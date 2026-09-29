# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

from openjiuwen.core.context_engine.processor.forked.offloader.rule_compression.types import (
    ContentType,
    RuleCompressionResult,
    RuleContext,
)


class SourceFileCompressor:
    """Decline rule compression for verbatim source, markup, config and document files.

    Every rule compressor drops lines or re-extracts text, which leaves source code
    unusable for editing and documents unreadable. Returning the content unmodified
    lets the offloader fall back to a head/tail preview that keeps the original
    retrievable through the offload handle.
    """

    @staticmethod
    def compress(content: str, ctx: RuleContext) -> RuleCompressionResult:
        """Return the content unchanged.

        Args:
            content: Tool result content.
            ctx: Rule compression context.

        Returns:
            An unmodified result typed as SOURCE_FILE.
        """
        _ = ctx
        return RuleCompressionResult(
            content=content,
            content_type=ContentType.SOURCE_FILE,
            modified=False,
        )
