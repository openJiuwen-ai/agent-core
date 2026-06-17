# coding: utf-8

from openjiuwen.core.context_engine.processor.compressor.forked.current import (
    ForkedCurrentRoundCompressor,
    ForkedCurrentRoundCompressorConfig,
)
from openjiuwen.core.context_engine.processor.compressor.forked.dialogue import (
    ForkedDialogueCompressor,
    ForkedDialogueCompressorConfig,
)
from openjiuwen.core.context_engine.processor.compressor.forked.round import (
    ForkedRoundLevelCompressor,
    ForkedRoundLevelCompressorConfig,
)
from openjiuwen.core.context_engine.processor.compressor.prompts.forked import (
    CURRENT_COMPACT_PROMPT,
    DIALOGUE_COMPACT_PROMPT,
    ROUND_COMPACT_PROMPT,
)


def test_forked_compressors_use_prompts_module_defaults():
    assert ForkedCurrentRoundCompressor.default_prompt == CURRENT_COMPACT_PROMPT
    assert ForkedCurrentRoundCompressorConfig().custom_compression_prompt == CURRENT_COMPACT_PROMPT

    assert ForkedDialogueCompressor.default_prompt == DIALOGUE_COMPACT_PROMPT
    assert ForkedDialogueCompressorConfig().custom_compression_prompt is None

    assert ForkedRoundLevelCompressor.default_prompt == ROUND_COMPACT_PROMPT
    assert ForkedRoundLevelCompressorConfig().custom_compression_prompt == ROUND_COMPACT_PROMPT


def test_forked_prompts_are_the_structured_compaction_prompts():
    assert "<coverage_check>" in CURRENT_COMPACT_PROMPT
    assert "<state_snapshot>" in DIALOGUE_COMPACT_PROMPT
    assert "full-context state snapshot" in ROUND_COMPACT_PROMPT
