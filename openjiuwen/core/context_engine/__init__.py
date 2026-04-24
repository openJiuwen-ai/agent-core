# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from openjiuwen.core.context_engine.schema.config import ContextEngineConfig
from openjiuwen.core.context_engine.base import ModelContext, ContextStats, ContextWindow
from openjiuwen.core.context_engine.context_engine import ContextEngine

from openjiuwen.core.context_engine.token.base import TokenCounter
from openjiuwen.core.context_engine.token.tiktoken_counter import TiktokenCounter

from openjiuwen.core.context_engine.processor.base import ContextProcessor
from openjiuwen.core.context_engine.processor.offloader.message_offloader import (
    MessageOffloader,
    MessageOffloaderConfig
)
from openjiuwen.core.context_engine.processor.offloader.tool_result_budget_processor import (
    ToolResultBudgetProcessor,
    ToolResultBudgetProcessorConfig,
)
from openjiuwen.core.context_engine.processor.offloader.message_summary_offloader import (
    MessageSummaryOffloader,
    MessageSummaryOffloaderConfig
)
from openjiuwen.core.context_engine.processor.compressor.micro_compact_processor import (
    MicroCompactProcessor,
    MicroCompactProcessorConfig,
)
from openjiuwen.core.context_engine.processor.compressor.dialogue_compressor import (
    DialogueCompressor,
    DialogueCompressorConfig
)
from openjiuwen.core.context_engine.processor.compressor.current_round_compressor import (
    CurrentRoundCompressor,
    CurrentRoundCompressorConfig
)
from openjiuwen.core.context_engine.processor.compressor.round_level_compressor import (
    RoundLevelCompressor,
    RoundLevelCompressorConfig
)
from openjiuwen.core.context_engine.processor.compressor.full_compact_processor import (
    FullCompactProcessor,
    FullCompactProcessorConfig,
)
from openjiuwen.core.context_engine.processor.multimodal.image_reference_processor import (
    ImageReferenceProcessor,
    ImageReferenceProcessorConfig,
)

# context base classes
_CORE_CLASSES = [
    "ContextEngineConfig",
    "ContextWindow",
    "ModelContext",
    "ContextStats",
    "ContextEngine"
]


_TOKEN_COUNTER = [
    "TokenCounter",
    "TiktokenCounter"
]


_PROCESSORS_CLASSES = [
    # base process class
    "ContextProcessor",
    "ToolResultBudgetProcessor",
    "ToolResultBudgetProcessorConfig",
    # message offloader
    "MessageOffloader",
    "MessageOffloaderConfig",
    # message summary offloader
    "MessageSummaryOffloader",
    "MessageSummaryOffloaderConfig",
    # micro compact
    "MicroCompactProcessor",
    "MicroCompactProcessorConfig",
    # dialogue compressor
    "DialogueCompressor",
    "DialogueCompressorConfig",
    # current round compressor
    "CurrentRoundCompressor",
    "CurrentRoundCompressorConfig",
    # round level compressor
    "RoundLevelCompressor",
    "RoundLevelCompressorConfig",
    # full compact processor
    "FullCompactProcessor",
    "FullCompactProcessorConfig",
    # multimodal image references
    "ImageReferenceProcessor",
    "ImageReferenceProcessorConfig",
]


# Combine all public APIs
__all__ = (
    _CORE_CLASSES
    + _TOKEN_COUNTER
    + _PROCESSORS_CLASSES
)
