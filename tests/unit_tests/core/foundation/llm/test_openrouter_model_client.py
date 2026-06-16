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
)


def _build_mock_response(content: str = "ok") -> MagicMock:
    response = MagicMock()
    response.choices = [MagicMock()]
    response.choices[0].message = MagicMock()
    response.choices[0].message.content = content
    response.choices[0].message.tool_calls = None
    response.choices[0].message.reasoning = None
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
    chunk.choices[0].delta.reasoning = None
    chunk.choices[0].delta.reasoning_content = None
    chunk.choices[0].delta.tool_calls = None
    chunk.choices[0].finish_reason = "stop"
    chunk.usage = None
    return chunk


class TestOpenRouterModelClient:

    def _make_configs(self, custom_headers=None):
        client_config = ModelClientConfig(
            client_provider="OpenRouter",
            api_key="sk-or-test-key",
            api_base="https://openrouter.ai/api/v1",
            timeout=60.0,
            verify_ssl=False,
            custom_headers=custom_headers,
        )
        request_config = ModelRequestConfig(model="anthropic/claude-sonnet-4")
        return client_config, request_config

    def test_no_default_attribution_headers(self):
        from openjiuwen.core.foundation.llm.model_clients.openrouter_model_client import (
            OpenRouterModelClient,
        )
        client_config, request_config = self._make_configs()
        client = OpenRouterModelClient(request_config, client_config)

        assert "HTTP-Referer" not in client._base_headers
        assert "X-OpenRouter-Title" not in client._base_headers
        assert "X-OpenRouter-Categories" not in client._base_headers

    def test_configurable_headers_from_custom_headers(self):
        from openjiuwen.core.foundation.llm.model_clients.openrouter_model_client import (
            OpenRouterModelClient,
        )
        client_config, request_config = self._make_configs(
            custom_headers={
                "HTTP-Referer": "https://openjiuwen.com/",
                "X-OpenRouter-Title": "JiuwenSwarm",
                "X-OpenRouter-Categories": "cli-agent,cloud-agent",
            }
        )
        client = OpenRouterModelClient(request_config, client_config)

        assert client._base_headers["HTTP-Referer"] == "https://openjiuwen.com/"
        assert client._base_headers["X-OpenRouter-Title"] == "JiuwenSwarm"
        assert client._base_headers["X-OpenRouter-Categories"] == "cli-agent,cloud-agent"

    def test_attribution_headers_protected_from_request_override(self):
        from openjiuwen.core.foundation.llm.model_clients.openrouter_model_client import (
            OpenRouterModelClient,
        )
        client_config, request_config = self._make_configs(
            custom_headers={
                "HTTP-Referer": "https://openjiuwen.com/",
                "X-OpenRouter-Title": "JiuwenSwarm",
            }
        )
        client = OpenRouterModelClient(request_config, client_config)

        effective = client._build_request_headers(
            client._base_headers,
            {
                "HTTP-Referer": "https://evil.com",
                "X-OpenRouter-Title": "EvilApp",
            },
        )
        assert effective["HTTP-Referer"] == "https://openjiuwen.com/"
        assert effective["X-OpenRouter-Title"] == "JiuwenSwarm"

    def test_custom_non_attribution_headers_preserved(self):
        from openjiuwen.core.foundation.llm.model_clients.openrouter_model_client import (
            OpenRouterModelClient,
        )
        client_config, request_config = self._make_configs(
            custom_headers={"X-Custom-Header": "my-value"}
        )
        client = OpenRouterModelClient(request_config, client_config)

        assert client._base_headers.get("X-Custom-Header") == "my-value"

    def test_non_attribution_headers_can_be_overridden(self):
        from openjiuwen.core.foundation.llm.model_clients.openrouter_model_client import (
            OpenRouterModelClient,
        )
        client_config, request_config = self._make_configs(
            custom_headers={"X-Custom-Header": "original"}
        )
        client = OpenRouterModelClient(request_config, client_config)

        effective = client._build_request_headers(
            client._base_headers,
            {"X-Custom-Header": "overridden"},
        )
        assert effective["X-Custom-Header"] == "overridden"

    def test_client_name_is_openrouter_only(self):
        from openjiuwen.core.foundation.llm.model_clients.openrouter_model_client import (
            OpenRouterModelClient,
        )
        assert "OpenRouter" in OpenRouterModelClient.__client_name__
        assert "OpenAI" not in OpenRouterModelClient.__client_name__

    def test_attribution_protection_is_case_insensitive(self):
        from openjiuwen.core.foundation.llm.model_clients.openrouter_model_client import (
            OpenRouterModelClient,
        )
        client_config, request_config = self._make_configs(
            custom_headers={
                "HTTP-Referer": "https://openjiuwen.com/",
                "X-OpenRouter-Title": "JiuwenSwarm",
            }
        )
        client = OpenRouterModelClient(request_config, client_config)

        effective = client._build_request_headers(
            client._base_headers,
            {
                "http-referer": "https://evil.com",
                "x-openrouter-title": "EvilApp",
            },
        )
        assert effective["HTTP-Referer"] == "https://openjiuwen.com/"
        assert effective["X-OpenRouter-Title"] == "JiuwenSwarm"


