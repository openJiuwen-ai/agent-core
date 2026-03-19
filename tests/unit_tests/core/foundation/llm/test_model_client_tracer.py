# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openjiuwen.core.foundation.llm.model_clients import OpenAIModelClient
from openjiuwen.core.foundation.llm.model_clients.siliconflow_model_client import SiliconFlowModelClient
from openjiuwen.core.foundation.llm.schema.config import ModelClientConfig, ModelRequestConfig, ProviderType
from openjiuwen.core.foundation.llm.schema.message import UserMessage


@pytest.fixture
def openai_client_config():
    """Create OpenAI client config for testing."""
    return ModelClientConfig(
        client_provider=ProviderType.OpenAI,
        api_key="sk-test",
        api_base="https://api.openai.com/v1",
        verify_ssl=False,
    )


@pytest.fixture
def siliconflow_client_config():
    """Create SiliconFlow client config for testing."""
    return ModelClientConfig(
        client_provider=ProviderType.SiliconFlow,
        api_key="sk-test",
        api_base="https://api.siliconflow.cn/v1",
        verify_ssl=False,
    )


@pytest.fixture
def model_request_config():
    """Create model request config for testing."""
    return ModelRequestConfig(
        model_name="gpt-3.5-turbo",
        temperature=0.7,
    )


class TestOpenAIModelClientTracer:
    """Test OpenAIModelClient tracer_record_data functionality."""

    @pytest.mark.asyncio
    async def test_invoke_calls_tracer_record_data_with_result(
        self, openai_client_config, model_request_config
    ):
        """Test that invoke calls tracer_record_data with llm_result parameter."""
        client = OpenAIModelClient(model_request_config, openai_client_config)

        # Mock response
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message = MagicMock()
        mock_response.choices[0].message.content = "Test response"
        mock_response.choices[0].message.tool_calls = None
        mock_response.choices[0].message.reasoning_content = None
        mock_response.usage = MagicMock()
        mock_response.usage.prompt_tokens = 10
        mock_response.usage.completion_tokens = 20
        mock_response.usage.total_tokens = 30
        mock_response.usage.prompt_tokens_details = None

        mock_async_client = AsyncMock()
        mock_async_client.chat.completions.create = AsyncMock(return_value=mock_response)

        with patch.object(
            client, "_create_async_openai_client", return_value=mock_async_client
        ):
            tracer_mock = AsyncMock()

            messages = [UserMessage(content="Hello")]
            await client.invoke(messages, tracer_record_data=tracer_mock)

            # Verify tracer_record_data was called with llm_response
            # It should be called twice: once with llm_params, once with llm_response
            assert tracer_mock.call_count == 2
            call_kwargs = tracer_mock.call_args_list[1].kwargs
            assert "llm_response" in call_kwargs
            result = call_kwargs["llm_response"]
            assert result.content == "Test response"

    @pytest.mark.asyncio
    async def test_stream_accumulates_final_message_and_calls_tracer(
        self, openai_client_config, model_request_config
    ):
        """Test that stream accumulates final_message and calls tracer_record_data."""
        client = OpenAIModelClient(model_request_config, openai_client_config)

        # Create mock streaming chunks
        chunks = []
        for i, content in enumerate(["Hello", " ", "world", "!"]):
            chunk = MagicMock()
            chunk.choices = [MagicMock()]
            chunk.choices[0].delta = MagicMock()
            chunk.choices[0].delta.content = content
            chunk.choices[0].delta.reasoning_content = None
            chunk.choices[0].delta.tool_calls = None
            chunk.choices[0].finish_reason = None
            chunk.usage = None
            chunks.append(chunk)

        # Make last chunk have finish_reason
        chunks[-1].choices[0].finish_reason = "stop"

        mock_async_client = AsyncMock()

        async def chunk_generator():
            for chunk in chunks:
                yield chunk

        mock_async_client.chat.completions.create = AsyncMock(return_value=chunk_generator())

        with patch.object(
            client, "_create_async_openai_client", return_value=mock_async_client
        ):
            tracer_mock = AsyncMock()

            messages = [UserMessage(content="Hello")]

            collected_chunks = []
            async for chunk in client.stream(messages, tracer_record_data=tracer_mock):
                collected_chunks.append(chunk)

            # Verify tracer_record_data was called
            assert tracer_mock.call_count == 2
            call_kwargs = tracer_mock.call_args_list[1].kwargs
            assert "llm_response" in call_kwargs
            result = call_kwargs["llm_response"]

            # Verify final_message has accumulated content
            assert result.content == "Hello world!"


