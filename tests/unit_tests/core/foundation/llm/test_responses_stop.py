# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

import json

import httpx
import pytest

from openjiuwen.core.common.logging.events import LogEventType
from openjiuwen.core.foundation.llm import Model, ModelClientConfig, ModelRequestConfig
from openjiuwen.core.foundation.llm.model_clients.openai_account_model_client import (
    DEFAULT_OPENAI_ACCOUNT_BASE_URL,
    OpenAIAccountModelClient,
)
from openjiuwen.core.foundation.llm.request_context import disabled_thinking_fallback_scope
from openjiuwen.core.foundation.llm.schema.config import ProviderType
from openjiuwen.core.foundation.llm.utils import request_parameter_fallback
from openjiuwen.core.foundation.llm.utils.request_parameter_fallback import RequestParameterFallback
from openjiuwen.core.foundation.llm.utils.responses_transport import OpenAIAccountResponsesTransport
from openjiuwen.core.foundation.llm.utils.responses_utils import OpenAIAccountResponsesError, build_request_body

_OK_STREAM = (
    "event: response.output_text.delta\n"
    'data: {"delta":"ok"}\n\n'
    "event: response.completed\n"
    'data: {"response":{"usage":{"input_tokens":2,"output_tokens":1,"total_tokens":3}}}\n\n'
)
_OPENAI_STOP_ERROR = {
    "error": {
        "message": "Unknown parameter: 'stop'. Did you mean 'store'?",
        "type": "invalid_request_error",
        "param": "stop",
        "code": "unknown_parameter",
    }
}
# The ChatGPT-login backend refuses unknown fields in this shape.
_DETAIL_STOP_ERROR = {"detail": "Unsupported parameter: stop"}
# OpenRouter wraps an upstream refusal: its own message is vague and the original is in metadata.raw.
_OPENROUTER_WRAPPED_ERROR = {
    "error": {
        "message": "Provider returned error",
        "code": 400,
        "metadata": {"raw": json.dumps(_OPENAI_STOP_ERROR, indent=2), "provider_name": "Azure"},
    }
}
# OpenAI-style value errors also set ``param``; they are about the value, not a refusal of the field.
_OPENAI_STOP_TYPE_ERROR = {
    "error": {
        "message": "Invalid type for 'stop': expected one of a string or array of strings, but got an integer instead.",
        "type": "invalid_request_error",
        "param": "stop",
        "code": "invalid_type",
    }
}
_OPENAI_STOP_LENGTH_ERROR = {
    "error": {
        "message": "Invalid 'stop': array too long. Expected an array with maximum length 4, but got 20 instead.",
        "type": "invalid_request_error",
        "param": "stop",
        "code": "array_above_max_length",
    }
}
# json.dumps escapes non-ASCII by default, so this body carries \uXXXX escapes, not Chinese text.
_ESCAPED_CHINESE_ERROR = json.dumps({"error": {"message": "当前模型不支持stop参数"}}).encode()
# OpenRouter's own check for too many stop items: it names stop but is not a refusal of the field.
_OPENROUTER_STOP_VALUE_ERROR = {
    "error": {"code": "invalid_prompt", "message": "Invalid Responses API request"},
    "metadata": {
        "raw": '[{"code": "too_big", "maximum": 4, "path": ["stop"], '
        '"message": "Too big: expected array to have <=4 items"}]',
    },
}
_TEMPERATURE_ERROR = {
    "error": {
        "message": "Unsupported parameter: 'temperature' is not supported with this model.",
        "type": "invalid_request_error",
        "param": "temperature",
        "code": "unsupported_parameter",
    }
}


@pytest.fixture
def warnings(monkeypatch):
    captured = []

    def fake_warning(message, *args, **kwargs):
        captured.append((message, args, kwargs))

    monkeypatch.setattr(
        "openjiuwen.core.foundation.llm.utils.request_parameter_fallback.llm_logger.warning",
        fake_warning,
    )
    return captured


