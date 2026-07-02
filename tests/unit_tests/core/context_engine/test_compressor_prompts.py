# coding: utf-8

from openjiuwen.core.context_engine.processor.compressor.current_round_compressor import (
    CurrentRoundCompressor,
)
from openjiuwen.core.context_engine.processor.compressor.dialogue_compressor import (
    DialogueCompressor,
)
from openjiuwen.core.context_engine.processor.compressor.round_level_compressor import (
    RoundLevelCompressor,
)
from openjiuwen.core.context_engine.processor.compressor.prompts.prompts import (
    CURRENT_COMPACT_PROMPT,
    DIALOGUE_COMPACT_PROMPT,
    ROUND_COMPACT_PROMPT,
)


def test_compressors_use_prompts_module_defaults():
    assert CurrentRoundCompressor.default_prompt == CURRENT_COMPACT_PROMPT

    assert DialogueCompressor.default_prompt == DIALOGUE_COMPACT_PROMPT

    assert RoundLevelCompressor.default_prompt == ROUND_COMPACT_PROMPT


def test_prompts_are_the_structured_compaction_prompts():
    assert "<coverage_check>" in CURRENT_COMPACT_PROMPT
    assert "<state_snapshot>" in DIALOGUE_COMPACT_PROMPT
    assert "full-context state snapshot" in ROUND_COMPACT_PROMPT