class TestSiliconFlowModelClientTracer:
    """Test SiliconFlowModelClient tracer_record_data functionality."""

    @pytest.mark.asyncio
    async def test_invoke_calls_tracer_record_data_with_result(
        self, siliconflow_client_config, model_request_config
    ):
        """Test that invoke calls tracer_record_data with llm_result parameter."""
        client = SiliconFlowModelClient(model_request_config, siliconflow_client_config)

        # Mock response data
        mock_response_data = {
            "choices": [
                {
                    "message": {
                        "content": "Test response",
                        "tool_calls": None,
                    },
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": 10,
                "completion_tokens": 20,
                "total_tokens": 30,
            },
        }

        # Create proper async context manager mock
        mock_response = AsyncMock()
        mock_response.json = AsyncMock(return_value=mock_response_data)

        @asynccontextmanager
        async def mock_post_gen(params, timeout=None):
            yield mock_response

        with patch.object(client, "_apost", side_effect=mock_post_gen):
            tracer_mock = AsyncMock()

            messages = [UserMessage(content="Hello")]
            await client.invoke(messages, tracer_record_data=tracer_mock)

            # Verify tracer_record_data was called with llm_response
            assert tracer_mock.call_count == 2
            call_kwargs = tracer_mock.call_args_list[1].kwargs
            assert "llm_response" in call_kwargs
            result = call_kwargs["llm_response"]
            assert result.content == "Test response"

    @pytest.mark.asyncio
    async def test_stream_accumulates_final_message_and_calls_tracer(
        self, siliconflow_client_config, model_request_config
    ):
        """Test that stream accumulates final_message and calls tracer_record_data."""
        client = SiliconFlowModelClient(model_request_config, siliconflow_client_config)

        # Create mock SSE chunks
        chunks = [
            b'data: {"choices": [{"delta": {"content": "Hello"}}]}\n',
            b'data: {"choices": [{"delta": {"content": " "}}]}\n',
            b'data: {"choices": [{"delta": {"content": "world"}}]}\n',
            b'data: {"choices": [{"delta": {"content": "!"}, "finish_reason": "stop"}]}\n',
            b'data: [DONE]\n',
        ]

        mock_response = AsyncMock()

        async def content_gen():
            for chunk in chunks:
                yield chunk

        mock_response.content = content_gen()

        @asynccontextmanager
        async def mock_post_gen(params, timeout=None):
            yield mock_response

        with patch.object(client, "_apost", side_effect=mock_post_gen):
            tracer_mock = AsyncMock()

            messages = [UserMessage(content="Hello")]

            collected_chunks = []
            async for chunk in client.stream(messages, tracer_record_data=tracer_mock):
                collected_chunks.append(chunk)

            # Verify tracer_record_data was called
            assert tracer_mock.call_count == 2
            call_kwargs = tracer_mock.call_args_list[1].kwargs
            assert "llm_response" in call_kwargs
            result = call_kwargs["llm_response"]

            # Verify final_message has accumulated content
            assert result.content == "Hello world!"

    @pytest.mark.asyncio
    async def test_invoke_without_tracer_does_not_fail(
        self, openai_client_config, model_request_config
    ):
        """Test that invoke works without tracer_record_data parameter."""
        client = OpenAIModelClient(model_request_config, openai_client_config)

        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message = MagicMock()
        mock_response.choices[0].message.content = "Test response"
        mock_response.choices[0].message.tool_calls = None
        mock_response.choices[0].message.reasoning_content = None
        mock_response.usage = MagicMock()
        mock_response.usage.prompt_tokens = 10
        mock_response.usage.completion_tokens = 20
        mock_response.usage.total_tokens = 30
        mock_response.usage.prompt_tokens_details = None

        mock_async_client = AsyncMock()
        mock_async_client.chat.completions.create = AsyncMock(return_value=mock_response)

        with patch.object(
            client, "_create_async_openai_client", return_value=mock_async_client
        ):
            messages = [UserMessage(content="Hello")]
            result = await client.invoke(messages)
            assert result.content == "Test response"

    @pytest.mark.asyncio
    async def test_stream_without_tracer_does_not_fail(
        self, openai_client_config, model_request_config
    ):
        """Test that stream works without tracer_record_data parameter."""
        client = OpenAIModelClient(model_request_config, openai_client_config)

        chunk = MagicMock()
        chunk.choices = [MagicMock()]
        chunk.choices[0].delta = MagicMock()
        chunk.choices[0].delta.content = "Hello"
        chunk.choices[0].delta.reasoning_content = None
        chunk.choices[0].delta.tool_calls = None
        chunk.choices[0].finish_reason = "stop"
        chunk.usage = None

        mock_async_client = AsyncMock()

        async def chunk_generator():
            yield chunk

        mock_async_client.chat.completions.create = AsyncMock(return_value=chunk_generator())

        with patch.object(
            client, "_create_async_openai_client", return_value=mock_async_client
        ):
            messages = [UserMessage(content="Hello")]
            collected = []
            async for c in client.stream(messages):
                collected.append(c)

            assert len(collected) > 0
            assert collected[0].content == "Hello"
