# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""
Unit tests for KVCacheManager behavior inside ContextEngine (context layer only).

**Scope**: ContextEngine + ModelContext.get_context_window() + KVCacheManager. No ReActAgent.
Verifies:
- When enable_kv_cache_release=True and context changes (MessageOffloader / DialogueCompressor
  modify messages), KVCacheManager.release() is invoked with the configured model.
- When enable_kv_cache_release=False, release() is never called.
- Multiple compression/offload rounds are correctly tracked (release on context change).

Does NOT cover:
ReActAgent entry (warning when non-InferenceAffinity, model injection) or
end-to-end invoke flow; see test_react_agent_kv_cache_release.py and
test_inference_affinity_kv_cache_release_with_processors.py.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import List, Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openjiuwen.core.context_engine import ContextEngine
from openjiuwen.core.context_engine.schema.config import ContextEngineConfig
from openjiuwen.core.context_engine.processor.compressor.dialogue_compressor import (
    DialogueCompressorConfig,
)
from openjiuwen.core.context_engine.processor.offloader.message_offloader import (
    MessageOffloaderConfig,
)
from openjiuwen.core.foundation.llm import UserMessage, ToolMessage, AssistantMessage, ToolCall
from openjiuwen.core.foundation.llm.inference_affinity_model import InferenceAffinityModel
from openjiuwen.core.foundation.llm.schema.config import ModelClientConfig

pytestmark = pytest.mark.asyncio


def create_tool_call_list(ids: List[str]) -> List[ToolCall]:
    return [ToolCall(id=tc, name="test-tool", type="function", arguments="") for tc in ids]


class _FakeInferenceAffinityModel(InferenceAffinityModel):
    """Fake model implementation for testing KV cache release behavior.

    This test double records all release() calls for verification without
    requiring a real model client configuration.
    """
    def __init__(
            self,
            model_client_config=None,
            model_config=None,
            model_name: str = "fake-model"
    ):
        # Intentionally skip parent __init__ to avoid requiring real client config
        # This is a test double that only needs to pass isinstance check
        if model_client_config is None:
            model_client_config = MagicMock(spec=ModelClientConfig)
        if model_config is None:
            model_config = SimpleNamespace(model_name=model_name)
        self.release_calls: list[dict] = []
        try:
            super().__init__(model_client_config, model_config)
        except Exception:
            # If parent init fails, manually set required attributes
            self.model_config = SimpleNamespace(model_name=model_name)
            self.model_client_config = model_client_config
            self._client = None

    async def release(
            self,
            session_id: str,
            messages: List,
            messages_released_index: int,
            *,
            tools: List = None,
            tools_released_index: Optional[int] = None,
            model: str = None
    ) -> bool:
        self.release_calls.append({
            "cache_salt": session_id,
            "block_released": "MOCK_BLOCK_RELEASED_NUM",
        })
        return True


