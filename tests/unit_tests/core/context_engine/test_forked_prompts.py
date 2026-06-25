# coding: utf-8

from openjiuwen.core.context_engine.processor.compressor.forked.current import (
    ForkedCurrentRoundCompressor,
)
from openjiuwen.core.context_engine.processor.compressor.forked.dialogue import (
    ForkedDialogueCompressor,
)
from openjiuwen.core.context_engine.processor.compressor.forked.round import (
    ForkedRoundLevelCompressor,
)
from openjiuwen.core.context_engine.processor.compressor.prompts.prompts import (
    CURRENT_COMPACT_PROMPT,
    DIALOGUE_COMPACT_PROMPT,
    ROUND_COMPACT_PROMPT,
)


def test_forked_compressors_use_prompts_module_defaults():
    assert ForkedCurrentRoundCompressor.default_prompt == CURRENT_COMPACT_PROMPT

    assert ForkedDialogueCompressor.default_prompt == DIALOGUE_COMPACT_PROMPT

    assert ForkedRoundLevelCompressor.default_prompt == ROUND_COMPACT_PROMPT


def test_forked_prompts_are_the_structured_compaction_prompts():
    assert "<coverage_check>" in CURRENT_COMPACT_PROMPT
    assert "<state_snapshot>" in DIALOGUE_COMPACT_PROMPT
    assert "full-context state snapshot" in ROUND_COMPACT_PROMPT
