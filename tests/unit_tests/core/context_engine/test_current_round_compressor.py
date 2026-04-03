# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from unittest.mock import MagicMock, AsyncMock, patch
from typing import List

import pytest

from openjiuwen.core.context_engine import ContextEngine, ContextEngineConfig
from openjiuwen.core.context_engine.processor.compressor.current_round_compressor import (
    CurrentRoundCompressorConfig,
)
from openjiuwen.core.context_engine.schema.messages import OffloadUserMessage, OffloadAssistantMessage, \
    OffloadToolMessage
from openjiuwen.core.foundation.llm import (
    UserMessage,
    AssistantMessage,
    ToolMessage,
    ToolCall,
)


def create_tool_call_list(ids: List[str], names: List[str] = None) -> List[ToolCall]:
    """Create a list of ToolCall objects."""
    names = names or ["test-tool"] * len(ids)
    return [ToolCall(id=tc_id, name=tc_name, type="function", arguments="") 
            for tc_id, tc_name in zip(ids, names)]


async def create_context_with_compressor(
    compressor_config: CurrentRoundCompressorConfig,
    history_messages=None,
    token_counter=None,
):
    """Create context with CurrentRoundCompressor via ContextEngine.create_context."""
    engine = ContextEngine(ContextEngineConfig(default_window_message_num=100))
    return await engine.create_context(
        "test_ctx",
        None,
        history_messages=history_messages or [],
        processors=[("CurrentRoundCompressor", compressor_config)],
        token_counter=token_counter,
    )


def create_mock_token_counter(return_value: int = 100):
    """Create a mock token counter that returns a specific value."""
    mock_counter = MagicMock()
    mock_counter.count_messages = MagicMock(return_value=return_value)
    return mock_counter


def create_length_token_counter():
    """Create a mock token counter that scales with message content length."""
    mock_counter = MagicMock()
    mock_counter.count_messages = MagicMock(
        side_effect=lambda messages: sum(len(getattr(message, "content", "") or "") for message in messages)
    )
    return mock_counter

offload_type = (OffloadUserMessage, OffloadAssistantMessage, OffloadToolMessage)