class TestKVCacheManager:
    async def test_kv_cache_release_triggered_after_message_offloader(self):
        """Test that KVCacheManager.release is triggered when MessageOffloader modifies context.

        Flow:
        1. Create ContextEngine with enable_kv_cache_release=True
        2. Create context with MessageOffloader processor
        3. Add messages with large tool content that triggers offload
        4. First get_context_window - KVCacheManager snapshots, no release
        5. Add new messages triggering offload which modifies previous messages
        6. Second get_context_window - release should be called due to message content change
        """
        # Create ContextEngine with kv_cache enabled
        engine_config = ContextEngineConfig(
            enable_kv_cache_release=True,
            default_window_message_num=100
        )
        engine = ContextEngine(engine_config)

        # MessageOffloader config - low thresholds to trigger offload
        offloader_config = MessageOffloaderConfig(
            messages_threshold=3,
            tokens_threshold=100000,
            large_message_threshold=50,  # Messages longer than 50 chars will be offloaded
            trim_size=10,  # Keep first 10 chars when offloading
            offload_message_type=["tool"],
            keep_last_round=False,
        )

        # Create mock model for release tracking
        fake_model = _FakeInferenceAffinityModel(model_name="test-model")

        # Initial messages with a large tool message (will be offloaded later)
        original_tool_content = "A" * 100  # Large content that exceeds large_message_threshold
        initial_messages = [
            UserMessage(content="u0"),
            ToolMessage(content=original_tool_content, tool_call_id="t1"),
            AssistantMessage(content="a0"),
        ]

        # Create context with offloader
        context = await engine.create_context(
            "offload_ctx",
            None,
            history_messages=initial_messages,
            processors=[("MessageOffloader", offloader_config)],
            token_counter=None,
        )

        # First get_context_window - snapshots the window, no release should happen
        window1 = await context.get_context_window(model=fake_model)
        assert fake_model.release_calls == [], "First get_context_window should not trigger release"

        # Add new messages to trigger offload (exceeds messages_threshold)
        # This will cause MessageOffloader to offload the large tool message
        await context.add_messages([
            UserMessage(content="Follow up question"),
            AssistantMessage(content="Follow up answer"),
        ])

        # Second get_context_window - should detect message change and trigger release
        window2 = await context.get_context_window(model=fake_model)

        # Verify release was called due to context change from offload
        assert len(fake_model.release_calls) == 1, \
            "Release should be triggered after context changed by offload"

    async def test_kv_cache_release_triggered_after_dialogue_compression(self):
        """Test that KVCacheManager.release is triggered when DialogueCompressor modifies context.

        Flow:
        1. Create ContextEngine with enable_kv_cache_release=True
        2. Create context with DialogueCompressor processor
        3. Add messages that trigger compression threshold
        4. First get_context_window - KVCacheManager snapshots, no release
        5. Second get_context_window after compression modifies messages
           - release should be called due to message content change
        """
        # Mock the compression model response
        mock_response = MagicMock()
        mock_response.parser_content = {"summary": "Through test-tool, obtained: summarized result."}

        with patch(
            "openjiuwen.core.context_engine.processor.compressor.dialogue_compressor.Model"
        ) as mock_model_cls:
            mock_model = MagicMock()
            mock_model.invoke = AsyncMock(return_value=mock_response)
            mock_model_cls.return_value = mock_model

            # Create ContextEngine with kv_cache enabled
            engine_config = ContextEngineConfig(
                enable_kv_cache_release=True,
                default_window_message_num=100
            )
            engine = ContextEngine(engine_config)

            # DialogueCompressor config - low threshold to trigger compression
            compressor_config = DialogueCompressorConfig(
                messages_threshold=2,
                tokens_threshold=100000,
                keep_last_round=False,
            )

            # Create mock model for release tracking
            fake_model = _FakeInferenceAffinityModel(model_name="test-model")

            # Initial messages (before compression)
            initial_messages = [
                UserMessage(content="Call the tool"),
                AssistantMessage(
                    content="",
                    tool_calls=create_tool_call_list(["tc-1"]),
                ),
                ToolMessage(content="Tool result: important data xyz", tool_call_id="tc-1"),
                AssistantMessage(content="Based on the result, the answer is X."),
            ]

            # Create context with compressor
            context = await engine.create_context(
                "test_ctx",
                None,
                history_messages=initial_messages.copy(),
                processors=[("DialogueCompressor", compressor_config)],
                token_counter=None,
            )

            # First get_context_window - triggers compression via add_messages
            # This snapshots the window, no release should happen yet
            window1 = await context.get_context_window(model=fake_model)
            assert fake_model.release_calls == [], "First get_context_window should not trigger release"

            # Now the context has been compressed (messages modified)
            # Get messages to verify compression happened
            compressed_messages = context.get_messages()

            # Add a new user message to simulate continued conversation
            await context.add_messages(UserMessage(content="Follow up question"))

            # Second get_context_window - should detect message change and trigger release
            window2 = await context.get_context_window(model=fake_model)

            # Verify release was called due to context change from compression
            assert len(fake_model.release_calls) == 1, \
                "Release should be triggered after context changed by compression"

    async def test_kv_cache_release_with_multiple_compression_rounds(self):
        """
        Test that KVCacheManager correctly tracks and releases cache
        across multiple compression rounds.
        """
        mock_response = MagicMock()
        mock_response.parser_content = {"summary": "Compressed round content."}

        with patch(
            "openjiuwen.core.context_engine.processor.compressor.dialogue_compressor.Model"
        ) as mock_model_cls:
            mock_model = MagicMock()
            mock_model.invoke = AsyncMock(return_value=mock_response)
            mock_model_cls.return_value = mock_model

            engine_config = ContextEngineConfig(
                enable_kv_cache_release=True,
                default_window_message_num=100
            )
            engine = ContextEngine(engine_config)

            compressor_config = DialogueCompressorConfig(
                messages_threshold=6,
                tokens_threshold=100000,
                messages_to_keep=4,
                keep_last_round=True,
            )

            fake_model = _FakeInferenceAffinityModel(model_name="test-model")

            # Create context with multiple rounds of messages
            initial_messages = []
            for r in range(2):
                initial_messages.extend([
                    UserMessage(content=f"Round {r} request"),
                    AssistantMessage(
                        content="",
                        tool_calls=create_tool_call_list([f"tc-{r}"]),
                    ),
                    ToolMessage(content=f"Round {r} tool output data", tool_call_id=f"tc-{r}"),
                    AssistantMessage(content=f"Round {r} final answer."),
                ])

            context = await engine.create_context(
                "multi_round_ctx",
                None,
                history_messages=initial_messages,
                processors=[("DialogueCompressor", compressor_config)],
                token_counter=None,
            )

            # First window - snapshot only
            _ = await context.get_context_window(model=fake_model)
            initial_release_count = len(fake_model.release_calls)

            # Add more messages to trigger another compression
            await context.add_messages([
                UserMessage(content="New round request"),
                AssistantMessage(
                    content="",
                    tool_calls=create_tool_call_list(["tc-new"]),
                ),
                ToolMessage(content="New tool output", tool_call_id="tc-new"),
                AssistantMessage(content="New round answer."),
            ])

            # Second window - should detect changes and release
            _ = await context.get_context_window(model=fake_model)

            # Verify release was called
            assert len(fake_model.release_calls) > initial_release_count, \
                "Release should be triggered after adding new messages and compression"

    async def test_kv_cache_no_release_when_disabled(self):
        """
        Test that no release is triggered when enable_kv_cache_release is False.
        """
        mock_response = MagicMock()
        mock_response.parser_content = {"summary": "Compressed content."}

        with patch(
            "openjiuwen.core.context_engine.processor.compressor.dialogue_compressor.Model"
        ) as mock_model_cls:
            mock_model = MagicMock()
            mock_model.invoke = AsyncMock(return_value=mock_response)
            mock_model_cls.return_value = mock_model

            # Create engine with kv_cache DISABLED
            engine_config = ContextEngineConfig(
                enable_kv_cache_release=False,
                default_window_message_num=100
            )
            engine = ContextEngine(engine_config)

            compressor_config = DialogueCompressorConfig(
                messages_threshold=2,
                tokens_threshold=100000,
                keep_last_round=False,
            )

            fake_model = _FakeInferenceAffinityModel(model_name="test-model")

            initial_messages = [
                UserMessage(content="Call the tool"),
                AssistantMessage(
                    content="",
                    tool_calls=create_tool_call_list(["tc-1"]),
                ),
                ToolMessage(content="Tool result data", tool_call_id="tc-1"),
                AssistantMessage(content="Answer based on tool."),
            ]

            context = await engine.create_context(
                "no_cache_ctx",
                None,
                history_messages=initial_messages,
                processors=[("DialogueCompressor", compressor_config)],
                token_counter=None,
            )

            # Multiple window accesses with message additions
            _ = await context.get_context_window(model=fake_model)
            await context.add_messages(UserMessage(content="Follow up"))
            _ = await context.get_context_window(model=fake_model)

            # No release should be called when kv_cache is disabled
            assert len(fake_model.release_calls) == 0, \
                "No release should be triggered when enable_kv_cache_release is False"