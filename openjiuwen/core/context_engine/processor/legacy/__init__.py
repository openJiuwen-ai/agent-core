# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Legacy context-engine processors kept for transition compatibility."""

from openjiuwen.core.context_engine.processor.legacy.budget_guard import (
    count_messages_tokens,
    effective_context_budget,
    truncate_message_content_to_fixed_head_tail_if_over_token_limit,
)
from openjiuwen.core.context_engine.processor.legacy.base import ContextEvent, ContextProcessor
from openjiuwen.core.context_engine.processor.legacy.compressor import (
    FullCompactProcessor,
    FullCompactProcessorConfig,
    LegacyCurrentRoundCompressor,
    LegacyCurrentRoundCompressorConfig,
    LegacyDialogueCompressor,
    LegacyDialogueCompressorConfig,
    LegacyRoundLevelCompressor,
    LegacyRoundLevelCompressorConfig,
    MicroCompactProcessor,
    MicroCompactProcessorConfig,
)
from openjiuwen.core.context_engine.processor.legacy.context_processor_rail import ContextProcessorRail
from openjiuwen.core.context_engine.processor.legacy.offloader import (
    MessageSummaryOffloader,
    MessageSummaryOffloaderConfig,
    ToolResultBudgetProcessor,
    ToolResultBudgetProcessorConfig,
)

__all__ = (
    ContextEvent.__name__,
    ContextProcessor.__name__,
    ContextProcessorRail.__name__,
    FullCompactProcessor.__name__,
    FullCompactProcessorConfig.__name__,
    LegacyCurrentRoundCompressor.__name__,
    LegacyCurrentRoundCompressorConfig.__name__,
    LegacyDialogueCompressor.__name__,
    LegacyDialogueCompressorConfig.__name__,
    LegacyRoundLevelCompressor.__name__,
    LegacyRoundLevelCompressorConfig.__name__,
    MessageSummaryOffloader.__name__,
    MessageSummaryOffloaderConfig.__name__,
    MicroCompactProcessor.__name__,
    MicroCompactProcessorConfig.__name__,
    ToolResultBudgetProcessor.__name__,
    ToolResultBudgetProcessorConfig.__name__,
    count_messages_tokens.__name__,
    effective_context_budget.__name__,
    truncate_message_content_to_fixed_head_tail_if_over_token_limit.__name__,
)
