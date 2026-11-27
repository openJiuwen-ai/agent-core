# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Responses mode diagnostics: error text (status, provider detail) and the success log."""

from __future__ import annotations

import json
from unittest.mock import patch

import httpx
import pytest

from openjiuwen.core.common.exception.errors import BaseError
from openjiuwen.core.common.logging import LogEventType
from openjiuwen.core.foundation.llm import Model, ModelClientConfig, ModelRequestConfig
from openjiuwen.core.foundation.llm.model_clients.openai_account_model_client import (
    DEFAULT_OPENAI_ACCOUNT_BASE_URL,
    OpenAIAccountModelClient,
)
from openjiuwen.core.foundation.llm.model_clients.openai_model_client import OpenAIModelClient
from openjiuwen.core.foundation.llm.schema.config import ProviderType
from openjiuwen.core.foundation.llm.utils.responses_transport import OpenAIAccountResponsesTransport
from openjiuwen.core.foundation.llm.utils.responses_utils import (
    OpenAIAccountResponsesError,
    ResponsesStreamParser,
    raise_for_http_error,
)

_URL = "https://openrouter.example/api/v1/responses"
_OPENAI_UNKNOWN_FIELD = {
    "error": {
        "message": "Unknown parameter: 'context_window'.",
        "type": "invalid_request_error",
        "param": "context_window",
        "code": "unknown_parameter",
    }
}
# OpenRouter wraps the upstream refusal: its own message is vague, the cause is in error.metadata.raw.
_OPENROUTER_WRAPPED = {
    "error": {
        "code": 400,
        "message": "Provider returned error",
        "metadata": {"raw": json.dumps(_OPENAI_UNKNOWN_FIELD, indent=2), "provider_name": "OpenAI"},
    }
}
_OPENROUTER_429 = {
    "error": {
        "code": 429,
        "message": "Provider returned error",
        "metadata": {"provider_name": "Upstream", "raw": "upstream rate limit, retry shortly"},
    }
}
# OpenRouter's own request validation puts metadata next to error, not inside it.
_OPENROUTER_VALIDATION = {
    "error": {"code": "invalid_prompt", "message": "Invalid Responses API request"},
    "metadata": {"raw": '[{"code": "too_big", "path": ["stop"], "message": "Too big"}]'},
}
_OK_STREAM = (
    "event: response.output_text.delta\n"
    'data: {"delta":"Kyoto"}\n\n'
    "event: response.completed\n"
    'data: {"response":{"id":"resp-1","status":"completed",'
    '"usage":{"input_tokens":4,"output_tokens":1,"total_tokens":5}}}\n\n'
)
_FAILED_STREAM = (
    "event: response.output_text.delta\n"
    'data: {"delta":"Ky"}\n\n'
    "event: response.failed\n"
    'data: {"response":{"status":"failed","error":{"code":"server_error","message":"The model crashed"}}}\n\n'
)


def _http_response(status: int, **kwargs) -> httpx.Response:
    return httpx.Response(status, request=httpx.Request("POST", _URL), **kwargs)


def _sse(content: str) -> httpx.Response:
    return httpx.Response(200, content=content, headers={"content-type": "text/event-stream"})


def _model() -> Model:
    return Model(
        ModelClientConfig(
            client_provider="OpenAI",
            api_key="mock-api-key",
            api_base="https://openrouter.example/api/v1",
            api_mode="responses",
        ),
        ModelRequestConfig(model="mock-model", temperature=0.3, top_p=0.9, max_tokens=64),
    )


def _use_mock_transport(monkeypatch, handler) -> list[dict]:
    bodies: list[dict] = []

    async def recording_handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content.decode()))
        return await handler(request)

    monkeypatch.setattr(
        OpenAIModelClient,
        "_make_responses_transport",
        lambda self, *, timeout: OpenAIAccountResponsesTransport(transport=httpx.MockTransport(recording_handler)),
    )
    return bodies


class _FakeAuthManager:
    def resolve_access_token(self, *, force_refresh: bool = False) -> str:
        return "access-token"


