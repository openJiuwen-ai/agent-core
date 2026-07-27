import subprocess
import sys

def test_default_import_keeps_official_registry_entries():
    script = """
import sys
from openjiuwen.core.context_engine import (
    ContextEngine,
    CurrentRoundCompressor,
    DialogueCompressor,
    MessageSummaryOffloader,
    RoundLevelCompressor,
)
assert ContextEngine._PROCESSOR_MAP["MessageSummaryOffloader"] is MessageSummaryOffloader
assert ContextEngine._PROCESSOR_MAP["DialogueCompressor"] is DialogueCompressor
assert ContextEngine._PROCESSOR_MAP["CurrentRoundCompressor"] is CurrentRoundCompressor
assert ContextEngine._PROCESSOR_MAP["RoundLevelCompressor"] is RoundLevelCompressor
assert "openjiuwen.core.context_engine.processor.forked" not in sys.modules
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr


def test_refactored_import_replaces_same_registry_entries():
    script = """
from openjiuwen.core.context_engine import ContextEngine
from openjiuwen.core.context_engine.processor.forked import (
    CurrentRoundCompressor,
    DialogueCompressor,
    MessageSummaryOffloader,
    RoundLevelCompressor,
)
expected = {
    "MessageSummaryOffloader": MessageSummaryOffloader,
    "DialogueCompressor": DialogueCompressor,
    "CurrentRoundCompressor": CurrentRoundCompressor,
    "RoundLevelCompressor": RoundLevelCompressor,
}
for key, processor in expected.items():
    assert ContextEngine._PROCESSOR_MAP[key] is processor
    assert processor.__module__.startswith("openjiuwen.core.context_engine.processor.forked")
assert not any(key.startswith("Forked") for key in ContextEngine._PROCESSOR_MAP)
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr


def test_refactored_configs_accept_official_preset_fields():
    from openjiuwen.core.context_engine.processor.forked import (
        CurrentRoundCompressorConfig,
        DialogueCompressorConfig,
        MessageSummaryOffloaderConfig,
        RoundLevelCompressorConfig,
    )

    configs = [
        MessageSummaryOffloaderConfig(
            large_message_threshold=15000,
            offload_message_type=["tool"],
            protected_tool_names=["read_file"],
        ),
        DialogueCompressorConfig(
            tokens_threshold=100000,
            messages_to_keep=10,
            keep_last_round=False,
            compression_target_tokens=1800,
        ),
        CurrentRoundCompressorConfig(tokens_threshold=100000, messages_to_keep=3),
        RoundLevelCompressorConfig(
            trigger_context_ratio=0.9,
            target_total_tokens=160000,
            keep_recent_messages=6,
        ),
    ]

    assert len(configs) == 4


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