class TestCurrentRoundCompressor:
    """CurrentRoundCompressor unit tests: single message compression scenarios."""

    @pytest.mark.asyncio
    async def test_large_message_compression_triggered(self):
        """When message token count exceeds threshold, the large tool message is compressed.
        
        Note: CurrentRoundCompressor compresses messages AFTER the last UserMessage.
        So we need UserMessage followed by assistant/tool messages.
        """
        mock_response = MagicMock()
        mock_response.content = "Compressed: tool execution result."

        mock_token_counter = create_length_token_counter()

        with patch(
            "openjiuwen.core.context_engine.processor.compressor.current_round_compressor.Model"
        ) as mock_model_cls:
            mock_model = MagicMock()
            mock_model.invoke = AsyncMock(return_value=mock_response)
            mock_model_cls.return_value = mock_model

            config = CurrentRoundCompressorConfig(
                tokens_threshold=100,
                min_selected_tokens_for_compression=1,
                messages_to_keep=1,  # Keep 1 message (the last UserMessage)
            )
            ctx = await create_context_with_compressor(config, token_counter=mock_token_counter)

            # Create a large tool message that exceeds threshold
            large_content = "根据用户传入的参数a:10,b:20，通过调用_add_2025工具得到的结果是-6。" * 10

            msgs = [
                UserMessage(content="First message"),  # This is the UserMessage
                AssistantMessage(
                    content="",
                    tool_calls=create_tool_call_list(["tc-1"], ["_add_2025"]),
                ),
                ToolMessage(content=large_content, tool_call_id="tc-1"),
                AssistantMessage(
                    content="",
                    tool_calls=create_tool_call_list(["tc-1"], ["_add_2025"]),
                ),
            ]
            await ctx.add_messages(msgs)

            result = ctx.get_messages()
            # After compression, the large tool message should be replaced with OffloadMixin
            offloaded = [m for m in result if isinstance(m, offload_type)]
            assert len(offloaded) == 1

    @pytest.mark.asyncio
    async def test_compression_with_assistant_and_tool_messages(self):
        """Compression should handle assistant and tool messages correctly."""
        mock_response = MagicMock()
        mock_response.content = "Through _add_2025 tool, obtained: result is -6."

        mock_token_counter = create_length_token_counter()

        with patch(
            "openjiuwen.core.context_engine.processor.compressor.current_round_compressor.Model"
        ) as mock_model_cls:
            mock_model = MagicMock()
            mock_model.invoke = AsyncMock(return_value=mock_response)
            mock_model_cls.return_value = mock_model

            config = CurrentRoundCompressorConfig(
                messages_to_keep=1,
                tokens_threshold=100,
                min_selected_tokens_for_compression=1,
            )
            ctx = await create_context_with_compressor(config, token_counter=mock_token_counter)
            large_content = "根据用户传入的参数a:10,b:20，通过调用_add_2025工具得到的结果是-6。" * 10

            msgs = [
                UserMessage(content="Calculate 10 + 20"),  # UserMessage first
                AssistantMessage(
                    content="",
                    tool_calls=create_tool_call_list(["tc-1"], ["_add_2025"]),
                ),
                ToolMessage(content=large_content, tool_call_id="tc-1"),
                AssistantMessage(
                    content="",
                    tool_calls=create_tool_call_list(["tc-1"], ["_add_2025"]),
                ),
                ToolMessage(content=large_content, tool_call_id="tc-1"),
                AssistantMessage(content="The answer is -6."),
            ]
            await ctx.add_messages(msgs)

            result = ctx.get_messages()
            # Verify compression happened
            offloaded = [m for m in result if isinstance(m, offload_type)]
            assert len(offloaded) >= 1

    @pytest.mark.asyncio
    async def test_compression_with_multi_assistant_and_tool_messages(self):
        """Compression should handle assistant and tool messages correctly."""
        mock_response = MagicMock()
        mock_response.content = "Through _add_2025 tool, obtained: result is -6."

        mock_token_counter = create_length_token_counter()

        with patch(
            "openjiuwen.core.context_engine.processor.compressor.current_round_compressor.Model"
        ) as mock_model_cls:
            mock_model = MagicMock()
            mock_model.invoke = AsyncMock(return_value=mock_response)
            mock_model_cls.return_value = mock_model

            config = CurrentRoundCompressorConfig(
                messages_to_keep=1,
                tokens_threshold=100,
                min_selected_tokens_for_compression=1,
            )
            ctx = await create_context_with_compressor(config, token_counter=mock_token_counter)
            large_content = "根据用户传入的参数a:10,b:20，通过调用_add_2025工具得到的结果是-6。" * 10

            msgs = [
                UserMessage(content="Calculate 10 + 20"),  # UserMessage first
                AssistantMessage(
                    content="",
                    tool_calls=create_tool_call_list(["tc-1"], ["_add_2025"]),
                ),
                ToolMessage(content=large_content, tool_call_id="tc-1"),
                AssistantMessage(
                    content="",
                    tool_calls=create_tool_call_list(["tc-1"], ["_add_2025"]),
                ),
                ToolMessage(content=large_content, tool_call_id="tc-1"),
                AssistantMessage(content="The answer is -6."),
            ]
            await ctx.add_messages(msgs)

            result = ctx.get_messages()
            # Verify compression happened
            offloaded = [m for m in result if isinstance(m, offload_type)]
            assert len(offloaded) >= 1


    @pytest.mark.asyncio
    async def test_no_compression_below_threshold(self):
        """When message size is below threshold, no compression occurs."""
        mock_token_counter = create_mock_token_counter(10)

        with patch(
            "openjiuwen.core.context_engine.processor.compressor.current_round_compressor.Model"
        ) as mock_model_cls:
            mock_model = MagicMock()
            mock_model_cls.return_value = mock_model

            config = CurrentRoundCompressorConfig(
                messages_threshold=10,
                large_message_threshold=1000,  # High threshold
                messages_to_keep=1,
            )
            ctx = await create_context_with_compressor(config, token_counter=mock_token_counter)

            msgs = [
                UserMessage(content="Short message"),
                AssistantMessage(content="Short response"),
            ]
            await ctx.add_messages(msgs)

            result = ctx.get_messages()
            assert len(result) >= 2
            assert not any(isinstance(m, offload_type) for m in result)


    @pytest.mark.asyncio
    async def test_no_compression_when_usermessage_is_last(self):
        """No compression when the last message is UserMessage (get_compress_idx returns -1)."""
        mock_model = MagicMock()
        mock_model.invoke = AsyncMock()
        
        mock_token_counter = create_mock_token_counter(10)

        with patch(
            "openjiuwen.core.context_engine.processor.compressor.current_round_compressor.Model",
            MagicMock(return_value=mock_model)
        ):
            config = CurrentRoundCompressorConfig(
                messages_threshold=10,
                large_message_threshold=10,
                messages_to_keep=1,
            )
            ctx = await create_context_with_compressor(config, token_counter=mock_token_counter)

            msgs = [
                UserMessage(content="First message"),
                AssistantMessage(content="Response"),
                UserMessage(content="Last message is user"),  # Last message is UserMessage
            ]
            await ctx.add_messages(msgs)

            result = ctx.get_messages()
            assert len(result) >= 3
            # Model.invoke should NOT be called when last message is UserMessage
            mock_model.invoke.assert_not_called()

