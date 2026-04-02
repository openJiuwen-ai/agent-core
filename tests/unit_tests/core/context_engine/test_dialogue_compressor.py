# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openjiuwen.core.context_engine.processor.compressor.dialogue_compressor import (
    DialogueCompressor,
    DialogueCompressorConfig,
    _DIALOGUE_MEMORY_BLOCK_MARKER,
)
from openjiuwen.core.foundation.llm import AssistantMessage, ToolCall, ToolMessage, UserMessage


def create_tool_call_list(ids: list[str]) -> list[ToolCall]:
    return [ToolCall(id=tool_call_id, name="test-tool", type="function", arguments="") for tool_call_id in ids]


class _TestableDialogueCompressor(DialogueCompressor):
    async def build_memory_message_for_test(
        self,
        context,
        source_messages,
        summary: str,
    ):
        return await self._build_memory_message(context, source_messages, summary)

    def _has_compression_benefit(self, context, original_messages, replacement_messages) -> bool:
        return True


class TestDialogueCompressor:
    @pytest.mark.asyncio
    async def test_trigger_add_messages_uses_character_fallback_without_token_counter(self):
        with patch(
            "openjiuwen.core.context_engine.processor.compressor.dialogue_compressor.Model"
        ) as mock_model_cls:
            mock_model_cls.return_value = MagicMock()
            compressor = DialogueCompressor(
                DialogueCompressorConfig(
                    messages_threshold=100,
                    tokens_threshold=100,
                    keep_last_round=False,
                    offload_writeback_enabled=False,
                )
            )
            context = MagicMock()
            context.__len__.return_value = 1
            context.get_messages.return_value = [AssistantMessage(content="A" * 180)]
            context.token_counter.return_value = None

            triggered = await compressor.trigger_add_messages(
                context,
                [AssistantMessage(content="B" * 180)],
            )

            assert triggered is True

    @pytest.mark.asyncio
    async def test_on_add_messages_replaces_finished_round_with_memory_block(self):
        mock_response = MagicMock()
        mock_response.parser_content = {
            "blocks": [
                {
                    "block_id": "react_1",
                    "summary": "Final Result: X.",
                }
            ]
        }
        mock_response.content = ""

        with patch(
            "openjiuwen.core.context_engine.processor.compressor.dialogue_compressor.Model"
        ) as mock_model_cls:
            mock_model = MagicMock()
            mock_model.invoke = AsyncMock(return_value=mock_response)
            mock_model_cls.return_value = mock_model

            compressor = _TestableDialogueCompressor(
                DialogueCompressorConfig(
                    messages_threshold=2,
                    keep_last_round=False,
                    offload_writeback_enabled=False,
                )
            )
            context = MagicMock()
            context.get_messages.return_value = []
            context.token_counter.return_value = None

            messages_to_add = [
                UserMessage(content="Call the tool"),
                AssistantMessage(content="", tool_calls=create_tool_call_list(["tc-1"])),
                ToolMessage(content="Tool result: data", tool_call_id="tc-1"),
                AssistantMessage(content="Based on the result, the answer is X."),
            ]

            event, remaining = await compressor.on_add_messages(context, messages_to_add)

            assert remaining == []
            assert event is not None
            assert event.messages_to_modify == [1, 2, 3]
            updated_messages = context.set_messages.call_args[0][0]
            assert len(updated_messages) == 2
            assert updated_messages[0].content == "Call the tool"
            assert isinstance(updated_messages[1], UserMessage)
            assert updated_messages[1].content.startswith(_DIALOGUE_MEMORY_BLOCK_MARKER)
            assert "Final Result" in updated_messages[1].content

    @pytest.mark.asyncio
    async def test_build_memory_message_offload_falls_back_to_plain_user_message(self):
        with patch(
            "openjiuwen.core.context_engine.processor.compressor.dialogue_compressor.Model"
        ) as mock_model_cls:
            mock_model_cls.return_value = MagicMock()
            compressor = _TestableDialogueCompressor(DialogueCompressorConfig())
            compressor.offload_messages = AsyncMock(return_value=None)
            context = MagicMock()

            message = await compressor.build_memory_message_for_test(
                context,
                [AssistantMessage(content="historical final answer")],
                "User Requirements:\n- Keep details.\n\nFinal Result:\n- Done.",
            )

            assert isinstance(message, UserMessage)
            assert message.content.startswith(_DIALOGUE_MEMORY_BLOCK_MARKER)

    @pytest.mark.asyncio
    async def test_builtin_compression_prompt_used_as_system_prompt(self):
        mock_response = MagicMock()
        mock_response.parser_content = {
            "blocks": [
                {
                    "block_id": "react_1",
                    "summary": "User Requirements:\n- Keep details.\n\nFinal Result:\n- Done.",
                }
            ]
        }
        mock_response.content = ""

        with patch(
            "openjiuwen.core.context_engine.processor.compressor.dialogue_compressor.Model"
        ) as mock_model_cls:
            mock_model = MagicMock()
            mock_model.invoke = AsyncMock(return_value=mock_response)
            mock_model_cls.return_value = mock_model

            compressor = DialogueCompressor(
                DialogueCompressorConfig(
                    messages_threshold=2,
                    keep_last_round=False,
                    offload_writeback_enabled=False,
                )
            )
            context = MagicMock()
            context.get_messages.return_value = []
            context.token_counter.return_value = None

            await compressor.on_add_messages(
                context,
                [
                    UserMessage(content="u"),
                    AssistantMessage(content="", tool_calls=create_tool_call_list(["tc-1"])),
                    ToolMessage(content="tool output", tool_call_id="tc-1"),
                    AssistantMessage(content="final answer"),
                ],
            )

            model_messages = mock_model.invoke.call_args[0][0]
            assert "Task Data Preservation Expert" in model_messages[0].content
