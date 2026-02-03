# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from unittest.mock import MagicMock, AsyncMock, patch

import pytest

from openjiuwen.core.context_engine import ContextEngine, ContextEngineConfig
from openjiuwen.core.context_engine.processor.compressor.round_level_compressor import (
    RoundLevelCompressorConfig,
)
from openjiuwen.core.context_engine.schema.messages import OffloadMixin
from openjiuwen.core.foundation.llm import (
    UserMessage,
    AssistantMessage,
)


async def create_context_with_compressor(
    compressor_config: RoundLevelCompressorConfig,
    history_messages=None,
    token_counter=None,
):
    """Create context with RoundLevelCompressor via ContextEngine.create_context."""
    engine = ContextEngine(ContextEngineConfig(default_window_message_num=100))
    return await engine.create_context(
        "test_ctx",
        None,
        history_messages=history_messages or [],
        processors=[("RoundLevelCompressor", compressor_config)],
        token_counter=token_counter,
    )


class TestRoundLevelCompressor:
    """RoundLevelCompressor unit tests: complex scenarios with multi-round add_messages."""

    @pytest.mark.asyncio
    async def test_tokens_threshold_triggers_round_compression(self):
        """When token count exceeds threshold and enough rounds exist, rounds are compressed."""
        mock_user = MagicMock()
        mock_user.parser_content = {"summary": "User intents: ask A, ask B, ask C."}
        mock_ai = MagicMock()
        mock_ai.parser_content = {"summary": "AI responses: answer A, answer B, answer C."}

        mock_counter = MagicMock()
        mock_counter.count_messages = MagicMock(return_value=15000)

        with patch(
            "openjiuwen.core.context_engine.processor.compressor.round_level_compressor.Model"
        ) as mock_model_cls:
            mock_model = MagicMock()
            mock_model.invoke = AsyncMock(side_effect=[mock_user, mock_ai])
            mock_model_cls.return_value = mock_model

            config = RoundLevelCompressorConfig(
                rounds_threshold=3,
                tokens_threshold=5000,
                keep_last_round=False,
            )
            ctx = await create_context_with_compressor(config, token_counter=mock_counter)

            rounds = []
            for i in range(5):
                rounds.append(UserMessage(content=f"User question {i}"))
                rounds.append(AssistantMessage(content=f"Assistant answer {i}"))

            await ctx.add_messages(rounds)

            result = ctx.get_messages()
            assert len(result) < 10
            offloaded = [m for m in result if isinstance(m, OffloadMixin)]
            assert len(offloaded) >= 2

            reloaded_user = await ctx.reloader_tool().invoke(
                dict(offload_handle=offloaded[0].offload_handle, offload_type="in_memory")
            )
            reloaded_ai = await ctx.reloader_tool().invoke(
                dict(offload_handle=offloaded[1].offload_handle, offload_type="in_memory")
            )
            assert "User question" in reloaded_user or "User intents" in reloaded_user
            assert "Assistant answer" in reloaded_ai or "AI responses" in reloaded_ai

    @pytest.mark.asyncio
    async def test_multi_round_add_triggers_compression(self):
        """Multiple add_messages calls: compression triggered when threshold reached."""
        mock_user = MagicMock()
        mock_user.parser_content = {"summary": "Compressed user intents."}
        mock_ai = MagicMock()
        mock_ai.parser_content = {"summary": "Compressed assistant responses."}

        mock_counter = MagicMock()
        mock_counter.count_messages = MagicMock(return_value=12000)

        with patch(
            "openjiuwen.core.context_engine.processor.compressor.round_level_compressor.Model"
        ) as mock_model_cls:
            mock_model = MagicMock()
            mock_model.invoke = AsyncMock(side_effect=[mock_user, mock_ai])
            mock_model_cls.return_value = mock_model

            config = RoundLevelCompressorConfig(
                rounds_threshold=3,
                tokens_threshold=5000,
                keep_last_round=False,
            )
            ctx = await create_context_with_compressor(config, token_counter=mock_counter)

            await ctx.add_messages([
                UserMessage(content="Round 0 user"),
                AssistantMessage(content="Round 0 assistant"),
            ])
            result0 = ctx.get_messages()
            assert len(result0) == 2

            await ctx.add_messages([
                UserMessage(content="Round 1 user"),
                AssistantMessage(content="Round 1 assistant"),
            ])
            result1 = ctx.get_messages()
            assert len(result1) == 4

            await ctx.add_messages([
                UserMessage(content="Round 2 user"),
                AssistantMessage(content="Round 2 assistant"),
            ])
            result2 = ctx.get_messages()
            offloaded = [m for m in result2 if isinstance(m, OffloadMixin)]
            assert len(offloaded) >= 2

            reloaded = await ctx.reloader_tool().invoke(
                dict(offload_handle=offloaded[0].offload_handle, offload_type="in_memory")
            )
            assert "Round" in reloaded or "user" in reloaded.lower() or "assistant" in reloaded.lower()

    @pytest.mark.asyncio
    async def test_keep_last_round_preserves_final_round(self):
        """With keep_last_round=True, the last round is not compressed."""
        mock_user = MagicMock()
        mock_user.parser_content = {"summary": "Earlier user intents."}
        mock_ai = MagicMock()
        mock_ai.parser_content = {"summary": "Earlier AI responses."}

        mock_counter = MagicMock()
        mock_counter.count_messages = MagicMock(return_value=15000)

        with patch(
            "openjiuwen.core.context_engine.processor.compressor.round_level_compressor.Model"
        ) as mock_model_cls:
            mock_model = MagicMock()
            mock_model.invoke = AsyncMock(side_effect=[mock_user, mock_ai])
            mock_model_cls.return_value = mock_model

            config = RoundLevelCompressorConfig(
                rounds_threshold=3,
                tokens_threshold=5000,
                keep_last_round=True,
            )
            ctx = await create_context_with_compressor(config, token_counter=mock_counter)

            rounds = []
            for i in range(5):
                rounds.append(UserMessage(content=f"Q{i}"))
                rounds.append(AssistantMessage(content=f"A{i}"))

            await ctx.add_messages(rounds)

            result = ctx.get_messages()
            last_user = next(m for m in result if m.content == "Q4")
            last_ai = next(m for m in result if m.content == "A4")
            assert not isinstance(last_user, OffloadMixin)
            assert not isinstance(last_ai, OffloadMixin)

    @pytest.mark.asyncio
    async def test_reloader_tool_restores_original_messages(self):
        """Reloader tool returns original messages for offloaded content."""
        mock_user = MagicMock()
        mock_user.parser_content = {"summary": "Summarized users."}
        mock_ai = MagicMock()
        mock_ai.parser_content = {"summary": "Summarized assistants."}

        mock_counter = MagicMock()
        mock_counter.count_messages = MagicMock(return_value=20000)

        with patch(
            "openjiuwen.core.context_engine.processor.compressor.round_level_compressor.Model"
        ) as mock_model_cls:
            mock_model = MagicMock()
            mock_model.invoke = AsyncMock(side_effect=[mock_user, mock_ai])
            mock_model_cls.return_value = mock_model

            config = RoundLevelCompressorConfig(
                rounds_threshold=3,
                tokens_threshold=5000,
                keep_last_round=False,
            )
            ctx = await create_context_with_compressor(config, token_counter=mock_counter)

            original_contents = [
                ("Unique user content X", "Unique assistant content Y"),
                ("Unique user content X2", "Unique assistant content Y2"),
                ("Unique user content X3", "Unique assistant content Y3"),
            ]
            rounds = []
            for u, a in original_contents:
                rounds.append(UserMessage(content=u))
                rounds.append(AssistantMessage(content=a))

            await ctx.add_messages(rounds)

            result = ctx.get_messages()
            offloaded = [m for m in result if isinstance(m, OffloadMixin)]
            assert len(offloaded) >= 2

            for offload_msg in offloaded[:2]:
                reloaded = await ctx.reloader_tool().invoke(
                    dict(
                        offload_handle=offload_msg.offload_handle,
                        offload_type="in_memory",
                    )
                )
                assert "Unique" in reloaded
                assert "content" in reloaded

    @pytest.mark.asyncio
    async def test_insufficient_rounds_no_compression(self):
        """When rounds are below rounds_threshold, no compression occurs."""
        mock_counter = MagicMock()
        mock_counter.count_messages = MagicMock(return_value=15000)

        config = RoundLevelCompressorConfig(
            rounds_threshold=10,
            tokens_threshold=5000,
            keep_last_round=False,
        )
        with patch(
            "openjiuwen.core.context_engine.processor.compressor.round_level_compressor.Model"
        ) as mock_model_cls:
            mock_model_cls.return_value = MagicMock()
            ctx = await create_context_with_compressor(config, token_counter=mock_counter)

            rounds = [
                UserMessage(content="u1"),
                AssistantMessage(content="a1"),
                UserMessage(content="u2"),
                AssistantMessage(content="a2"),
            ]
            await ctx.add_messages(rounds)

            result = ctx.get_messages()
            assert len(result) == 4
            assert not any(isinstance(m, OffloadMixin) for m in result)

    @pytest.mark.asyncio
    async def test_customized_compression_prompt_used(self):
        """Customized compression prompt is passed to Model.invoke."""
        mock_user = MagicMock()
        mock_user.parser_content = {"summary": "Custom user summary."}
        mock_ai = MagicMock()
        mock_ai.parser_content = {"summary": "Custom ai summary."}

        mock_counter = MagicMock()
        mock_counter.count_messages = MagicMock(return_value=12000)

        custom_prompt = "Custom round compression prompt."

        with patch(
            "openjiuwen.core.context_engine.processor.compressor.round_level_compressor.Model"
        ) as mock_model_cls:
            mock_model = MagicMock()
            mock_model.invoke = AsyncMock(side_effect=[mock_user, mock_ai])
            mock_model_cls.return_value = mock_model

            config = RoundLevelCompressorConfig(
                rounds_threshold=3,
                tokens_threshold=5000,
                keep_last_round=False,
                customized_compression_prompt=custom_prompt,
            )
            ctx = await create_context_with_compressor(config, token_counter=mock_counter)

            rounds = []
            for i in range(4):
                rounds.append(UserMessage(content=f"u{i}"))
                rounds.append(AssistantMessage(content=f"a{i}"))
            await ctx.add_messages(rounds)

            call_args_list = mock_model.invoke.call_args_list
            assert len(call_args_list) >= 2
            for call_args in call_args_list[:2]:
                messages_passed = call_args[0][0]
                assert messages_passed[0].content == custom_prompt