def _stop_fallback() -> RequestParameterFallback:
    return RequestParameterFallback("OpenAI", "OpenAI Responses", retry_without_stop=True)


def _response(status: int, payload: object) -> httpx.Response:
    if isinstance(payload, bytes):
        return httpx.Response(status, content=payload, headers={"content-type": "application/json"})
    if isinstance(payload, str):
        return httpx.Response(status, text=payload)
    return httpx.Response(status, json=payload)


class _Endpoint:
    """Fake Responses endpoint that records each request body and may refuse ``stop``."""

    def __init__(self, refusal: tuple[int, object] | None = None):
        self.refusal = refusal
        self.bodies: list[dict] = []

    async def handler(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode())
        self.bodies.append(body)
        if self.refusal and "stop" in body:
            return _response(*self.refusal)
        return httpx.Response(200, content=_OK_STREAM, headers={"content-type": "text/event-stream"})

    def transport(self, request_fallback: RequestParameterFallback | None = None) -> OpenAIAccountResponsesTransport:
        return OpenAIAccountResponsesTransport(
            transport=httpx.MockTransport(self.handler),
            request_fallback=request_fallback,
        )


def _body(model: str = "gpt-5.6-luna", **extra) -> dict:
    return {**build_request_body(model=model, messages="hi", stop="\n\n"), **extra}


def test_configured_stop_reaches_the_body():
    """``ModelRequestConfig.stop`` is still sent: some Responses endpoints (e.g. Qwen via OpenRouter) honour it."""
    model = Model(
        ModelClientConfig(
            client_provider="OpenAI",
            api_key="mock-api-key",
            api_base="https://api.openai.com/v1",
            api_mode="responses",
        ),
        ModelRequestConfig(model="gpt-5.6-luna", stop="END"),
    )

    body = model._client._build_responses_request_body(
        messages="hi",
        tools=None,
        temperature=None,
        top_p=None,
        model=None,
        max_tokens=None,
        stop=None,
    )

    assert body["stop"] == "END"


def test_only_the_responses_fallbacks_retry_without_stop():
    """Chat mode keeps sending ``stop`` as before; both Responses clients and the transport retry."""
    model = Model(
        ModelClientConfig(
            client_provider="OpenAI",
            api_key="mock-api-key",
            api_base="https://api.openai.com/v1",
            api_mode="responses",
        ),
        ModelRequestConfig(model="gpt-5.6-luna"),
    )
    account_client = OpenAIAccountModelClient(
        ModelRequestConfig(model="gpt-5.6-luna"),
        ModelClientConfig(
            client_provider=ProviderType.OpenAIAccount,
            api_key="",
            api_base=DEFAULT_OPENAI_ACCOUNT_BASE_URL,
            verify_ssl=False,
        ),
    )

    assert model._client._responses_request_fallback._retry_without_stop is True
    assert model._client._chat_request_fallback._retry_without_stop is False
    assert account_client._request_fallback._retry_without_stop is True
    assert OpenAIAccountResponsesTransport()._request_fallback._retry_without_stop is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "refusal",
    [
        (400, _OPENAI_STOP_ERROR),
        (400, _DETAIL_STOP_ERROR),
        (400, "Unrecognized parameter: `stop`"),
        (400, _OPENROUTER_WRAPPED_ERROR),
        (422, {"error": {"message": "stop is not supported for this model"}}),
        (400, {"error": {"code": "InvalidParameter", "message": "参数 stop 不支持"}}),
        (400, _ESCAPED_CHINESE_ERROR),
        (400, {"error": {"message": "Bad request", "param": "stop", "code": "unsupported_parameter"}}),
    ],
    ids=[
        "openai_error_param",
        "detail_message",
        "plain_text",
        "openrouter_wrapped",
        "422",
        "chinese",
        "escaped",
        "refusal_code",
    ],
)
async def test_refused_stop_is_retried_without_it(refusal, warnings):
    """OpenAI's ``/v1/responses`` answers ``400 Unknown parameter: 'stop'``; the retry drops it."""
    endpoint = _Endpoint(refusal=refusal)

    message = await endpoint.transport().create_response(body=_body(), access_token="access-token")

    assert message.content == "ok"
    assert ["stop" in body for body in endpoint.bodies] == [True, False]
    assert len(warnings) == 1
    text, _, fields = warnings[0]
    assert "rejected stop" in text
    assert fields["event_type"] == LogEventType.LLM_CALL_ERROR
    assert fields["model_name"] == "gpt-5.6-luna"