class TestOpenRouterFactoryRouting:

    def test_factory_routes_openrouter_to_dedicated_client(self):
        from openjiuwen.core.foundation.llm.model_clients import create_model_client
        from openjiuwen.core.foundation.llm.model_clients.openrouter_model_client import (
            OpenRouterModelClient,
        )
        client_config = ModelClientConfig(
            client_provider="OpenRouter",
            api_key="sk-or-test-key",
            api_base="https://openrouter.ai/api/v1",
            timeout=60.0,
            verify_ssl=False,
        )
        request_config = ModelRequestConfig(model="anthropic/claude-sonnet-4")
        client = create_model_client(client_config, request_config)
        assert isinstance(client, OpenRouterModelClient)

    def test_factory_routes_openai_to_openai_client(self):
        from openjiuwen.core.foundation.llm.model_clients import create_model_client
        from openjiuwen.core.foundation.llm.model_clients.openai_model_client import (
            OpenAIModelClient,
        )
        from openjiuwen.core.foundation.llm.model_clients.openrouter_model_client import (
            OpenRouterModelClient,
        )
        client_config = ModelClientConfig(
            client_provider="OpenAI",
            api_key="sk-test-key",
            api_base="https://api.openai.com/v1",
            timeout=60.0,
            verify_ssl=False,
        )
        request_config = ModelRequestConfig(model="gpt-4o")
        client = create_model_client(client_config, request_config)
        assert isinstance(client, OpenAIModelClient)
        assert not isinstance(client, OpenRouterModelClient)

    def test_openai_client_no_longer_registers_openrouter(self):
        from openjiuwen.core.foundation.llm.model_clients.openai_model_client import (
            OpenAIModelClient,
        )
        names = OpenAIModelClient.__client_name__
        if isinstance(names, list):
            assert "OpenRouter" not in names
        else:
            assert names != "OpenRouter"


