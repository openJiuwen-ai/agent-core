# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
# pylint: disable=protected-access

import asyncio
import json
import sys
import types
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import BaseModel


def _mock_urlencode(value, **kwargs):
    return value


oauthlib_module = types.ModuleType("oauthlib")
oauthlib_common_module = types.ModuleType("oauthlib.common")
oauthlib_common_module.urlencode = _mock_urlencode
oauthlib_module.common = oauthlib_common_module
sys.modules.setdefault("oauthlib", oauthlib_module)
sys.modules.setdefault("oauthlib.common", oauthlib_common_module)

openai_module = types.ModuleType("openai")
openai_module.BaseModel = BaseModel
sys.modules.setdefault("openai", openai_module)

dashscope_module = types.ModuleType("dashscope")
dashscope_module.MultiModalConversation = type("MultiModalConversation", (), {})
dashscope_module.VideoSynthesis = type("VideoSynthesis", (), {})
sys.modules.setdefault("dashscope", dashscope_module)


def _mock_is_successful(*args, **kwargs):
    return True


pymilvus_module = types.ModuleType("pymilvus")
pymilvus_client_module = types.ModuleType("pymilvus.client")
pymilvus_utils_module = types.ModuleType("pymilvus.client.utils")
pymilvus_utils_module.is_successful = _mock_is_successful
pymilvus_client_module.utils = pymilvus_utils_module
pymilvus_module.client = pymilvus_client_module
sys.modules.setdefault("pymilvus", pymilvus_module)
sys.modules.setdefault("pymilvus.client", pymilvus_client_module)
sys.modules.setdefault("pymilvus.client.utils", pymilvus_utils_module)

from openjiuwen.core.common.exception.errors import BaseError
from openjiuwen.core.context_engine import ContextEngineConfig
from openjiuwen.core.context_engine.context.context import SessionModelContext
from openjiuwen.core.context_engine.processor.offloader.message_summary_offloader import (
    TRUNCATED_MARKER,
    MessageSummaryOffloader,
    MessageSummaryOffloaderConfig,
)
from openjiuwen.core.context_engine.processor.offloader.message_offloader import MessageOffloader
from openjiuwen.core.context_engine.schema.messages import OffloadMixin
from openjiuwen.core.foundation.llm import AssistantMessage, ToolMessage, UserMessage


class ToolMessageWithMetadata(ToolMessage):
    metadata: dict = {}