@pytest.mark.asyncio
async def test_stop_retry_does_not_need_the_fallback_scope():
    """Unlike temperature, a refused ``stop`` is retried by default; inside the scope it behaves the same."""
    outside = _Endpoint(refusal=(400, _OPENAI_STOP_ERROR))
    inside = _Endpoint(refusal=(400, _OPENAI_STOP_ERROR))

    await outside.transport().create_response(body=_body(), access_token="access-token")
    with disabled_thinking_fallback_scope():
        await inside.transport().create_response(body=_body(), access_token="access-token")

    assert ["stop" in body for body in outside.bodies] == [True, False]
    assert ["stop" in body for body in inside.bodies] == [True, False]


@pytest.mark.asyncio
async def test_fallback_without_the_option_does_not_retry():
    endpoint = _Endpoint(refusal=(400, _OPENAI_STOP_ERROR))
    fallback = RequestParameterFallback("OpenAI", "OpenAI-compatible")

    with pytest.raises(OpenAIAccountResponsesError):
        await endpoint.transport(fallback).create_response(body=_body(), access_token="access-token")

    assert len(endpoint.bodies) == 1


@pytest.mark.asyncio
async def test_refusal_is_remembered_by_the_client_fallback(warnings):
    endpoint = _Endpoint(refusal=(400, _OPENAI_STOP_ERROR))
    fallback = _stop_fallback()

    await endpoint.transport(fallback).create_response(body=_body(), access_token="access-token")
    # A fresh transport sharing the fallback, as the API-key client builds one per call.
    await endpoint.transport(fallback).create_response(body=_body(), access_token="access-token")

    assert ["stop" in body for body in endpoint.bodies] == [True, False, False]
    assert len(warnings) == 1


@pytest.mark.asyncio
async def test_memory_is_per_model_and_per_client():
    fallback = _stop_fallback()
    await (
        _Endpoint(refusal=(400, _OPENAI_STOP_ERROR))
        .transport(fallback)
        .create_response(body=_body(), access_token="access-token")
    )

    accepting = _Endpoint()
    await accepting.transport(fallback).create_response(body=_body(model="other-model"), access_token="access-token")
    await accepting.transport(_stop_fallback()).create_response(body=_body(), access_token="access-token")

    assert all("stop" in body for body in accepting.bodies)


@pytest.mark.asyncio
async def test_memory_keeps_only_the_newest_models(monkeypatch):
    monkeypatch.setattr(request_parameter_fallback, "_MAX_STOP_CACHE_ENTRIES", 2)
    fallback = _stop_fallback()
    endpoint = _Endpoint(refusal=(400, _OPENAI_STOP_ERROR))

    for model in ("model-a", "model-b", "model-c"):
        await endpoint.transport(fallback).create_response(body=_body(model=model), access_token="access-token")

    assert list(fallback._models_rejecting_stop) == ["model-b", "model-c"]


