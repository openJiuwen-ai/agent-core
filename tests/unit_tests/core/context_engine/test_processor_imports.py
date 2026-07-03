from openjiuwen.core.context_engine.processor.compressor.support.compression_executor import (
    CompressionExecutor,
)
from openjiuwen.core.context_engine.processor.compressor.current_round_compressor import (
    CurrentRoundCompressor,
)
from openjiuwen.core.context_engine.processor.offloader.rule_compression.compressors.diff_compressor import (
    DiffCompressor,
)
from openjiuwen.core.context_engine.processor.offloader.rule_compression.pipeline import (
    RuleCompressionPipeline,
)
from openjiuwen.core.context_engine.processor.offloader.rule_compression.compressors.source_code_compressor import (
    SourceCodeCompressor,
)


def test_processor_canonical_imports_are_available():
    assert CurrentRoundCompressor.__name__ == "CurrentRoundCompressor"
    assert CompressionExecutor.__name__ == "CompressionExecutor"
    assert RuleCompressionPipeline.__name__ == "RuleCompressionPipeline"
    assert DiffCompressor.__name__ == "DiffCompressor"
    assert SourceCodeCompressor.__name__ == "SourceCodeCompressor"
