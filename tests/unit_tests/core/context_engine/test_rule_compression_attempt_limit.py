# coding: utf-8
"""A rule-compression pass is retried only when the character budget gets tighter."""

from __future__ import annotations

from unittest.mock import MagicMock

from openjiuwen.core.context_engine.processor.forked.offloader.rule_compression.pipeline import (
    RULE_COMPRESSION_ATTEMPTED_CHARS_KEY,
    RuleCompressionPipeline,
)
from openjiuwen.core.context_engine.processor.forked.offloader.rule_compression.types import (
    ContentType,
    RuleCompressionResult,
)
from openjiuwen.core.foundation.llm import ToolMessage


class _Context:
    def context_window_tokens(self) -> int:
        return 100

    def get_messages(self):
        return []


def _pipeline(modified: bool) -> tuple[RuleCompressionPipeline, MagicMock]:
    router = MagicMock()
    router.compress.return_value = RuleCompressionResult(
        content="tiny" if modified else "x" * 200,
        content_type=ContentType.PLAIN_TEXT,
        modified=modified,
        lossy=False,
    )
    return RuleCompressionPipeline(router=router, time_func=lambda: 1.0), router


def test_same_budget_is_not_retried():
    pipeline, router = _pipeline(modified=True)
    message = ToolMessage(content="x" * 200, tool_call_id="tc-1")

    first = pipeline.compress(message, _Context(), pass_name="add", max_chars=10, force=True)
    second = pipeline.compress(first, _Context(), pass_name="add", max_chars=10, force=True)

    assert first.metadata[RULE_COMPRESSION_ATTEMPTED_CHARS_KEY] == 10
    assert router.compress.call_count == 1
    assert second is first


def test_tighter_budget_gets_one_retry():
    pipeline, router = _pipeline(modified=False)
    message = ToolMessage(content="x" * 200, tool_call_id="tc-2")

    after_add = pipeline.compress(message, _Context(), pass_name="add", max_chars=100, force=True)
    after_ttl = pipeline.compress(after_add, _Context(), pass_name="ttl", max_chars=50, force=True)
    pipeline.compress(after_ttl, _Context(), pass_name="ttl", max_chars=50, force=True)

    assert after_add.metadata[RULE_COMPRESSION_ATTEMPTED_CHARS_KEY] == 100
    assert after_ttl.metadata[RULE_COMPRESSION_ATTEMPTED_CHARS_KEY] == 50
    assert router.compress.call_count == 2


def test_looser_budget_after_a_tighter_pass_is_skipped():
    pipeline, router = _pipeline(modified=False)
    message = ToolMessage(content="x" * 200, tool_call_id="tc-3")

    first = pipeline.compress(message, _Context(), pass_name="ttl", max_chars=50, force=True)
    second = pipeline.compress(first, _Context(), pass_name="add", max_chars=100, force=True)

    assert first.content == message.content
    assert router.compress.call_count == 1
    assert second is first