@pytest.mark.asyncio
async def test_failed_retry_is_not_remembered():
    requests = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.content.decode()))
        if "stop" in requests[-1]:
            return httpx.Response(400, json=_OPENAI_STOP_ERROR)
        return httpx.Response(500, text="upstream unavailable")

    fallback = _stop_fallback()
    transport = OpenAIAccountResponsesTransport(transport=httpx.MockTransport(handler), request_fallback=fallback)

    with pytest.raises(OpenAIAccountResponsesError):
        await transport.create_response(body=_body(), access_token="access-token")

    assert len(requests) == 2
    assert not fallback._models_rejecting_stop


@pytest.mark.asyncio
async def test_stop_and_temperature_are_each_retried_once_inside_the_scope():
    requests = []

    async def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode())
        requests.append(body)
        if "stop" in body:
            return httpx.Response(400, json=_OPENAI_STOP_ERROR)
        if "temperature" in body:
            return httpx.Response(400, json=_TEMPERATURE_ERROR)
        return httpx.Response(200, content=_OK_STREAM, headers={"content-type": "text/event-stream"})

    transport = OpenAIAccountResponsesTransport(transport=httpx.MockTransport(handler))

    with disabled_thinking_fallback_scope():
        message = await transport.create_response(body=_body(temperature=0.0), access_token="access-token")

    assert message.content == "ok"
    assert [("stop" in body, "temperature" in body) for body in requests] == [
        (True, True),
        (False, True),
        (False, False),
    ]


@pytest.mark.asyncio
async def test_streaming_yields_the_retried_response(warnings):
    endpoint = _Endpoint(refusal=(400, _OPENAI_STOP_ERROR))

    chunks = [chunk async for chunk in endpoint.transport().stream_response(body=_body(), access_token="access-token")]

    assert "".join(str(chunk.content or "") for chunk in chunks) == "ok"
    assert len(endpoint.bodies) == 2
    assert warnings[0][2]["is_stream"] is True


@pytest.mark.asyncio
async def test_accepted_stop_is_sent_once(warnings):
    endpoint = _Endpoint()

    await endpoint.transport().create_response(body=_body(), access_token="access-token")

    assert len(endpoint.bodies) == 1
    assert endpoint.bodies[0]["stop"] == "\n\n"
    assert warnings == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "refusal",
    [
        (400, _TEMPERATURE_ERROR),
        (400, {"error": {"message": "Unknown parameter: 'stop_sequences'"}}),
        (400, "Provider returned error"),
        (400, _OPENROUTER_STOP_VALUE_ERROR),
        (400, _OPENAI_STOP_TYPE_ERROR),
        (400, _OPENAI_STOP_LENGTH_ERROR),
        (400, {"error": {"code": 1210, "message": "API 调用参数有误，请检查文档。"}}),
        (403, {"error": {"message": "Unknown parameter: 'stop'", "param": "stop"}}),
        (429, {"error": {"message": "Unknown parameter: 'stop'", "param": "stop"}}),
        (500, "Unsupported parameter: stop"),
    ],
    ids=[
        "other_param",
        "longer_name",
        "vague",
        "stop_value_error",
        "openai_type_error",
        "openai_length_error",
        "zhipu_generic",
        "forbidden",
        "rate_limit",
        "server_error",
    ],
)
async def test_other_errors_are_raised_without_retry(refusal, warnings):
    endpoint = _Endpoint(refusal=refusal)

    with pytest.raises(OpenAIAccountResponsesError):
        await endpoint.transport().create_response(body=_body(), access_token="access-token")

    # Nothing is remembered without a successful retry, so one request means no fallback ran.
    assert len(endpoint.bodies) == 1
    assert warnings == []


@pytest.mark.asyncio
async def test_error_naming_stop_is_not_retried_when_stop_was_not_sent():
    requests = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(400, json=_OPENAI_STOP_ERROR)

    transport = OpenAIAccountResponsesTransport(transport=httpx.MockTransport(handler))
    body = build_request_body(model="gpt-5.6-luna", messages="hi")

    with pytest.raises(OpenAIAccountResponsesError):
        await transport.create_response(body=body, access_token="access-token")

    assert len(requests) == 1
