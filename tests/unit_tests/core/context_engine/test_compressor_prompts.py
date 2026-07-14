# coding: utf-8

from openjiuwen.core.context_engine.processor.forked.compressor.current_round_compressor import (
    ForkedCurrentRoundCompressor,
    ForkedCurrentRoundCompressorConfig,
)
from openjiuwen.core.context_engine.processor.forked.compressor.dialogue_compressor import (
    ForkedDialogueCompressor,
)
from openjiuwen.core.context_engine.processor.forked.compressor.round_level_compressor import (
    ForkedRoundLevelCompressor,
)
from openjiuwen.core.context_engine.processor.forked.compressor.prompts.prompts import (
    CURRENT_COMPACT_PROMPT,
    DIALOGUE_COMPACT_PROMPT,
    ROUND_COMPACT_PROMPT,
)


def test_compressors_use_prompts_module_defaults():
    assert ForkedCurrentRoundCompressor.default_prompt == CURRENT_COMPACT_PROMPT

    assert ForkedDialogueCompressor.default_prompt == DIALOGUE_COMPACT_PROMPT

    assert ForkedRoundLevelCompressor.default_prompt == ROUND_COMPACT_PROMPT


def test_prompts_are_the_structured_compaction_prompts():
    assert "<coverage_check>" in CURRENT_COMPACT_PROMPT
    assert "<state_snapshot>" in DIALOGUE_COMPACT_PROMPT
    assert "full-context state snapshot" in ROUND_COMPACT_PROMPT


def test_manual_compact_preserve_instruction_is_appended_to_prompt():
    processor = ForkedCurrentRoundCompressor(ForkedCurrentRoundCompressorConfig())

    default_prompt = processor._build_prompt(None)
    assert default_prompt == CURRENT_COMPACT_PROMPT
    assert "User preservation instruction:" not in default_prompt

    prompt = processor._build_prompt(
        None,
        preserve_instruction="preserve API decisions and failed attempts",
    )

    assert prompt.startswith(CURRENT_COMPACT_PROMPT.rstrip())
    assert "User preservation instruction:" in prompt
    assert "preserve API decisions and failed attempts" in prompt
    assert "not as a new task to execute" in prompt
