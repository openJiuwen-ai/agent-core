# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openjiuwen.core.common.exception.errors import BaseError
from openjiuwen.core.context_engine import ContextEngineConfig
from openjiuwen.core.context_engine.processor.offloader.message_summary_offloader import (
    MessageSummaryOffloader,
    MessageSummaryOffloaderConfig,
    DEFAULT_OFFLOAD_SUMMARY_PROMPT
)
from openjiuwen.core.context_engine.schema.messages import OffloadMixin
from openjiuwen.core.context_engine.context.context import SessionModelContext
from openjiuwen.core.foundation.llm import (
    UserMessage,
    AssistantMessage,
    ToolMessage,
    SystemMessage,
    ModelRequestConfig,
    ModelClientConfig
)


class TestMessageSummaryOffloader:
    """Unit tests for MessageSummaryOffloader"""

    @pytest.fixture
    def default_config(self):
        """Create a default configuration for testing"""
        return MessageSummaryOffloaderConfig()

    @pytest.fixture
    def custom_config(self):
        """Create a custom configuration for testing"""
        return MessageSummaryOffloaderConfig(
            messages_threshold=100,
            tokens_threshold=15000,
            large_message_threshold=500,
            offload_message_type=["user", "assistant"],
            messages_to_keep=10,
            keep_last_round=True,
            customized_summary_prompt="Custom summary prompt"
        )

    @pytest.fixture
    def model_config(self):
        """Create a model configuration for testing"""
        return ModelRequestConfig(
            model="test-model",
            temperature=0.7
        )

    @pytest.fixture
    def model_client_config(self):
        """Create a model client configuration for testing"""
        return ModelClientConfig(
            client_id="test-client",
            client_provider="OpenAI",
            api_key="test-key",
            api_base="http://test.api.com"
        )

    @pytest.mark.asyncio
    async def test_init_with_default_config(self, default_config):
        """Test initialization with default configuration"""
        with (patch('openjiuwen.core.context_engine.processor.offloader.message_summary_offloader.Model')
              as mock_model_class):
            mock_model_instance = MagicMock()
            mock_model_class.return_value = mock_model_instance
            
            offloader = MessageSummaryOffloader(default_config)
            
            assert offloader.config == default_config
            mock_model_class.assert_called_once_with(
                model_client_config=None,
                model_config=None
            )

    @pytest.mark.asyncio
    async def test_init_with_custom_config(self, custom_config, model_config, model_client_config):
        """Test initialization with custom configuration including model configs"""
        with (patch('openjiuwen.core.context_engine.processor.offloader.message_summary_offloader.Model')
              as mock_model_class):
            mock_model_instance = MagicMock()
            mock_model_class.return_value = mock_model_instance
            
            custom_config.model = model_config
            custom_config.model_client = model_client_config
            offloader = MessageSummaryOffloader(custom_config)
            
            assert offloader.config == custom_config
            mock_model_class.assert_called_once_with(
                model_client_config=model_client_config,
                model_config=model_config
            )

    @pytest.mark.asyncio
    async def test_offload_message_with_default_prompt(self, default_config):
        """Test _offload_message method with default prompt"""
        original_content = "This is a very long message that needs to be summarized. " * 20
        original_message = UserMessage(content=original_content)
        summarized_content = "This is a summarized version of the message."
        
        with (patch('openjiuwen.core.context_engine.processor.offloader.message_summary_offloader.Model')
              as mock_model_class):
            mock_model_instance = MagicMock()
            mock_model_instance.invoke = AsyncMock(return_value=AssistantMessage(content=summarized_content))
            mock_model_class.return_value = mock_model_instance
            
            offloader = MessageSummaryOffloader(default_config)
            context = SessionModelContext(
                "context_id", "session_id", ContextEngineConfig(), history_messages=[]
            )
            result = await offloader._offload_message(original_message, context)
            
            # Verify result is a OffloadMixin
            reload_messages = await context.reloader_tool().invoke(
                dict(offload_handle=result.offload_handle, offload_type=result.offload_type)
            )
            assert isinstance(result, OffloadMixin)
            assert result.role == original_message.role
            assert summarized_content in result.content
            assert original_message.content in reload_messages
            
            # Verify Model.invoke was called correctly
            mock_model_instance.invoke.assert_called_once()
            call_args = mock_model_instance.invoke.call_args[0][0]
            assert len(call_args) == 2
            assert isinstance(call_args[0], SystemMessage)
            assert call_args[0].content == DEFAULT_OFFLOAD_SUMMARY_PROMPT
            assert isinstance(call_args[1], UserMessage)
            assert call_args[1].content == original_content

    @pytest.mark.asyncio
    async def test_offload_message_with_custom_prompt(self, custom_config):
        """Test _offload_message method with custom prompt"""
        original_content = "This is a very long message that needs to be summarized. " * 20
        original_message = AssistantMessage(content=original_content)
        summarized_content = "Custom summarized version."
        
        with (patch('openjiuwen.core.context_engine.processor.offloader.message_summary_offloader.Model')
              as mock_model_class):
            mock_model_instance = MagicMock()
            mock_model_instance.invoke = AsyncMock(return_value=AssistantMessage(content=summarized_content))
            mock_model_class.return_value = mock_model_instance
            
            offloader = MessageSummaryOffloader(custom_config)
            context = SessionModelContext(
                "context_id", "session_id", ContextEngineConfig(), history_messages=[]
            )
            result = await offloader._offload_message(original_message, context)
            
            # Verify result
            reload_messages = await context.reloader_tool().invoke(
                dict(offload_handle=result.offload_handle, offload_type=result.offload_type)
            )
            assert isinstance(result, OffloadMixin)
            assert result.role == original_message.role
            assert summarized_content in result.content
            assert original_message.content in reload_messages

            # Verify custom prompt was used
            call_args = mock_model_instance.invoke.call_args[0][0]
            assert call_args[0].content == custom_config.customized_summary_prompt

    @pytest.mark.asyncio
    async def test_offload_message_with_different_roles(self, default_config):
        """Test _offload_message with different message roles"""
        test_cases = [
            (UserMessage(content="User message"), "user"),
            (AssistantMessage(content="Assistant message"), "assistant"),
            (ToolMessage(content="Tool message", tool_call_id="123"), "tool"),
        ]
        
        for original_message, expected_role in test_cases:
            summarized_content = f"Summarized {expected_role} message"
            
            with (patch('openjiuwen.core.context_engine.processor.offloader.message_summary_offloader.Model')
                  as mock_model_class):
                mock_model_instance = MagicMock()
                mock_model_instance.invoke = AsyncMock(return_value=AssistantMessage(content=summarized_content))
                mock_model_class.return_value = mock_model_instance
                
                offloader = MessageSummaryOffloader(default_config)
                context = SessionModelContext(
                    "context_id", "session_id", ContextEngineConfig(), history_messages=[]
                )
                result = await offloader._offload_message(original_message, context)

                reload_messages = await context.reloader_tool().invoke(
                    dict(offload_handle=result.offload_handle, offload_type=result.offload_type)
                )
                assert result.role == expected_role
                assert summarized_content in result.content
                assert original_message.content in reload_messages

    @pytest.mark.asyncio
    async def test_validate_config_valid(self):
        """Test _validate_config with valid configuration"""
        # Valid config: messages_to_keep < messages_threshold
        config = MessageSummaryOffloaderConfig(
            messages_to_keep=10,
            messages_threshold=20
        )
        
        with patch('openjiuwen.core.context_engine.processor.offloader.message_summary_offloader.Model'):
            offloader = MessageSummaryOffloader(config)
            # Should not raise any exception
            assert offloader is not None

    @pytest.mark.asyncio
    async def test_validate_config_invalid_messages_to_keep_equals_threshold(self):
        """Test _validate_config raises ValueError when messages_to_keep equals messages_threshold"""
        config = MessageSummaryOffloaderConfig(
            messages_to_keep=20,
            messages_threshold=20
        )
        
        with patch('openjiuwen.core.context_engine.processor.offloader.message_summary_offloader.Model'):
            with pytest.raises(BaseError):
                MessageSummaryOffloader(config)

    @pytest.mark.asyncio
    async def test_validate_config_invalid_messages_to_keep_greater_than_threshold(self):
        """Test _validate_config raises ValueError when messages_to_keep > messages_threshold"""
        config = MessageSummaryOffloaderConfig(
            messages_to_keep=30,
            messages_threshold=20
        )
        
        with patch('openjiuwen.core.context_engine.processor.offloader.message_summary_offloader.Model'):
            with pytest.raises(BaseError):
                MessageSummaryOffloader(config)

    @pytest.mark.asyncio
    async def test_validate_config_no_messages_to_keep(self):
        """Test _validate_config when messages_to_keep is None"""
        config = MessageSummaryOffloaderConfig(
            messages_to_keep=None,
            messages_threshold=20
        )
        
        with patch('openjiuwen.core.context_engine.processor.offloader.message_summary_offloader.Model'):
            offloader = MessageSummaryOffloader(config)
            # Should not raise any exception
            assert offloader is not None

    @pytest.mark.asyncio
    async def test_validate_config_no_messages_threshold(self):
        """Test _validate_config when messages_threshold is None"""
        config = MessageSummaryOffloaderConfig(
            messages_to_keep=10,
            messages_threshold=None
        )
        
        with patch('openjiuwen.core.context_engine.processor.offloader.message_summary_offloader.Model'):
            offloader = MessageSummaryOffloader(config)
            # Should not raise any exception
            assert offloader is not None

    @pytest.mark.asyncio
    async def test_offload_message_empty_content(self, default_config):
        """Test _offload_message with empty content"""
        original_message = UserMessage(content="")
        summarized_content = "Empty message summary"
        
        with (patch('openjiuwen.core.context_engine.processor.offloader.message_summary_offloader.Model')
              as mock_model_class):
            mock_model_instance = MagicMock()
            mock_model_instance.invoke = AsyncMock(return_value=AssistantMessage(content=summarized_content))
            mock_model_class.return_value = mock_model_instance
            
            offloader = MessageSummaryOffloader(default_config)
            context = SessionModelContext(
                "context_id", "session_id", ContextEngineConfig(), history_messages=[]
            )
            result = await offloader._offload_message(original_message, context)

            reload_messages = await context.reloader_tool().invoke(
                dict(offload_handle=result.offload_handle, offload_type=result.offload_type)
            )
            assert summarized_content in result.content
            assert original_message.content in reload_messages

    @pytest.mark.asyncio
    async def test_offload_message_preserves_original_messages(self, default_config):
        """Test that _offload_message correctly stores original messages"""
        original_message = UserMessage(content="Original message content")
        summarized_content = "Summary"
        
        with (patch('openjiuwen.core.context_engine.processor.offloader.message_summary_offloader.Model')
              as mock_model_class):
            mock_model_instance = MagicMock()
            mock_model_instance.invoke = AsyncMock(return_value=AssistantMessage(content=summarized_content))
            mock_model_class.return_value = mock_model_instance
            
            offloader = MessageSummaryOffloader(default_config)
            context = SessionModelContext(
                "context_id", "session_id", ContextEngineConfig(), history_messages=[]
            )
            result = await offloader._offload_message(
                original_message,
                context=context
            )
            
            # Verify original messages are stored
            reload_messages = await context.reloader_tool().invoke(
                dict(offload_handle=result.offload_handle, offload_type=result.offload_type)
            )
            assert "Original message content" in reload_messages, True
