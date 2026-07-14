# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import json
import time
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest

from openjiuwen.core.foundation.llm.model_clients import create_model_client
from openjiuwen.core.foundation.llm.model_clients.openai_account_model_client import (
    DEFAULT_OPENAI_ACCOUNT_BASE_URL,
    OpenAIAccountModelClient,
)
from openjiuwen.core.foundation.llm.utils.responses_transport import OpenAIAccountResponsesTransport
from openjiuwen.core.foundation.llm.schema.config import ModelClientConfig, ModelRequestConfig, ProviderType


class _FakeOpenAIAccountAuthManager:
    def __init__(self, access_token: str = "access-token", refreshed_token: str = "refreshed-token"):
        self.access_token = access_token
        self.refreshed_token = refreshed_token
        self.calls: list[bool] = []

    def resolve_access_token(self, *, force_refresh: bool = False) -> str:
        self.calls.append(force_refresh)
        return self.refreshed_token if force_refresh else self.access_token


class _UpperParser:
    async def parse(self, content: str) -> str:
        return content.upper()


class _FakeOpenAIAccountModelCatalog:
    def __init__(self):
        self.calls = []

    def list_model_ids(self, *, auth_manager=None, access_token=None, force_refresh: bool = False) -> list[str]:
        self.calls.append(
            {
                "auth_manager": auth_manager,
                "access_token": access_token,
                "force_refresh": force_refresh,
            }
        )
        return ["gpt-5.4"]


def _client_config(**kwargs):
    return ModelClientConfig(
        client_provider=ProviderType.OpenAIAccount,
        api_key="",
        api_base=DEFAULT_OPENAI_ACCOUNT_BASE_URL,
        verify_ssl=False,
        **kwargs,
    )


def _request_config():
    return ModelRequestConfig(model="gpt-5.4-mini", temperature=0.2, top_p=0.1)


def test_model_client_config_accepts_openai_account_provider_value():
    cfg = ModelClientConfig(
        client_provider="OpenAIAccount",
        api_key="",
        api_base=DEFAULT_OPENAI_ACCOUNT_BASE_URL,
    )

    assert cfg.client_provider == ProviderType.OpenAIAccount.value


def test_model_client_config_normalizes_openai_account_provider_case():
    cfg = ModelClientConfig(
        client_provider="OPENAIACCOUNT",
        api_key="",
        api_base=DEFAULT_OPENAI_ACCOUNT_BASE_URL,
    )

    assert cfg.client_provider == ProviderType.OpenAIAccount.value


def test_model_client_config_normalizes_openai_account_member_name():
    cfg = ModelClientConfig(
        client_provider="OpenAIAccount",
        api_key="",
        api_base=DEFAULT_OPENAI_ACCOUNT_BASE_URL,
    )

    assert cfg.client_provider == ProviderType.OpenAIAccount.value


def test_factory_routes_openai_account_to_dedicated_client():
    client_config = ModelClientConfig(
        client_provider=ProviderType.OpenAIAccount,
        api_key="",
        api_base=DEFAULT_OPENAI_ACCOUNT_BASE_URL,
    )
    request_config = ModelRequestConfig(model="gpt-5.4-mini")

    client = create_model_client(client_config, request_config)

    assert isinstance(client, OpenAIAccountModelClient)
    assert client.model_client_config.api_key == ""


def test_openai_account_client_lists_available_models_through_catalog():
    auth = _FakeOpenAIAccountAuthManager()
    catalog = _FakeOpenAIAccountModelCatalog()
    client = OpenAIAccountModelClient(
        _request_config(),
        _client_config(openai_account_auth_manager=auth, openai_account_model_catalog=catalog),
    )

    assert client.list_available_models(force_refresh=True) == ["gpt-5.4"]
    assert catalog.calls == [
        {
            "auth_manager": auth,
            "access_token": None,
            "force_refresh": True,
        }
    ]


