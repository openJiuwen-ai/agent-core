# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Legacy rail that configures old context-engine processors explicitly."""
from __future__ import annotations

from typing import Dict, List, Tuple, Union

from pydantic import BaseModel

from openjiuwen.core.context_engine.processor.legacy.compressor import (
    FullCompactProcessorConfig,
    LegacyCurrentRoundCompressorConfig,
    LegacyDialogueCompressorConfig,
    LegacyRoundLevelCompressorConfig,
    MicroCompactProcessorConfig,
)
from openjiuwen.core.context_engine.processor.legacy.offloader import (
    MessageSummaryOffloaderConfig,
    ToolResultBudgetProcessorConfig,
)
from openjiuwen.core.foundation.llm import ModelRequestConfig
from openjiuwen.harness.rails.context_engineer.context_processor_rail import (
    ContextProcessorRail as CurrentContextProcessorRail,
)


_LEGACY_PROCESSOR_KEYS = {
    "DialogueCompressor": "LegacyDialogueCompressor",
    "CurrentRoundCompressor": "LegacyCurrentRoundCompressor",
    "RoundLevelCompressor": "LegacyRoundLevelCompressor",
}


class ContextProcessorRail(CurrentContextProcessorRail):
    """Context processor rail for old processor presets.

    This rail is opt-in. The default harness ContextProcessorRail keeps the new
    processor stack.
    """

    def __init__(
            self,
            processors: Union[
                Tuple[str, BaseModel],
                Tuple[str, Dict],
                List[Tuple[str, BaseModel]],
                List[Tuple[str, Dict]],
                None,
            ] = None,
            preset: bool = True,
            preset_name: str | None = None,
            session_memory=None,
    ):
        super().__init__(
            processors=self._normalize_legacy_processors(processors),
            preset=preset,
            preset_name=preset_name,
            session_memory=session_memory,
        )

    @staticmethod
    def _normalize_legacy_processors(processors):
        if processors is None:
            return None

        is_single_processor = isinstance(processors, tuple) and len(processors) == 2 and isinstance(processors[0], str)
        processor_list = [processors] if is_single_processor else list(processors)
        normalized = [
            (_LEGACY_PROCESSOR_KEYS.get(processor_type, processor_type), config)
            for processor_type, config in processor_list
        ]
        return normalized[0] if is_single_processor else normalized

    def _build_preset_processors(
            self,
            model_config=None,
            model_client_config=None,
    ) -> List[Tuple[str, BaseModel]]:
        model_cfg = ModelRequestConfig.model_copy(model_config) if model_config is not None else None
        if self._session_memory_enabled:
            return [
                (
                    "ToolResultBudgetProcessor",
                    ToolResultBudgetProcessorConfig(),
                ),
                (
                    "MicroCompactProcessor",
                    MicroCompactProcessorConfig(),
                ),
                (
                    "FullCompactProcessor",
                    FullCompactProcessorConfig(
                        model=model_config,
                        model_client=model_client_config,
                    ),
                ),
            ]

        return [
            (
                "MessageSummaryOffloader",
                MessageSummaryOffloaderConfig(
                    large_message_threshold=15000,
                    offload_message_type=["tool"],
                    protected_tool_names=["read_file"],
                    model=model_cfg,
                    model_client=model_client_config,
                ),
            ),
            (
                "LegacyDialogueCompressor",
                LegacyDialogueCompressorConfig(
                    tokens_threshold=100000,
                    messages_to_keep=10,
                    keep_last_round=False,
                    compression_target_tokens=1800,
                    model=model_cfg,
                    model_client=model_client_config,
                ),
            ),
            (
                "LegacyCurrentRoundCompressor",
                LegacyCurrentRoundCompressorConfig(
                    tokens_threshold=100000,
                    messages_to_keep=3,
                    model=model_cfg,
                    model_client=model_client_config,
                ),
            ),
            (
                "LegacyRoundLevelCompressor",
                LegacyRoundLevelCompressorConfig(
                    trigger_context_ratio=0.9,
                    target_total_tokens=160000,
                    keep_recent_messages=6,
                    model=model_cfg,
                    model_client=model_client_config,
                ),
            ),
        ]
