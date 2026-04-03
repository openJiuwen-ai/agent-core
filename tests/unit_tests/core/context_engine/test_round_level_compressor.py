# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from unittest.mock import AsyncMock, MagicMock

import pytest

from openjiuwen.core.context_engine.base import ContextWindow
from openjiuwen.core.context_engine.processor.compressor.round_level_compressor import (
    ROUND_LEVEL_FALLBACK_MARKER,
    RoundLevelCompressor,
    RoundLevelCompressorConfig,
    _CompressTarget,
)
from openjiuwen.core.foundation.llm import AssistantMessage, UserMessage


class _TestableRoundLevelCompressor(RoundLevelCompressor):
    def __init__(self, config: RoundLevelCompressorConfig):
        super().__init__(config)
        self.compress_until_target_result = None

    async def build_memory_message_for_test(self, summary: str, target: _CompressTarget, context):
        return await self._build_memory_message(summary, target, context)

    def build_compression_user_prompt_for_test(self, **kwargs) -> str:
        return self._build_compression_user_prompt(**kwargs)

    async def _compress_until_target(self, *args, **kwargs):
        return self.compress_until_target_result


class TestRoundLevelCompressor:
    @pytest.mark.asyncio
    async def test_build_memory_message_offload_falls_back_to_plain_user_message(self):
        compressor = _TestableRoundLevelCompressor(
            RoundLevelCompressorConfig(
                trigger_total_tokens=100,
                target_total_tokens=50,
            )
        )
        compressor.offload_messages = AsyncMock(return_value=None)
        context = MagicMock()
        target = _CompressTarget(
            block_id="block_1",
            scope="ongoing_react",
            start_idx=0,
            end_idx=0,
            messages=[AssistantMessage(content="analysis state")],
        )

        message = await compressor.build_memory_message_for_test(
            "User Requirements:\n- Keep intent.",
            target,
            context,
        )

        assert isinstance(message, UserMessage)
        assert message.content.startswith(ROUND_LEVEL_FALLBACK_MARKER)
        assert "processor: RoundLevelCompressor" in message.content

    @pytest.mark.asyncio
    async def test_on_get_context_window_reports_original_message_range(self):
        compressor = _TestableRoundLevelCompressor(
            RoundLevelCompressorConfig(
                trigger_total_tokens=100,
                target_total_tokens=50,
                offload_writeback_enabled=False,
            )
        )
        compressor.compress_until_target_result = [
            UserMessage(
                content=(
                    f"{ROUND_LEVEL_FALLBACK_MARKER}\n"
                    "processor: RoundLevelCompressor\n"
                    "Summary:\ncompressed"
                )
            )
        ]
        context = MagicMock()
        context.token_counter.return_value = None
        context_window = ContextWindow(
            system_messages=[],
            context_messages=[
                UserMessage(content="u" * 90),
                AssistantMessage(content="a" * 90),
                UserMessage(content="x" * 90),
                AssistantMessage(content="y" * 90),
            ],
            tools=[],
        )

        event, updated_context_window = await compressor.on_get_context_window(context, context_window)

        assert event is not None
        assert event.messages_to_modify == [0, 1, 2, 3]
        assert len(updated_context_window.context_messages) == 1
        assert updated_context_window.context_messages[0].content.startswith(ROUND_LEVEL_FALLBACK_MARKER)

    @staticmethod
    def test_build_compression_user_prompt_includes_ongoing_and_completed_requirements():
        compressor = _TestableRoundLevelCompressor(
            RoundLevelCompressorConfig(
                trigger_total_tokens=100,
                target_total_tokens=50,
            )
        )
        context = MagicMock()
        context.token_counter.return_value = None

        prompt_text = compressor.build_compression_user_prompt_for_test(
            context_messages=[
                UserMessage(content="request"),
                AssistantMessage(content="working"),
                UserMessage(content="another request"),
                AssistantMessage(content="final answer"),
            ],
            targets=[
                _CompressTarget(
                    block_id="block_1",
                    scope="ongoing_react",
                    start_idx=0,
                    end_idx=1,
                    messages=[
                        UserMessage(content="request"),
                        AssistantMessage(content="working"),
                    ],
                ),
                _CompressTarget(
                    block_id="block_2",
                    scope="completed_react",
                    start_idx=2,
                    end_idx=3,
                    messages=[
                        UserMessage(content="another request"),
                        AssistantMessage(content="final answer"),
                    ],
                ),
            ],
            context=context,
            phase_name="phase_1",
            target_tokens=300,
            keep_recent_messages=0,
            system_messages=None,
            tools=None,
        )

        assert "User Requirements" in prompt_text
        assert "Final Result" in prompt_text
        assert "Do not weaken or over-compress the user's original request" in prompt_text