class TestMessageSummaryOffloader:
    @pytest.fixture
    def adaptive_config(self):
        return MessageSummaryOffloaderConfig(
            enable_adaptive_compression=True,
            large_message_threshold=10,
            summary_max_tokens=128,
            step_summary_max_context_messages=3,
            content_max_chars_for_compression=60,
        )

    @pytest.fixture
    def legacy_config(self):
        return MessageSummaryOffloaderConfig(
            enable_adaptive_compression=False,
            messages_threshold=5,
            messages_to_keep=2,
        )

    @pytest.fixture
    def context(self):
        return SessionModelContext(
            "context_id",
            "session_id",
            ContextEngineConfig(),
            history_messages=[],
        )

    @staticmethod
    def test_trigger_add_messages_only_for_large_tool_messages(adaptive_config, context):
        with patch("openjiuwen.core.context_engine.processor.offloader.message_summary_offloader.Model"):
            offloader = MessageSummaryOffloader(adaptive_config)

        small_tool = ToolMessage(content="small", tool_call_id="tool-small")
        large_user = UserMessage(content="x" * 100)
        large_tool = ToolMessage(content="x" * 100, tool_call_id="tool-large")

        assert asyncio.run(offloader.trigger_add_messages(context, [small_tool])) is False
        assert asyncio.run(offloader.trigger_add_messages(context, [large_user])) is False
        assert asyncio.run(offloader.trigger_add_messages(context, [large_tool])) is True

    @staticmethod
    def test_on_add_messages_offloads_only_new_large_tool_message(adaptive_config, context):
        with patch("openjiuwen.core.context_engine.processor.offloader.message_summary_offloader.Model") as model_cls:
            model = MagicMock()
            model.invoke = AsyncMock(
                return_value=AssistantMessage(
                    content=json.dumps(
                        {
                            "compression_strategy": "extractive",
                            "summary": "condensed tool output",
                            "offload_data_explanation": {
                                "category": "raw tool output",
                                "description": "full output retained in offload storage",
                                "inferability": "medium",
                            },
                        }
                    )
                )
            )
            model_cls.return_value = model
            offloader = MessageSummaryOffloader(adaptive_config)

        user_message = UserMessage(content="Please inspect the tool output")
        context.set_messages([user_message])
        tool_message = ToolMessageWithMetadata(
            content="x" * 120,
            tool_call_id="call_1",
            metadata={"source": "weather-tool"},
        )
        small_tool_message = ToolMessage(content="ok", tool_call_id="call_2")

        event, processed = asyncio.run(offloader.on_add_messages(context, [tool_message, small_tool_message]))

        assert event is not None
        assert event.messages_to_modify == [1]
        assert isinstance(processed[0], OffloadMixin)
        assert processed[1] is small_tool_message
        assert "condensed tool output" in processed[0].content
        assert "[offloaded_info]" in processed[0].content
        assert "category: raw tool output" in processed[0].content
        assert processed[0].metadata["source"] == "weather-tool"

        offload_state = context.save_state()["offload_messages"]
        assert processed[0].offload_handle in offload_state
        assert offload_state[processed[0].offload_handle][0].content == tool_message.content

    @staticmethod
    def test_legacy_mode_delegates_to_parent_hooks(legacy_config, context):
        with patch("openjiuwen.core.context_engine.processor.offloader.message_summary_offloader.Model"):
            offloader = MessageSummaryOffloader(legacy_config)

        tool_message = ToolMessage(content="x" * 100, tool_call_id="call_legacy")

        with patch.object(
            offloader.__class__.__bases__[0],
            "trigger_add_messages",
            new=AsyncMock(return_value=True),
        ) as trigger_mock:
            assert asyncio.run(offloader.trigger_add_messages(context, [tool_message])) is True
            trigger_mock.assert_awaited_once()

        with patch.object(
            offloader.__class__.__bases__[0],
            "on_add_messages",
            new=AsyncMock(return_value=("event", [tool_message])),
        ) as on_add_mock:
            event, messages = asyncio.run(offloader.on_add_messages(context, [tool_message]))
            assert event == "event"
            assert messages == [tool_message]
            on_add_mock.assert_awaited_once()

    @staticmethod
    def test_get_function_call_from_chain_returns_raw_tool_call(adaptive_config, context):
        with patch("openjiuwen.core.context_engine.processor.offloader.message_summary_offloader.Model"):
            offloader = MessageSummaryOffloader(adaptive_config)

        raw_tool_call = {
            "id": "call_123",
            "type": "function",
            "function": {"name": "get_weather", "arguments": '{"city": "Beijing"}'},
        }
        assistant_message = AssistantMessage(content="", tool_calls=[raw_tool_call])
        tool_message = ToolMessage(content="{}", tool_call_id="call_123")

        result = offloader._get_function_call_from_chain(tool_message, [assistant_message])

        assert result is not None
        assert getattr(result, "id", None) == "call_123"
        assert getattr(result, "name", None) == "get_weather"
        assert getattr(result, "arguments", None) == '{"city": "Beijing"}'

    @staticmethod
    def test_precise_step_uses_recent_message_limit(adaptive_config):
        adaptive_config.enable_precise_step = True
        adaptive_config.step_summary_max_context_messages = 2

        with patch("openjiuwen.core.context_engine.processor.offloader.message_summary_offloader.Model") as model_cls:
            model = MagicMock()
            model.invoke = AsyncMock(return_value=AssistantMessage(content="latest task"))
            model_cls.return_value = model
            offloader = MessageSummaryOffloader(adaptive_config)

        messages = [
            UserMessage(content="first task"),
            AssistantMessage(content="first answer"),
            UserMessage(content="second task"),
            AssistantMessage(content="second answer"),
        ]

        result = asyncio.run(offloader._get_step_from_chain_precise(messages))

        assert result == "latest task"
        prompt = model.invoke.await_args.args[0][0].content
        assert "first task" not in prompt
        assert "first answer" not in prompt
        assert "second task" in prompt
        assert "second answer" in prompt

    @staticmethod
    def test_compress_with_fallback_uses_configured_char_budget(adaptive_config):
        adaptive_config.content_max_chars_for_compression = 50

        with patch("openjiuwen.core.context_engine.processor.offloader.message_summary_offloader.Model") as model_cls:
            model = MagicMock()
            model.invoke = AsyncMock(
                side_effect=[
                    Exception("context length exceeded"),
                    AssistantMessage(
                        content=json.dumps(
                            {
                                "compression_strategy": "abstractive",
                                "summary": "fallback summary",
                                "offload_data_explanation": {},
                            }
                        )
                    ),
                ]
            )
            model_cls.return_value = model
            offloader = MessageSummaryOffloader(adaptive_config)

        result = asyncio.run(
            offloader._compress_with_fallback(
                step="summarize",
                function_call={"name": "tool", "arguments": "{}"},
                tool_content="A" * 500,
            )
        )

        assert result["summary"] == "fallback summary"
        assert model.invoke.await_count == 2
        retry_prompt = model.invoke.await_args_list[1].args[0][0].content
        assert TRUNCATED_MARKER in retry_prompt

    @staticmethod
    def test_offload_prompt_contains_default_step_and_function_call(adaptive_config, context):
        with patch("openjiuwen.core.context_engine.processor.offloader.message_summary_offloader.Model") as model_cls:
            model = MagicMock()
            model.invoke = AsyncMock(
                return_value=AssistantMessage(
                    content=json.dumps(
                        {
                            "compression_strategy": "extractive",
                            "summary": "prompt checked",
                            "offload_data_explanation": {},
                        }
                    )
                )
            )
            model_cls.return_value = model
            offloader = MessageSummaryOffloader(adaptive_config)

        user_message = UserMessage(content="Look at the weather tool result")
        assistant_message = AssistantMessage(
            content="",
            tool_calls=[
                {
                    "id": "call_abc",
                    "type": "function",
                    "function": {
                        "name": "get_weather",
                        "arguments": '{"city":"Shenzhen"}',
                    },
                }
            ],
        )
        context.set_messages([user_message, assistant_message])
        tool_message = ToolMessage(content="temperature=28", tool_call_id="call_abc")

        asyncio.run(offloader._offload_message(tool_message, context))

        prompt = model.invoke.await_args.args[0][0].content
        assert "Look at the weather tool result" in prompt
        assert "get_weather" in prompt
        assert "Shenzhen" in prompt
        assert "temperature=28" in prompt

    @staticmethod
    def test_precise_step_retries_with_trimmed_messages(adaptive_config):
        adaptive_config.enable_precise_step = True
        adaptive_config.step_summary_max_context_messages = 4

        with patch("openjiuwen.core.context_engine.processor.offloader.message_summary_offloader.Model") as model_cls:
            model = MagicMock()
            model.invoke = AsyncMock(
                side_effect=[
                    Exception("context window exceeded"),
                    AssistantMessage(content="trimmed task"),
                ]
            )
            model_cls.return_value = model
            offloader = MessageSummaryOffloader(adaptive_config)

        messages = [
            UserMessage(content="task 1"),
            AssistantMessage(content="reply 1"),
            UserMessage(content="task 2"),
            AssistantMessage(content="reply 2"),
        ]

        result = asyncio.run(offloader._get_step_from_chain_precise(messages))

        assert result == "trimmed task"
        first_prompt = model.invoke.await_args_list[0].args[0][0].content
        second_prompt = model.invoke.await_args_list[1].args[0][0].content
        assert "task 1" in first_prompt
        assert "reply 1" in first_prompt
        assert "task 1" not in second_prompt
        assert "reply 1" not in second_prompt
        assert "task 2" in second_prompt
        assert "reply 2" in second_prompt

    @staticmethod
    def test_build_compression_attempts_respect_configured_limits(adaptive_config):
        adaptive_config.content_max_chars_for_compression = 60

        with patch("openjiuwen.core.context_engine.processor.offloader.message_summary_offloader.Model"):
            offloader = MessageSummaryOffloader(adaptive_config)

        attempts = offloader._build_compression_attempts("A" * 300)

        assert len(attempts) == 3
        assert attempts[0] == "A" * 300
        assert len(attempts[1]) <= 60
        assert len(attempts[2]) <= 30
        assert TRUNCATED_MARKER in attempts[1]

    @staticmethod
    def test_parse_compression_result_accepts_embedded_json(adaptive_config):
        with patch("openjiuwen.core.context_engine.processor.offloader.message_summary_offloader.Model"):
            offloader = MessageSummaryOffloader(adaptive_config)

        payload = """```json
        {
          "compression_strategy": "extractive",
          "summary": "important facts",
          "offload_data_explanation": {
            "category": "details",
            "description": "full output",
            "inferability": "low"
          }
        }
        ```"""

        result = offloader._parse_compression_result(payload)

        assert result["summary"] == "important facts"
        assert result["compression_strategy"] == "extractive"

    @staticmethod
    def test_parse_compression_result_requires_summary(adaptive_config):
        with patch("openjiuwen.core.context_engine.processor.offloader.message_summary_offloader.Model"):
            offloader = MessageSummaryOffloader(adaptive_config)

        with pytest.raises(BaseError):
            offloader._parse_compression_result('{"compression_strategy": "extractive"}')

    @staticmethod
    def test_compress_with_fallback_uses_raw_llm_output_when_json_parse_fails_but_is_shorter(
        adaptive_config
    ):
        with patch("openjiuwen.core.context_engine.processor.offloader.message_summary_offloader.Model") as model_cls:
            model = MagicMock()
            model.invoke = AsyncMock(return_value=AssistantMessage(content="plain fallback summary"))
            model_cls.return_value = model
            offloader = MessageSummaryOffloader(adaptive_config)

        result = asyncio.run(
            offloader._compress_with_fallback(
                step="summarize",
                function_call={"name": "tool", "arguments": "{}"},
                tool_content="A" * 100,
            )
        )

        assert result is not None
        assert result["summary"] == "plain fallback summary"
        assert result["offload_data_explanation"] == {}

    @staticmethod
    def test_offload_message_adaptive_keeps_original_message_when_fallback_is_not_shorter(
        adaptive_config, context
    ):
        with patch("openjiuwen.core.context_engine.processor.offloader.message_summary_offloader.Model") as model_cls:
            model = MagicMock()
            model.invoke = AsyncMock(return_value=AssistantMessage(content="B" * 200))
            model_cls.return_value = model
            offloader = MessageSummaryOffloader(adaptive_config)

        user_message = UserMessage(content="Please inspect the tool output")
        context.set_messages([user_message])
        tool_message = ToolMessage(content="A" * 120, tool_call_id="call_keep_original")

        result = asyncio.run(offloader._offload_message(tool_message, context))

        assert result is tool_message

    @staticmethod
    def test_should_offload_message_respects_role_and_size(adaptive_config, context):
        """Test _should_offload_message only processes large tool messages."""
        with patch("openjiuwen.core.context_engine.processor.offloader.message_summary_offloader.Model"):
            offloader = MessageSummaryOffloader(adaptive_config)

        # Small tool message should not be offloaded
        small_tool = ToolMessage(content="small", tool_call_id="tool-small")
        assert offloader._should_offload_message(small_tool, context) is False

        # Large user message should not be offloaded
        large_user = UserMessage(content="x" * 100)
        assert offloader._should_offload_message(large_user, context) is False

        # Already offloaded message should be skipped - create a mock that satisfies OffloadMixin check
        offloaded_tool = MagicMock(spec=ToolMessage)
        offloaded_tool.role = "tool"
        offloaded_tool.content = "x" * 100
        # Make isinstance check return True for OffloadMixin
        offloaded_tool.__class__ = type('OffloadedTool', (ToolMessage, OffloadMixin), {})
        # Manually set the OffloadMixin attributes
        offloaded_tool.offload_handle = "handle-123"
        offloaded_tool.offload_type = "in_memory"

        # Patch isinstance to recognize this as OffloadMixin
        with patch("openjiuwen.core.context_engine.processor.offloader.message_summary_offloader.isinstance",
                   side_effect=lambda obj, cls: True if cls is OffloadMixin else isinstance(obj, cls)):
            result = offloader._should_offload_message(offloaded_tool, context)
            assert result is False

    @staticmethod
    def test_message_size_uses_token_counter_when_available(adaptive_config):
        """Test _message_size prefers token count over char count."""
        with patch("openjiuwen.core.context_engine.processor.offloader.message_summary_offloader.Model"):
            offloader = MessageSummaryOffloader(adaptive_config)

        # Create context with token counter
        token_counter = MagicMock()
        token_counter.count_messages.return_value = 500
        context = MagicMock()
        context.token_counter.return_value = token_counter

        message = ToolMessage(content="x" * 1000, tool_call_id="tool-1")
        size = offloader._message_size(message, context)

        assert size == 500
        token_counter.count_messages.assert_called_once()

    @staticmethod
    def test_message_size_falls_back_to_char_division(adaptive_config):
        """Test _message_size falls back to char length / 3 when no token counter."""
        with patch("openjiuwen.core.context_engine.processor.offloader.message_summary_offloader.Model"):
            offloader = MessageSummaryOffloader(adaptive_config)

        context = MagicMock()
        context.token_counter.return_value = None

        message = ToolMessage(content="x" * 99, tool_call_id="tool-1")
        size = offloader._message_size(message, context)

        assert size == 33  # 99 / 3

    @staticmethod
    def test_smart_truncate_content_preserves_head_middle_tail(adaptive_config):
        """Test _smart_truncate_content preserves three sections."""
        with patch("openjiuwen.core.context_engine.processor.offloader.message_summary_offloader.Model"):
            offloader = MessageSummaryOffloader(adaptive_config)

        content = "ABCDEFGHIJKLMNOPQRSTUVWXYZ" * 10
        max_chars = 100

        truncated = offloader._smart_truncate_content(content, max_chars)

        assert "...[TRUNCATED]..." in truncated
        # Should contain parts from beginning, middle, and end
        assert truncated.startswith("A")
        assert truncated.endswith("Z")

    @staticmethod
    def test_smart_truncate_content_returns_original_if_short_enough(adaptive_config):
        """Test _smart_truncate_content returns original when under limit."""
        with patch("openjiuwen.core.context_engine.processor.offloader.message_summary_offloader.Model"):
            offloader = MessageSummaryOffloader(adaptive_config)

        content = "short content"
        result = offloader._smart_truncate_content(content, 100)

        assert result == content

    @staticmethod
    def test_is_context_overflow_error_detects_keywords(adaptive_config):
        """Test _is_context_overflow_error detects overflow keywords."""
        with patch("openjiuwen.core.context_engine.processor.offloader.message_summary_offloader.Model"):
            offloader = MessageSummaryOffloader(adaptive_config)

        # Should detect overflow errors
        assert offloader._is_context_overflow_error(Exception("context length exceeded")) is True
        assert offloader._is_context_overflow_error(Exception("token limit reached")) is True
        assert offloader._is_context_overflow_error(Exception("prompt is too long")) is True

        # Should not detect other errors
        assert offloader._is_context_overflow_error(Exception("network timeout")) is False
        assert offloader._is_context_overflow_error(Exception("invalid api key")) is False

    @staticmethod
    def test_tool_call_matches_id_handles_dict_and_object(adaptive_config):
        """Test _tool_call_matches_id handles both dict and object formats."""
        with patch("openjiuwen.core.context_engine.processor.offloader.message_summary_offloader.Model"):
            offloader = MessageSummaryOffloader(adaptive_config)

        # Dict format
        dict_call = {"id": "call-123", "name": "test_tool"}
        assert offloader._tool_call_matches_id(dict_call, "call-123") is True
        assert offloader._tool_call_matches_id(dict_call, "call-456") is False

        # Object format
        obj_call = MagicMock()
        obj_call.id = "call-456"
        assert offloader._tool_call_matches_id(obj_call, "call-456") is True
        assert offloader._tool_call_matches_id(obj_call, "call-123") is False

    @staticmethod
    def test_get_step_from_chain_default_extracts_user_content(adaptive_config, context):
        """Test _get_step_from_chain_default extracts last user message."""
        with patch("openjiuwen.core.context_engine.processor.offloader.message_summary_offloader.Model"):
            offloader = MessageSummaryOffloader(adaptive_config)

        messages = [
            UserMessage(content="First user message"),
            AssistantMessage(content="Assistant response"),
            UserMessage(content="Latest user message"),
            AssistantMessage(content="Another response"),
        ]

        step = offloader._get_step_from_chain_default(messages)
        assert step == "Latest user message"

    @staticmethod
    def test_get_step_from_chain_default_returns_empty_when_no_user(adaptive_config, context):
        """Test _get_step_from_chain_default returns empty when no user message."""
        with patch("openjiuwen.core.context_engine.processor.offloader.message_summary_offloader.Model"):
            offloader = MessageSummaryOffloader(adaptive_config)

        messages = [
            AssistantMessage(content="Assistant only"),
            ToolMessage(content="Tool response", tool_call_id="tool-1"),
        ]

        step = offloader._get_step_from_chain_default(messages)
        assert step == ""

    @staticmethod
    def test_validate_config_skips_when_adaptive_enabled(adaptive_config):
        """Test _validate_config skips parent validation in adaptive mode."""
        with patch("openjiuwen.core.context_engine.processor.offloader.message_summary_offloader.Model"):
            offloader = MessageSummaryOffloader(adaptive_config)

        # Should not raise even with invalid parent config
        offloader._validate_config()  # Should pass without error

    @staticmethod
    def test_validate_config_calls_parent_when_adaptive_disabled(legacy_config):
        """Test _validate_config validates messages_to_keep in non-adaptive mode."""
        with patch("openjiuwen.core.context_engine.processor.offloader.message_summary_offloader.Model"):
            offloader = MessageSummaryOffloader(legacy_config)
            # In non-adaptive mode, it should check messages_to_keep config
            # No exception should be raised for valid config
            offloader._validate_config()
            # Test passes if no exception

    @staticmethod
    def test_non_adaptive_mode_delegates_trigger_to_parent(legacy_config, context):
        """Test trigger_add_messages delegates to parent in non-adaptive mode."""
        with patch("openjiuwen.core.context_engine.processor.offloader.message_summary_offloader.Model"):
            with patch.object(MessageOffloader, 'trigger_add_messages', return_value=True) as parent_trigger:
                offloader = MessageSummaryOffloader(legacy_config)
                result = asyncio.run(offloader.trigger_add_messages(context, []))
                assert result is True
                parent_trigger.assert_called_once()

    @staticmethod
    def test_non_adaptive_mode_delegates_on_add_to_parent(legacy_config, context):
        """Test on_add_messages delegates to parent in non-adaptive mode."""
        with patch("openjiuwen.core.context_engine.processor.offloader.message_summary_offloader.Model"):
            with patch.object(MessageOffloader, 'on_add_messages', return_value=(None, [])) as parent_on_add:
                offloader = MessageSummaryOffloader(legacy_config)
                result = asyncio.run(offloader.on_add_messages(context, []))
                assert result == (None, [])

