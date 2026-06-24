from __future__ import annotations

from pydantic import Field

from openjiuwen.core.context_engine.context_engine import ContextEngine
from openjiuwen.core.context_engine.processor.compressor.forked.base import (
    ForkedPrefixCompactProcessorConfig,
    ForkedPrefixCompactProcessor,
    PrefixCompactSpan,
    adjust_keep_recent_for_tool_boundaries,
    find_last_real_user_message_index,
)
from openjiuwen.core.context_engine.processor.compressor.prompts.prompts import DIALOGUE_COMPACT_PROMPT
from openjiuwen.core.foundation.llm import BaseMessage


MEMORY_BLOCK_DIALOGUE_OPEN = "<memory_block_dialogue>"
MEMORY_BLOCK_DIALOGUE_CLOSE = "</memory_block_dialogue>"


DEFAULT_DIALOGUE_COMPRESSION_PROMPT = DIALOGUE_COMPACT_PROMPT


class ForkedDialogueCompressorConfig(ForkedPrefixCompactProcessorConfig):
    trigger_context_ratio: float = Field(default=0.4, gt=0.0, lt=1.0)
    min_target_context_ratio: float = Field(default=0.1, ge=0.0, lt=1.0)


@ContextEngine.register_processor()
class ForkedDialogueCompressor(ForkedPrefixCompactProcessor):
    memory_block_open = MEMORY_BLOCK_DIALOGUE_OPEN
    memory_block_close = MEMORY_BLOCK_DIALOGUE_CLOSE
    default_prompt = DEFAULT_DIALOGUE_COMPRESSION_PROMPT
    processor_label = "ForkedDialogueCompressor"
    reinject_builder_names = ["skills", "read_file", "plan_mode", "plan"]

    def __init__(self, config: ForkedDialogueCompressorConfig):
        super().__init__(config)

    @property
    def config(self) -> ForkedDialogueCompressorConfig:
        return self._config

    def _build_span(self, messages: list[BaseMessage]) -> PrefixCompactSpan:
        current_round_start = find_last_real_user_message_index(messages)
        if current_round_start < 0:
            return PrefixCompactSpan([], [], list(messages))
        keep_recent = len(messages) - current_round_start
        effective_keep = adjust_keep_recent_for_tool_boundaries(messages, keep_recent)
        split_index = max(len(messages) - effective_keep, 0)
        return PrefixCompactSpan(
            preserved_prefix=[],
            messages_to_compress=list(messages[:split_index]),
            protected_tail=list(messages[split_index:]),
        )
