# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Tests for OpenAIModelClient shared/cached AsyncOpenAI client lifecycle."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openjiuwen.core.foundation.llm import (
    Model,
    ModelClientConfig,
    ModelRequestConfig,
    ProviderType,
    UserMessage,
)
from openjiuwen.core.foundation.llm.model_clients.openai_model_client import OpenAIModelClient


def _build_mock_response(content: str = "ok") -> MagicMock:
    response = MagicMock()
    response.choices = [MagicMock()]
    response.choices[0].message = MagicMock()
    response.choices[0].message.content = content
    response.choices[0].message.tool_calls = None
    response.choices[0].message.reasoning_content = None
    response.choices[0].finish_reason = "stop"
    response.usage = MagicMock()
    response.usage.prompt_tokens = 5
    response.usage.completion_tokens = 3
    response.usage.total_tokens = 8
    response.usage.prompt_tokens_details = None
    return response


def _make_model(use_shared: bool = True) -> Model:
    return Model(
        model_client_config=ModelClientConfig(
            client_provider=ProviderType.OpenAI,
            api_key="sk-test",
            api_base="https://api.openai.com/v1",
            verify_ssl=False,
            use_shared_llm_http_client=use_shared,
        ),
        model_config=ModelRequestConfig(model="gpt-4o-mini"),
    )


def _mock_async_client() -> AsyncMock:
    mc = AsyncMock()
    mc.chat.completions.create = AsyncMock(return_value=_build_mock_response())
    return mc


@pytest.fixture(autouse=True)
def _clear_client_cache():
    OpenAIModelClient._client_cache.clear()
    yield
    OpenAIModelClient._client_cache.clear()


class TestSharedClientPooling:
    @pytest.mark.asyncio
    async def test_shared_client_is_built_once_and_reused_without_hot_path_close(self):
        model = _make_model(use_shared=True)
        client = model._client

        mock_async_client = _mock_async_client()
        build_mock = MagicMock(return_value=mock_async_client)
        with patch.object(client, "_build_async_openai_client", build_mock):
            await model.invoke(messages=[UserMessage(content="hi")])
            await model.invoke(messages=[UserMessage(content="hi again")])

        # Built once, reused for the second call.
        assert build_mock.call_count == 1
        assert mock_async_client.chat.completions.create.call_count == 2
        # Never closed on the hot path.
        assert mock_async_client.close.call_count == 0

    @pytest.mark.asyncio
    async def test_per_request_timeout_forwarded_without_rebuilding_client(self):
        model = _make_model(use_shared=True)
        client = model._client

        mock_async_client = _mock_async_client()
        build_mock = MagicMock(return_value=mock_async_client)
        with patch.object(client, "_build_async_openai_client", build_mock):
            await model.invoke(messages=[UserMessage(content="hi")], timeout=12.5)

        assert build_mock.call_count == 1
        sent_kwargs = mock_async_client.chat.completions.create.call_args.kwargs
        assert sent_kwargs["timeout"] == 12.5

    @pytest.mark.asyncio
    async def test_aclose_closes_cached_clients_and_clears_cache(self):
        model = _make_model(use_shared=True)
        client = model._client

        mock_async_client = _mock_async_client()
        build_mock = MagicMock(return_value=mock_async_client)
        with patch.object(client, "_build_async_openai_client", build_mock):
            await model.invoke(messages=[UserMessage(content="hi")])

        assert OpenAIModelClient._client_cache

        await OpenAIModelClient.aclose()

        assert mock_async_client.close.call_count == 1
        assert OpenAIModelClient._client_cache == {}


class TestAcloseConnections:
    def _cfg(self, api_base: str) -> ModelClientConfig:
        return ModelClientConfig(
            client_provider=ProviderType.OpenAI,
            api_key="sk-test",
            api_base=api_base,
            verify_ssl=False,
        )

    @pytest.mark.asyncio
    async def test_aclose_connections_closes_only_given(self):
        cfg_keep = self._cfg("https://api.openai.com/v1")
        cfg_drop = self._cfg("https://old.example.com/v1")
        keep_client, drop_client = AsyncMock(), AsyncMock()
        OpenAIModelClient._client_cache[OpenAIModelClient.connection_key(cfg_keep)] = keep_client
        OpenAIModelClient._client_cache[OpenAIModelClient.connection_key(cfg_drop)] = drop_client

        await OpenAIModelClient.aclose_connections([cfg_drop])

        # Only the targeted connection is closed; the untouched one stays.
        assert drop_client.close.call_count == 1
        assert keep_client.close.call_count == 0
        assert OpenAIModelClient.connection_key(cfg_keep) in OpenAIModelClient._client_cache
        assert OpenAIModelClient.connection_key(cfg_drop) not in OpenAIModelClient._client_cache


class TestFallbackClient:
    @pytest.mark.asyncio
    async def test_fallback_builds_and_closes_client_per_request(self):
        model = _make_model(use_shared=False)
        client = model._client

        built = []

        def fake_build(timeout=None):
            mc = _mock_async_client()
            built.append(mc)
            return mc

        with patch.object(client, "_build_async_openai_client", side_effect=fake_build):
            await model.invoke(messages=[UserMessage(content="hi")])
            await model.invoke(messages=[UserMessage(content="hi again")])

        # A fresh client per request, each closed after use.
        assert len(built) == 2
        assert all(mc.close.call_count == 1 for mc in built)
        # Shared cache stays empty in fallback mode.
        assert OpenAIModelClient._client_cache == {}
