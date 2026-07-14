import subprocess
import sys

from openjiuwen.core.context_engine import (
    ContextEngine,
    CurrentRoundCompressor,
    DialogueCompressor,
    MessageSummaryOffloader,
    ReasoningToolLoopCompactProcessor,
    RoundLevelCompressor,
)


def test_official_processors_remain_default_registry_entries():
    assert ContextEngine._PROCESSOR_MAP["MessageSummaryOffloader"] is MessageSummaryOffloader
    assert ContextEngine._PROCESSOR_MAP["ReasoningToolLoopCompactProcessor"] is ReasoningToolLoopCompactProcessor
    assert ContextEngine._PROCESSOR_MAP["DialogueCompressor"] is DialogueCompressor
    assert ContextEngine._PROCESSOR_MAP["CurrentRoundCompressor"] is CurrentRoundCompressor
    assert ContextEngine._PROCESSOR_MAP["RoundLevelCompressor"] is RoundLevelCompressor


def test_default_context_engine_import_does_not_register_forked_processors():
    script = """
from openjiuwen.core.context_engine import ContextEngine
assert not any(key.startswith("Forked") for key in ContextEngine._PROCESSOR_MAP)
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr


def test_forked_import_registers_distinct_entries_without_overwriting_official():
    official = {
        key: ContextEngine._PROCESSOR_MAP[key]
        for key in (
            "MessageSummaryOffloader",
            "DialogueCompressor",
            "CurrentRoundCompressor",
            "RoundLevelCompressor",
        )
    }

    from openjiuwen.core.context_engine.processor.forked import (
        ForkedCurrentRoundCompressor,
        ForkedDialogueCompressor,
        ForkedMessageOffloader,
        ForkedRoundLevelCompressor,
    )

    assert ContextEngine._PROCESSOR_MAP["ForkedMessageOffloader"] is ForkedMessageOffloader
    assert ContextEngine._PROCESSOR_MAP["ForkedDialogueCompressor"] is ForkedDialogueCompressor
    assert ContextEngine._PROCESSOR_MAP["ForkedCurrentRoundCompressor"] is ForkedCurrentRoundCompressor
    assert ContextEngine._PROCESSOR_MAP["ForkedRoundLevelCompressor"] is ForkedRoundLevelCompressor
    assert {
        key: ContextEngine._PROCESSOR_MAP[key]
        for key in official
    } == official


def test_forked_support_imports_are_available():
    from openjiuwen.core.context_engine.processor.forked.compressor.support.compression_executor import (
        CompressionExecutor,
    )
    from openjiuwen.core.context_engine.processor.forked.offloader.rule_compression.compressors.diff_compressor import (
        DiffCompressor,
    )
    from openjiuwen.core.context_engine.processor.forked.offloader.rule_compression.pipeline import (
        RuleCompressionPipeline,
    )

    assert CompressionExecutor.__name__ == "CompressionExecutor"
    assert DiffCompressor.__name__ == "DiffCompressor"
    assert RuleCompressionPipeline.__name__ == "RuleCompressionPipeline"