def test_openai_account_default_transport_created_once_for_concurrent_calls(monkeypatch):
    client = OpenAIAccountModelClient(
        _request_config(),
        _client_config(openai_account_auth_manager=_FakeOpenAIAccountAuthManager()),
    )
    created_timeouts = []

    def fake_make_transport(*, timeout: float):
        time.sleep(0.02)
        created_timeouts.append(timeout)
        return object()

    monkeypatch.setattr(client, "_make_transport", fake_make_transport)

    with ThreadPoolExecutor(max_workers=8) as executor:
        transports = list(executor.map(lambda _: client._get_transport(timeout=None), range(8)))

    assert len({id(transport) for transport in transports}) == 1
    assert created_timeouts == [client.model_client_config.timeout]


def test_openai_account_get_transport_preserves_zero_timeout(monkeypatch):
    client = OpenAIAccountModelClient(
        _request_config(),
        _client_config(openai_account_auth_manager=_FakeOpenAIAccountAuthManager()),
    )
    created_timeouts = []

    def fake_make_transport(*, timeout: float):
        created_timeouts.append(timeout)
        return object()

    monkeypatch.setattr(client, "_make_transport", fake_make_transport)

    transport = client._get_transport(timeout=0)

    assert transport is not None
    assert created_timeouts == [0]


@pytest.mark.asyncio
async def test_openai_account_invoke_uses_auth_and_responses_transport():
    seen_request = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        seen_request["headers"] = request.headers
        seen_request["body"] = json.loads(request.content.decode())
        return httpx.Response(
            200,
            content=(
                "event: response.output_text.delta\n"
                'data: {"delta":"ok"}\n\n'
                "event: response.completed\n"
                'data: {"response":{"usage":{"input_tokens":2,"output_tokens":1,"total_tokens":3}}}\n\n'
            ),
            headers={"content-type": "text/event-stream"},
        )

    auth = _FakeOpenAIAccountAuthManager()
    client = OpenAIAccountModelClient(
        _request_config(),
        _client_config(
            custom_headers={"Authorization": "bad", "X-Config": "config-value"},
            openai_account_auth_manager=auth,
            openai_account_transport=OpenAIAccountResponsesTransport(transport=httpx.MockTransport(handler)),
        ),
    )

    response = await client.invoke(
        "hello",
        session_id="session-1",
        custom_headers={"Authorization": "bad-request", "X-Request": "request-value"},
        output_parser=_UpperParser(),
    )

    assert response.content == "ok"
    assert response.parser_content == "OK"
    assert response.usage_metadata.total_tokens == 3
    assert seen_request["headers"]["Authorization"] == "Bearer access-token"
    assert seen_request["headers"]["session_id"] == "session-1"
    assert seen_request["headers"]["X-Config"] == "config-value"
    assert seen_request["headers"]["X-Request"] == "request-value"
    assert seen_request["body"]["model"] == "gpt-5.4-mini"
    assert seen_request["body"]["stream"] is True
    assert "temperature" not in seen_request["body"]
    assert "top_p" not in seen_request["body"]
    assert "max_output_tokens" not in seen_request["body"]
    assert auth.calls == [False]


@pytest.mark.asyncio
async def test_openai_account_invoke_can_opt_in_to_sampling_params():
    seen_body = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        seen_body.update(json.loads(request.content.decode()))
        return httpx.Response(
            200,
            content=(
                "event: response.output_text.delta\n"
                'data: {"delta":"ok"}\n\n'
                "event: response.completed\n"
                'data: {"response":{"usage":{"input_tokens":2,"output_tokens":1,"total_tokens":3}}}\n\n'
            ),
            headers={"content-type": "text/event-stream"},
        )

    client = OpenAIAccountModelClient(
        _request_config(),
        _client_config(
            openai_account_auth_manager=_FakeOpenAIAccountAuthManager(),
            openai_account_transport=OpenAIAccountResponsesTransport(transport=httpx.MockTransport(handler)),
        ),
    )

    response = await client.invoke("hello", temperature=0.3, top_p=0.9, send_sampling_params=True)

    assert response.content == "ok"
    assert seen_body["temperature"] == 0.3
    assert seen_body["top_p"] == 0.9