class TestOpenRouterModelIntegration:

    async def _invoke_and_get_sent_headers(self, model: Model, request_headers=None) -> dict:
        mock_async_client = AsyncMock()
        mock_async_client.chat.completions.create = AsyncMock(return_value=_build_mock_response())

        invoke_kwargs = {}
        if request_headers is not None:
            invoke_kwargs["custom_headers"] = request_headers

        with patch.object(model._client, "_create_async_openai_client", return_value=mock_async_client):
            await model.invoke(messages=[UserMessage(content="hello")], **invoke_kwargs)

        sent_params = mock_async_client.chat.completions.create.call_args.kwargs
        return sent_params.get("extra_headers", {})

    @pytest.mark.asyncio
    async def test_openrouter_model_sends_attribution_headers(self):
        model = Model(
            model_client_config=ModelClientConfig(
                client_provider=ProviderType.OpenRouter,
                api_key="sk-or-test",
                api_base="https://openrouter.ai/api/v1",
                verify_ssl=False,
                custom_headers={
                    "HTTP-Referer": "https://openjiuwen.com/",
                    "X-OpenRouter-Title": "JiuwenSwarm",
                    "X-OpenRouter-Categories": "cli-agent,cloud-agent",
                },
            ),
            model_config=ModelRequestConfig(model="anthropic/claude-sonnet-4"),
        )

        sent_headers = await self._invoke_and_get_sent_headers(model)

        assert sent_headers["HTTP-Referer"] == "https://openjiuwen.com/"
        assert sent_headers["X-OpenRouter-Title"] == "JiuwenSwarm"
        assert sent_headers["X-OpenRouter-Categories"] == "cli-agent,cloud-agent"

    @pytest.mark.asyncio
    async def test_openrouter_model_protects_attribution_from_request_headers(self):
        model = Model(
            model_client_config=ModelClientConfig(
                client_provider=ProviderType.OpenRouter,
                api_key="sk-or-test",
                api_base="https://openrouter.ai/api/v1",
                verify_ssl=False,
                custom_headers={
                    "HTTP-Referer": "https://openjiuwen.com/",
                    "X-OpenRouter-Title": "JiuwenSwarm",
                },
            ),
            model_config=ModelRequestConfig(model="anthropic/claude-sonnet-4"),
        )

        sent_headers = await self._invoke_and_get_sent_headers(
            model,
            request_headers={
                "HTTP-Referer": "https://evil.com",
                "X-OpenRouter-Title": "EvilApp",
            },
        )

        assert sent_headers["HTTP-Referer"] == "https://openjiuwen.com/"
        assert sent_headers["X-OpenRouter-Title"] == "JiuwenSwarm"

    @pytest.mark.asyncio
    async def test_openrouter_model_allows_non_attribution_request_headers(self):
        model = Model(
            model_client_config=ModelClientConfig(
                client_provider=ProviderType.OpenRouter,
                api_key="sk-or-test",
                api_base="https://openrouter.ai/api/v1",
                verify_ssl=False,
                custom_headers={
                    "HTTP-Referer": "https://openjiuwen.com/",
                },
            ),
            model_config=ModelRequestConfig(model="anthropic/claude-sonnet-4"),
        )

        sent_headers = await self._invoke_and_get_sent_headers(
            model,
            request_headers={
                "X-Custom-Request": "request-value",
            },
        )

        assert sent_headers["HTTP-Referer"] == "https://openjiuwen.com/"
        assert sent_headers["X-Custom-Request"] == "request-value"

    @pytest.mark.asyncio
    async def test_openrouter_stream_sends_attribution_headers(self):
        model = Model(
            model_client_config=ModelClientConfig(
                client_provider=ProviderType.OpenRouter,
                api_key="sk-or-test",
                api_base="https://openrouter.ai/api/v1",
                verify_ssl=False,
                custom_headers={
                    "HTTP-Referer": "https://openjiuwen.com/",
                    "X-OpenRouter-Title": "JiuwenSwarm",
                },
            ),
            model_config=ModelRequestConfig(model="anthropic/claude-sonnet-4"),
        )

        async def chunk_generator():
            yield _build_stream_chunk("hello")

        mock_async_client = AsyncMock()
        mock_async_client.chat.completions.create = AsyncMock(return_value=chunk_generator())

        with patch.object(model._client, "_create_async_openai_client", return_value=mock_async_client):
            async for _ in model.stream(messages=[UserMessage(content="hello")]):
                pass

        sent_params = mock_async_client.chat.completions.create.call_args.kwargs
        sent_headers = sent_params.get("extra_headers", {})

        assert sent_headers["HTTP-Referer"] == "https://openjiuwen.com/"
        assert sent_headers["X-OpenRouter-Title"] == "JiuwenSwarm"
