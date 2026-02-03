# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from unittest.mock import MagicMock, AsyncMock, patch
from typing import List

import pytest

from openjiuwen.core.context_engine import ContextEngine, ContextEngineConfig
from openjiuwen.core.context_engine.processor.compressor.dialogue_compressor import (
    DialogueCompressorConfig,
)
from openjiuwen.core.context_engine.schema.messages import OffloadMixin
from openjiuwen.core.foundation.llm import (
    UserMessage,
    AssistantMessage,
    ToolMessage,
    ToolCall,
)


def create_tool_call_list(ids: List[str]) -> List[ToolCall]:
    return [ToolCall(id=tc, name="test-tool", type="function", arguments="") for tc in ids]


async def create_context_with_compressor(
    compressor_config: DialogueCompressorConfig,
    history_messages=None,
    token_counter=None,
):
    """Create context with DialogueCompressor via ContextEngine.create_context."""
    engine = ContextEngine(ContextEngineConfig(default_window_message_num=100))
    return await engine.create_context(
        "test_ctx",
        None,
        history_messages=history_messages or [],
        processors=[("DialogueCompressor", compressor_config)],
        token_counter=token_counter,
    )


class TestDialogueCompressor:
    """DialogueCompressor unit tests: full scenarios from create_context."""

    @pytest.mark.asyncio
    async def test_messages_threshold_triggers_compression(self):
        """When message count exceeds threshold, tool-call rounds are compressed."""
        mock_response = MagicMock()
        mock_response.parser_content = {"summary": "Through test-tool, obtained: summarized result."}

        with patch(
            "openjiuwen.core.context_engine.processor.compressor.dialogue_compressor.Model"
        ) as mock_model_cls:
            mock_model = MagicMock()
            mock_model.invoke = AsyncMock(return_value=mock_response)
            mock_model_cls.return_value = mock_model

            config = DialogueCompressorConfig(
                messages_threshold=2,
                tokens_threshold=100000,
                keep_last_round=False,
            )
            ctx = await create_context_with_compressor(config)

            msgs = [
                UserMessage(content="Call the tool"),
                AssistantMessage(
                    content="",
                    tool_calls=create_tool_call_list(["tc-1"]),
                ),
                ToolMessage(content="Tool result: data", tool_call_id="tc-1"),
                AssistantMessage(content="Based on the result, the answer is X."),
            ]
            await ctx.add_messages(msgs)

            result = ctx.get_messages()
            assert len(result) == 2
            assert result[0].content == "Call the tool"
            assert isinstance(result[1], OffloadMixin)
            assert "summarized result" in result[1].content or "Through" in result[1].content

            reloaded = await ctx.reloader_tool().invoke(
                dict(offload_handle=result[1].offload_handle, offload_type="in_memory")
            )
            assert "Tool result" in reloaded or "data" in reloaded

    @pytest.mark.asyncio
    async def test_tokens_threshold_triggers_compression(self):
        """When token count exceeds threshold, compression is triggered."""
        mock_response = MagicMock()
        mock_response.parser_content = {"summary": "Compressed: tool output and final reply."}

        mock_counter = MagicMock()
        mock_counter.count_messages = MagicMock(return_value=20000)

        with patch(
            "openjiuwen.core.context_engine.processor.compressor.dialogue_compressor.Model"
        ) as mock_model_cls:
            mock_model = MagicMock()
            mock_model.invoke = AsyncMock(return_value=mock_response)
            mock_model_cls.return_value = mock_model

            config = DialogueCompressorConfig(
                messages_threshold=100,
                tokens_threshold=5000,
                keep_last_round=False,
            )
            ctx = await create_context_with_compressor(config, token_counter=mock_counter)

            msgs = [
                UserMessage(content="Use the tool"),
                AssistantMessage(
                    content="",
                    tool_calls=create_tool_call_list(["tc-1"]),
                ),
                ToolMessage(content="Result from tool", tool_call_id="tc-1"),
                AssistantMessage(content="Here is the answer."),
            ]
            await ctx.add_messages(msgs)

            result = ctx.get_messages()
            assert len(result) == 2
            assert isinstance(result[1], OffloadMixin)
            assert "Compressed" in result[1].content

    @pytest.mark.asyncio
    async def test_keep_last_round_preserves_final_assistant(self):
        """With keep_last_round=True, the last round is not compressed."""
        mock_response = MagicMock()
        mock_response.parser_content = {"summary": "Compressed first round."}

        with patch(
            "openjiuwen.core.context_engine.processor.compressor.dialogue_compressor.Model"
        ) as mock_model_cls:
            mock_model = MagicMock()
            mock_model.invoke = AsyncMock(return_value=mock_response)
            mock_model_cls.return_value = mock_model

            config = DialogueCompressorConfig(
                messages_threshold=5,
                keep_last_round=True,
            )
            ctx = await create_context_with_compressor(config)

            msgs = [
                UserMessage(content="First: call tool"),
                AssistantMessage(content="", tool_calls=create_tool_call_list(["tc-1"])),
                ToolMessage(content="First tool result", tool_call_id="tc-1"),
                AssistantMessage(content="First round answer."),
                UserMessage(content="Second: call tool"),
                AssistantMessage(content="", tool_calls=create_tool_call_list(["tc-2"])),
                ToolMessage(content="Second tool result", tool_call_id="tc-2"),
                AssistantMessage(content="Second round final answer."),
            ]
            await ctx.add_messages(msgs)

            result = ctx.get_messages()
            last_final = next(m for m in result if m.content == "Second round final answer.")
            assert not isinstance(last_final, OffloadMixin)
            offloaded = [m for m in result if isinstance(m, OffloadMixin)]
            assert len(offloaded) >= 1

    @pytest.mark.asyncio
    async def test_messages_to_keep_below_no_compression(self):
        """When message count is below messages_to_keep, no compression."""
        mock_counter = MagicMock()
        mock_counter.count_messages = MagicMock(return_value=20000)
        config = DialogueCompressorConfig(
            tokens_threshold=10000,
            messages_to_keep=15,
            keep_last_round=False,
        )
        with patch(
            "openjiuwen.core.context_engine.processor.compressor.dialogue_compressor.Model"
        ) as mock_model_cls:
            mock_model = MagicMock()
            mock_model_cls.return_value = mock_model
            ctx = await create_context_with_compressor(config, token_counter=mock_counter)

            msgs = [
                UserMessage(content="u1"),
                AssistantMessage(content="a1", tool_calls=create_tool_call_list(["tc-1"])),
                ToolMessage(content="t1", tool_call_id="tc-1"),
                AssistantMessage(content="a2"),
            ]
            await ctx.add_messages(msgs)

            result = ctx.get_messages()
            assert len(result) == 4
            assert not any(isinstance(m, OffloadMixin) for m in result)

    @pytest.mark.asyncio
    async def test_multi_round_compresses_earlier_rounds(self):
        """Multiple tool-call rounds: earlier rounds compressed, content reloadable."""
        mock_response = MagicMock()
        mock_response.parser_content = {
            "summary": "Through test-tool, obtained: round 1 compressed."
        }

        with patch(
            "openjiuwen.core.context_engine.processor.compressor.dialogue_compressor.Model"
        ) as mock_model_cls:
            mock_model = MagicMock()
            mock_model.invoke = AsyncMock(return_value=mock_response)
            mock_model_cls.return_value = mock_model

            config = DialogueCompressorConfig(
                messages_threshold=10,
                tokens_threshold=100000,
                messages_to_keep=4,
                keep_last_round=True,
            )
            ctx = await create_context_with_compressor(config)

            rounds = []
            for r in range(3):
                rounds.extend([
                    UserMessage(content=f"Round {r} request"),
                    AssistantMessage(
                        content="",
                        tool_calls=create_tool_call_list([f"tc-{r}"]),
                    ),
                    ToolMessage(content=f"Round {r} tool output", tool_call_id=f"tc-{r}"),
                    AssistantMessage(content=f"Round {r} final answer."),
                ])
            await ctx.add_messages(rounds)

            result = ctx.get_messages()
            assert len(result) < 12
            offloaded = [m for m in result if isinstance(m, OffloadMixin)]
            assert len(offloaded) == 2
            reloaded = await ctx.reloader_tool().invoke(
                dict(offload_handle=offloaded[0].offload_handle, offload_type="in_memory")
            )
            assert "Round" in reloaded or "tool" in reloaded.lower()

    @pytest.mark.asyncio
    async def test_customized_compression_prompt_used(self):
        """Customized compression prompt is passed to Model.invoke."""
        mock_response = MagicMock()
        mock_response.parser_content = {"summary": "Custom compressed."}

        with patch(
            "openjiuwen.core.context_engine.processor.compressor.dialogue_compressor.Model"
        ) as mock_model_cls:
            mock_model = MagicMock()
            mock_model.invoke = AsyncMock(return_value=mock_response)
            mock_model_cls.return_value = mock_model

            custom_prompt = "Custom: compress the following dialogue."
            config = DialogueCompressorConfig(
                messages_threshold=3,
                tokens_threshold=100000,
                keep_last_round=False,
                customized_compression_prompt=custom_prompt,
            )
            ctx = await create_context_with_compressor(config)

            msgs = [
                UserMessage(content="u"),
                AssistantMessage(content="", tool_calls=create_tool_call_list(["tc-1"])),
                ToolMessage(content="t", tool_call_id="tc-1"),
                AssistantMessage(content="a"),
            ]
            await ctx.add_messages(msgs)

            call_args = mock_model.invoke.call_args
            messages_passed = call_args[0][0]
            assert isinstance(messages_passed, list)
            assert messages_passed[0].content == custom_prompt