@pytest.mark.asyncio
async def test_openai_account_invoke_refreshes_once_on_401():
    seen_auth_headers = []

    async def handler(request: httpx.Request) -> httpx.Response:
        seen_auth_headers.append(request.headers["Authorization"])
        if len(seen_auth_headers) == 1:
            return httpx.Response(401, json={"error": {"message": "expired"}})
        return httpx.Response(
            200,
            content=(
                "event: response.output_text.delta\n"
                'data: {"delta":"ok"}\n\n'
                "event: response.completed\n"
                'data: {"response":{"usage":{"input_tokens":2,"output_tokens":1,"total_tokens":3}}}\n\n'
            ),
            headers={"content-type": "text/event-stream"},
        )

    auth = _FakeOpenAIAccountAuthManager()
    client = OpenAIAccountModelClient(
        _request_config(),
        _client_config(
            openai_account_auth_manager=auth,
            openai_account_transport=OpenAIAccountResponsesTransport(transport=httpx.MockTransport(handler)),
        ),
    )

    response = await client.invoke("hello")

    assert response.content == "ok"
    assert seen_auth_headers == ["Bearer access-token", "Bearer refreshed-token"]
    assert auth.calls == [False, True]


@pytest.mark.asyncio
async def test_openai_account_invoke_requires_relogin_after_refreshed_401():
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": {"message": "still expired"}})

    auth = _FakeOpenAIAccountAuthManager()
    client = OpenAIAccountModelClient(
        _request_config(),
        _client_config(
            openai_account_auth_manager=auth,
            openai_account_transport=OpenAIAccountResponsesTransport(transport=httpx.MockTransport(handler)),
        ),
    )

    with pytest.raises(Exception) as error:
        await client.invoke("hello")

    assert "Please login again" in str(error.value)
    assert "still expired" in str(error.value)
    assert auth.calls == [False, True]


@pytest.mark.asyncio
async def test_openai_account_stream_yields_chunks_and_usage():
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer access-token"
        return httpx.Response(
            200,
            content=(
                "event: response.output_text.delta\n"
                'data: {"delta":"Hel"}\n\n'
                "event: response.output_text.delta\n"
                'data: {"delta":"lo"}\n\n'
                "event: response.completed\n"
                'data: {"response":{"usage":{"input_tokens":2,"output_tokens":3,"total_tokens":5}}}\n\n'
            ),
            headers={"content-type": "text/event-stream"},
        )

    client = OpenAIAccountModelClient(
        _request_config(),
        _client_config(
            openai_account_auth_manager=_FakeOpenAIAccountAuthManager(),
            openai_account_transport=OpenAIAccountResponsesTransport(transport=httpx.MockTransport(handler)),
        ),
    )

    chunks = [chunk async for chunk in client.stream("hello", output_parser=_UpperParser())]

    assert [chunk.content for chunk in chunks] == ["Hel", "lo", ""]
    assert chunks[0].parser_content == "HEL"
    assert chunks[1].parser_content == "LO"
    assert chunks[-1].usage_metadata.total_tokens == 5


@pytest.mark.asyncio
async def test_openai_account_stream_requires_relogin_after_refreshed_403():
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"error": {"message": "session revoked"}})

    auth = _FakeOpenAIAccountAuthManager()
    client = OpenAIAccountModelClient(
        _request_config(),
        _client_config(
            openai_account_auth_manager=auth,
            openai_account_transport=OpenAIAccountResponsesTransport(transport=httpx.MockTransport(handler)),
        ),
    )

    with pytest.raises(Exception) as error:
        _ = [chunk async for chunk in client.stream("hello")]

    assert "Please login again" in str(error.value)
    assert "session revoked" in str(error.value)
    assert auth.calls == [False, True]


@pytest.mark.asyncio
async def test_openai_account_invoke_wraps_429_as_model_call_failed():
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={"error": {"message": "rate limited"}})

    client = OpenAIAccountModelClient(
        _request_config(),
        _client_config(
            openai_account_auth_manager=_FakeOpenAIAccountAuthManager(),
            openai_account_transport=OpenAIAccountResponsesTransport(transport=httpx.MockTransport(handler)),
        ),
    )

    with pytest.raises(Exception) as error:
        await client.invoke("hello")

    assert "rate limited" in str(error.value)