def _account_client(handler) -> OpenAIAccountModelClient:
    return OpenAIAccountModelClient(
        ModelRequestConfig(model="gpt-5.4-mini", temperature=0.2, top_p=0.1),
        ModelClientConfig(
            client_provider=ProviderType.OpenAIAccount,
            api_key="",
            api_base=DEFAULT_OPENAI_ACCOUNT_BASE_URL,
            verify_ssl=False,
            openai_account_auth_manager=_FakeAuthManager(),
            openai_account_transport=OpenAIAccountResponsesTransport(transport=httpx.MockTransport(handler)),
        ),
    )


def _info_calls(logger, message: str) -> list[dict]:
    return [call.kwargs for call in logger.info.call_args_list if call.args and call.args[0] == message]


# HTTP errors carry the status and the provider's detail.
@pytest.mark.parametrize(
    "status, payload, expected",
    [
        pytest.param(400, _OPENAI_UNKNOWN_FIELD, "Error code: 400 - Unknown parameter: 'context_window'.", id="plain"),
        pytest.param(
            429,
            _OPENROUTER_429,
            "Error code: 429 - Provider returned error "
            '(metadata: {"provider_name":"Upstream","raw":"upstream rate limit, retry shortly"})',
            id="openrouter-metadata",
        ),
        pytest.param(
            400,
            _OPENROUTER_WRAPPED,
            # metadata.raw is an indented JSON string; it is decoded, not copied with escapes.
            "Error code: 400 - Provider returned error (metadata: "
            '{"raw":{"error":{"message":"Unknown parameter: \'context_window\'.","type":"invalid_request_error",'
            '"param":"context_window","code":"unknown_parameter"}},"provider_name":"OpenAI"})',
            id="decoded-raw",
        ),
        pytest.param(
            400,
            _OPENROUTER_VALIDATION,
            "Error code: 400 - Invalid Responses API request "
            '(metadata: {"raw":[{"code":"too_big","path":["stop"],"message":"Too big"}]})',
            id="top-level-metadata",
        ),
        pytest.param(
            400,
            {"error": {"message": "Expected max_tokens to be at least 1", "metadata": {"provider_name": None}}},
            "Error code: 400 - Expected max_tokens to be at least 1",
            id="empty-metadata",
        ),
        pytest.param(503, None, "Error code: 503 - Responses request failed without an error body.", id="no-body"),
    ],
)
def test_http_error_text(status, payload, expected):
    response = _http_response(status, json=payload) if payload is not None else _http_response(status)

    with pytest.raises(OpenAIAccountResponsesError) as caught:
        raise_for_http_error(response)

    assert str(caught.value) == expected
    assert caught.value.status_code == status
    assert caught.value.body == payload


def test_http_error_metadata_is_bounded():
    payload = {"error": {"message": "bad", "metadata": {"raw": "x" * 5000}}}

    with pytest.raises(OpenAIAccountResponsesError) as caught:
        raise_for_http_error(_http_response(400, json=payload))

    assert len(str(caught.value)) < 600
    assert "[truncated " in str(caught.value)


@pytest.mark.asyncio
async def test_model_invoke_error_shows_status_and_cause(monkeypatch):
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json=_OPENROUTER_429)

    _use_mock_transport(monkeypatch, handler)

    with pytest.raises(BaseError) as caught:
        await _model().invoke("Name a city.")

    assert "Error code: 429 - Provider returned error" in str(caught.value)
    assert "upstream rate limit, retry shortly" in str(caught.value)
    assert caught.value.__cause__.status_code == 429


@pytest.mark.asyncio
async def test_account_invoke_error_shows_status():
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json=_OPENAI_UNKNOWN_FIELD)

    with pytest.raises(BaseError) as caught:
        await _account_client(handler).invoke("hello")

    assert "Error code: 400 - Unknown parameter: 'context_window'." in str(caught.value)


# Stream failure events keep the error code and the provider's detail.
_SLOW_DOWN = "Error code: rate_limit_exceeded - Slow down"


