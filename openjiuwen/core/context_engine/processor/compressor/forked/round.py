from __future__ import annotations

from pydantic import Field

from openjiuwen.core.context_engine.context_engine import ContextEngine
from openjiuwen.core.context_engine.processor.compressor.forked.base import (
    ForkedPrefixCompactProcessorConfig,
    ForkedPrefixCompactProcessor,
    PrefixCompactSpan,
    adjust_keep_recent_for_tool_boundaries,
)
from openjiuwen.core.context_engine.processor.compressor.prompts.prompts import ROUND_COMPACT_PROMPT
from openjiuwen.core.foundation.llm import BaseMessage


MEMORY_BLOCK_ROUND_OPEN = "<memory_block_round>"
MEMORY_BLOCK_ROUND_CLOSE = "</memory_block_round>"


DEFAULT_ROUND_COMPRESSION_PROMPT = ROUND_COMPACT_PROMPT


class ForkedRoundLevelCompressorConfig(ForkedPrefixCompactProcessorConfig):
    trigger_context_ratio: float = Field(default=0.8, gt=0.0, lt=1.0)
    keep_recent_messages: int = Field(default=4, ge=0)


@ContextEngine.register_processor()
class ForkedRoundLevelCompressor(ForkedPrefixCompactProcessor):
    memory_block_open = MEMORY_BLOCK_ROUND_OPEN
    memory_block_close = MEMORY_BLOCK_ROUND_CLOSE
    default_prompt = DEFAULT_ROUND_COMPRESSION_PROMPT
    processor_label = "ForkedRoundLevelCompressor"
    reinject_builder_names = ["plan_mode", "plan", "task_status", "todo", "skills", "read_file"]

    def __init__(self, config: ForkedRoundLevelCompressorConfig):
        super().__init__(config)

    @property
    def config(self) -> ForkedRoundLevelCompressorConfig:
        return self._config

    def _build_span(self, messages: list[BaseMessage]) -> PrefixCompactSpan:
        effective_keep = adjust_keep_recent_for_tool_boundaries(messages, self.config.keep_recent_messages)
        split_index = max(len(messages) - effective_keep, 0)
        return PrefixCompactSpan(
            preserved_prefix=[],
            messages_to_compress=list(messages[:split_index]),
            protected_tail=list(messages[split_index:]),
        )
