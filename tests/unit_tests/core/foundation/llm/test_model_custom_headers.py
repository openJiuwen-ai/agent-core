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
from openjiuwen.core.common.utils.hash_util import generate_key
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


class TestModelCustomHeadersByEntry:
    async def _invoke_and_get_sent_headers(self, model: Model, request_headers=None) -> dict:
        mock_async_client = AsyncMock()
        mock_async_client.chat.completions.create = AsyncMock(return_value=_build_mock_response())

        invoke_kwargs = {}
        if request_headers is not None:
            invoke_kwargs["custom_headers"] = request_headers

        with patch.object(model._client, "_create_async_openai_client", return_value=mock_async_client):
            await model.invoke(messages=[UserMessage(content="hello")], **invoke_kwargs)

        sent_params = mock_async_client.chat.completions.create.call_args.kwargs
        sent_headers = sent_params.get("extra_headers", {})
        print(f"[debug] full outgoing headers: {sent_headers}")
        return sent_headers

    async def _stream_and_get_sent_headers(self, model: Model, request_headers=None) -> dict:
        chunk = MagicMock()
        chunk.choices = [MagicMock()]
        chunk.choices[0].delta = MagicMock()
        chunk.choices[0].delta.content = "hello"
        chunk.choices[0].delta.reasoning_content = None
        chunk.choices[0].delta.tool_calls = None
        chunk.choices[0].finish_reason = "stop"
        chunk.usage = None

        async def chunk_generator():
            yield chunk

        mock_async_client = AsyncMock()
        mock_async_client.chat.completions.create = AsyncMock(return_value=chunk_generator())

        stream_kwargs = {}
        if request_headers is not None:
            stream_kwargs["custom_headers"] = request_headers

        with patch.object(model._client, "_create_async_openai_client", return_value=mock_async_client):
            async for _ in model.stream(messages=[UserMessage(content="hello")], **stream_kwargs):
                pass

        sent_params = mock_async_client.chat.completions.create.call_args.kwargs
        sent_headers = sent_params.get("extra_headers", {})
        print(f"[debug] full outgoing headers: {sent_headers}")
        return sent_headers

    @pytest.mark.asyncio
    async def test_direct_model_constructor_sanitizes_custom_headers(self):
        model = Model(
            model_client_config=ModelClientConfig(
                client_provider=ProviderType.OpenAI,
                api_key="sk-test",
                api_base="https://api.openai.com/v1",
                verify_ssl=False,
                custom_headers={
                    "Token": "token-custom",
                    "UserID": "user-001",
                    "Content-Length": "blocked",
                    "X-None": None,
                    "": "empty-key",
                },
            ),
            model_config=ModelRequestConfig(model="gpt-4o-mini"),
        )

        sent_headers = await self._invoke_and_get_sent_headers(model)

        assert sent_headers == {
            "Token": "token-custom",
            "UserID": "user-001",
        }

    @pytest.mark.asyncio
    async def test_init_model_merges_request_custom_headers(self):
        model = init_model(
            provider=ProviderType.OpenAI.value,
            model_name="gpt-4o-mini",
            api_key="sk-test",
            api_base="https://api.openai.com/v1",
            verify_ssl=False,
            custom_headers={
                "Token": "token-init",
                "UserID": "user-init",
                "Host": "blocked",
            },
        )

        sent_headers = await self._invoke_and_get_sent_headers(
            model,
            request_headers={
                "token": "token-req",
                "UserID": "user-req",
                "Transfer-Encoding": "blocked",
                "Authorization": "Bearer blocked",
                "X-Empty": "",
            },
        )

        assert sent_headers == {
            "Token": "token-req",
            "UserID": "user-req",
        }

    @pytest.mark.asyncio
    async def test_headers_merge_is_case_insensitive_and_blocks_authorization(self):
        model = Model(
            model_client_config=ModelClientConfig(
                client_provider=ProviderType.OpenAI,
                api_key="sk-test",
                api_base="https://api.openai.com/v1",
                verify_ssl=False,
                custom_headers={
                    "X-Tenant": "tenant-config",
                    "Authorization": "Bearer blocked-config",
                },
            ),
            model_config=ModelRequestConfig(model="gpt-4o-mini"),
        )

        sent_headers = await self._invoke_and_get_sent_headers(
            model,
            request_headers={
                "x-tenant": "tenant-request",
                "userid": "user-request",
                "authorization": "Bearer blocked-request",
            },
        )

        assert sent_headers == {
            "X-Tenant": "tenant-request",
            "userid": "user-request",
        }

    @pytest.mark.asyncio
    async def test_react_agent_configure_model_client_propagates_custom_headers(self):
        config = ReActAgentConfig()
        config.configure_custom_headers(
            {
                "Token": "token-react",
                "UserID": "user-react",
                "Connection": "blocked",
            },
        )
        config.configure_model_client(
            provider=ProviderType.OpenAI.value,
            api_key="sk-test",
            api_base="https://api.openai.com/v1",
            model_name="gpt-4o-mini",
            verify_ssl=False,
        )
        model = Model(
            model_client_config=config.model_client_config,
            model_config=config.model_config_obj,
        )

        sent_headers = await self._invoke_and_get_sent_headers(
            model,
            request_headers={
                "UserID": "user-override",
                "X-Empty": None,
            },
        )

        assert sent_headers == {
            "Token": "token-react",
            "UserID": "user-override",
        }

    @pytest.mark.asyncio
    async def test_stream_injects_effective_headers(self):
        model = Model(
            model_client_config=ModelClientConfig(
                client_provider=ProviderType.OpenAI,
                api_key="sk-test",
                api_base="https://api.openai.com/v1",
                verify_ssl=False,
                custom_headers={"UserID": "user-cfg", "Host": "blocked"},
            ),
            model_config=ModelRequestConfig(model="gpt-4o-mini"),
        )

        sent_headers = await self._stream_and_get_sent_headers(
            model,
            request_headers={"UserID": "user-req", "Connection": "blocked"},
        )

        assert sent_headers == {"UserID": "user-req"}


def test_model_fingerprint_stays_stable_with_different_custom_headers():
    cfg1 = ModelClientConfig(
        client_provider=ProviderType.OpenAI,
        api_key="sk-test",
        api_base="https://api.openai.com/v1",
        custom_headers={"Token": "token-a", "UserID": "user-a"},
    )
    cfg2 = ModelClientConfig(
        client_provider=ProviderType.OpenAI,
        api_key="sk-test",
        api_base="https://api.openai.com/v1",
        custom_headers={"Token": "token-b", "UserID": "user-b"},
    )

    key1 = generate_key(cfg1.api_key, cfg1.api_base, cfg1.client_provider.value)
    key2 = generate_key(cfg2.api_key, cfg2.api_base, cfg2.client_provider.value)

    assert key1 == key2