@pytest.mark.parametrize(
    "event, expected",
    [
        pytest.param(
            {"type": "response.failed", "response": {"error": {"code": "rate_limit_exceeded", "message": "Slow down"}}},
            _SLOW_DOWN,
            id="response.failed",
        ),
        pytest.param(
            {"type": "error", "code": "rate_limit_exceeded", "message": "Slow down"}, _SLOW_DOWN, id="error-top-level"
        ),
        pytest.param(
            {"type": "error", "error": {"code": "rate_limit_exceeded", "message": "Slow down"}},
            _SLOW_DOWN,
            id="error-object",
        ),
        pytest.param(
            {"type": "response.failed", "response": {"error": {"message": "Slow down"}}}, "Slow down", id="no-code"
        ),
        pytest.param(
            {"type": "error", "error": {**_OPENROUTER_429["error"], "code": 502}},
            "Error code: 502 - Provider returned error "
            '(metadata: {"provider_name":"Upstream","raw":"upstream rate limit, retry shortly"})',
            id="openrouter-metadata",
        ),
    ],
)
def test_stream_failure_text(event, expected):
    with pytest.raises(OpenAIAccountResponsesError) as caught:
        ResponsesStreamParser(model_name="mock-model").parse(event)

    assert str(caught.value) == expected


@pytest.mark.asyncio
async def test_model_stream_failed_event_shows_code(monkeypatch):
    async def handler(_request: httpx.Request) -> httpx.Response:
        return _sse(_FAILED_STREAM)

    _use_mock_transport(monkeypatch, handler)

    with pytest.raises(BaseError) as caught:
        async for _ in _model().stream("Name a city."):
            pass

    assert "Error code: server_error - The model crashed" in str(caught.value)


# A successful non-streamed Responses call is logged like Chat mode's invoke.
@pytest.mark.asyncio
async def test_responses_invoke_logs_success(monkeypatch):
    async def handler(_request: httpx.Request) -> httpx.Response:
        return _sse(_OK_STREAM)

    bodies = _use_mock_transport(monkeypatch, handler)

    with patch("openjiuwen.core.foundation.llm.model_clients.openai_model_client.llm_logger") as logger:
        response = await _model().invoke("Name a city.")

    assert response.content == "Kyoto"
    logged = _info_calls(logger, "Responses API response received.")
    assert len(logged) == 1
    body = bodies[0]
    assert logged[0]["event_type"] == LogEventType.LLM_CALL_END
    assert logged[0]["model_name"] == "mock-model"
    assert logged[0]["model_provider"] == "OpenAI"
    assert logged[0]["messages"] == "Name a city."
    assert logged[0]["tools"] is None
    assert logged[0]["temperature"] == body.get("temperature")
    assert logged[0]["top_p"] == body.get("top_p")
    assert logged[0]["max_tokens"] == body.get("max_output_tokens")
    assert logged[0]["is_stream"] is False
    assert "Kyoto" in logged[0]["metadata"]["response"]


@pytest.mark.asyncio
async def test_responses_invoke_error_does_not_log_success(monkeypatch):
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json=_OPENROUTER_429)

    _use_mock_transport(monkeypatch, handler)

    with patch("openjiuwen.core.foundation.llm.model_clients.openai_model_client.llm_logger") as logger:
        with pytest.raises(BaseError):
            await _model().invoke("Name a city.")

    assert _info_calls(logger, "Responses API response received.") == []
    logger.error.assert_called_once()


@pytest.mark.asyncio
async def test_account_invoke_logs_success():
    bodies: list[dict] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content.decode()))
        return _sse(_OK_STREAM)

    client = _account_client(handler)
    with patch("openjiuwen.core.foundation.llm.model_clients.openai_account_model_client.llm_logger") as logger:
        response = await client.invoke("hello", temperature=0.3, top_p=0.9, send_sampling_params=True)

    assert response.content == "Kyoto"
    logged = _info_calls(logger, "OpenAI account API response received.")
    assert len(logged) == 1
    assert logged[0]["event_type"] == LogEventType.LLM_CALL_END
    assert logged[0]["model_name"] == "gpt-5.4-mini"
    assert logged[0]["model_provider"] == ProviderType.OpenAIAccount.value
    assert logged[0]["messages"] == "hello"
    assert logged[0]["tools"] is None
    assert logged[0]["temperature"] == bodies[0]["temperature"] == 0.3
    assert logged[0]["top_p"] == bodies[0]["top_p"] == 0.9
    assert logged[0]["max_tokens"] is None
    assert logged[0]["is_stream"] is False
    assert "Kyoto" in logged[0]["metadata"]["response"]
