# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Legacy compressor processors."""

from openjiuwen.core.context_engine.processor.legacy.compressor.current_round_compressor import (
    LegacyCurrentRoundCompressor,
    LegacyCurrentRoundCompressorConfig,
)
from openjiuwen.core.context_engine.processor.legacy.compressor.dialogue_compressor import (
    LegacyDialogueCompressor,
    LegacyDialogueCompressorConfig,
)
from openjiuwen.core.context_engine.processor.legacy.compressor.full_compact_processor import (
    FullCompactProcessor,
    FullCompactProcessorConfig,
)
from openjiuwen.core.context_engine.processor.legacy.compressor.micro_compact_processor import (
    MicroCompactProcessor,
    MicroCompactProcessorConfig,
)
from openjiuwen.core.context_engine.processor.legacy.compressor.round_level_compressor import (
    LegacyRoundLevelCompressor,
    LegacyRoundLevelCompressorConfig,
)

__all__ = (
    LegacyCurrentRoundCompressor.__name__,
    LegacyCurrentRoundCompressorConfig.__name__,
    LegacyDialogueCompressor.__name__,
    LegacyDialogueCompressorConfig.__name__,
    FullCompactProcessor.__name__,
    FullCompactProcessorConfig.__name__,
    MicroCompactProcessor.__name__,
    MicroCompactProcessorConfig.__name__,
    LegacyRoundLevelCompressor.__name__,
    LegacyRoundLevelCompressorConfig.__name__,
)
