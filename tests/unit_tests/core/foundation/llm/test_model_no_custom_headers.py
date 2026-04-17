# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openjiuwen.core.foundation.llm import (
    Model,
    ModelClientConfig,
    ModelRequestConfig,
    ProviderType,
    UserMessage,
    init_model,
)
from openjiuwen.core.single_agent.agents.react_agent import ReActAgentConfig


def _build_mock_response(content: str = "ok") -> MagicMock:
    response = MagicMock()
    response.choices = [MagicMock()]
    response.choices[0].message = MagicMock()
    response.choices[0].message.content = content
    response.choices[0].message.tool_calls = None
    response.choices[0].message.reasoning_content = None
    response.usage = MagicMock()
    response.usage.prompt_tokens = 5
    response.usage.completion_tokens = 3
    response.usage.total_tokens = 8
    response.usage.prompt_tokens_details = None
    return response


def _build_stream_chunk(content: str = "ok") -> MagicMock:
    chunk = MagicMock()
    chunk.choices = [MagicMock()]
    chunk.choices[0].delta = MagicMock()
    chunk.choices[0].delta.content = content
    chunk.choices[0].delta.reasoning_content = None
    chunk.choices[0].delta.tool_calls = None
    chunk.choices[0].finish_reason = "stop"
    chunk.usage = None
    return chunk


def _build_model_without_headers(mode: str) -> Model:
    if mode == "model":
        return Model(
            model_client_config=ModelClientConfig(
                client_provider=ProviderType.OpenAI,
                api_key="sk-test",
                api_base="https://api.openai.com/v1",
                verify_ssl=False,
            ),
            model_config=ModelRequestConfig(model="gpt-4o-mini"),
        )

    if mode == "init_model":
        return init_model(
            provider=ProviderType.OpenAI.value,
            model_name="gpt-4o-mini",
            api_key="sk-test",
            api_base="https://api.openai.com/v1",
            verify_ssl=False,
        )

    if mode == "react_agent":
        config = ReActAgentConfig()
        config.configure_model_client(
            provider=ProviderType.OpenAI.value,
            api_key="sk-test",
            api_base="https://api.openai.com/v1",
            model_name="gpt-4o-mini",
            verify_ssl=False,
        )
        return Model(
            model_client_config=config.model_client_config,
            model_config=config.model_config_obj,
        )

    raise ValueError(f"Unsupported mode: {mode}")


async def _invoke_and_get_sent_params(mode: str) -> dict:
    model = _build_model_without_headers(mode)
    mock_async_client = AsyncMock()
    mock_async_client.chat.completions.create = AsyncMock(return_value=_build_mock_response())

    with patch.object(model._client, "_create_async_openai_client", return_value=mock_async_client):
        await model.invoke(messages=[UserMessage(content="hello")])

    return mock_async_client.chat.completions.create.call_args.kwargs


async def _stream_and_get_sent_params(mode: str) -> dict:
    model = _build_model_without_headers(mode)

    async def _chunk_generator():
        yield _build_stream_chunk("hello")

    mock_async_client = AsyncMock()
    mock_async_client.chat.completions.create = AsyncMock(return_value=_chunk_generator())

    with patch.object(model._client, "_create_async_openai_client", return_value=mock_async_client):
        async for _ in model.stream(messages=[UserMessage(content="hello")]):
            pass

    return mock_async_client.chat.completions.create.call_args.kwargs


@pytest.mark.parametrize("mode", ["model", "init_model", "react_agent"])
@pytest.mark.asyncio
async def test_invoke_without_custom_headers_does_not_send_extra_headers(mode: str):
    sent_params = await _invoke_and_get_sent_params(mode)

    assert "extra_headers" not in sent_params


@pytest.mark.parametrize("mode", ["model", "init_model", "react_agent"])
@pytest.mark.asyncio
async def test_stream_without_custom_headers_does_not_send_extra_headers(mode: str):
    sent_params = await _stream_and_get_sent_params(mode)

    assert "extra_headers" not in sent_params