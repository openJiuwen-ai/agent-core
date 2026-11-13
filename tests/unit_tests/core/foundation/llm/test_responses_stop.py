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
from openjiuwen.core.foundation.llm.schema.config import ProviderType
from openjiuwen.core.foundation.llm.utils import responses_transport
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
# OpenRouter wraps an upstream refusal: its own message is vague and the original is in metadata.raw.
_OPENROUTER_WRAPPED_ERROR = {
    "error": {
        "message": "Provider returned error",
        "code": 400,
        "metadata": {"raw": json.dumps(_OPENAI_STOP_ERROR, indent=2), "provider_name": "Azure"},
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


@pytest.fixture(autouse=True)
def _isolated_memory(monkeypatch):
    monkeypatch.setattr(responses_transport, "_REFUSED_PARAMS", set(), raising=False)


@pytest.fixture
def warnings(monkeypatch):
    captured = []

    def fake_warning(message, *args, **kwargs):
        captured.append((message, args, kwargs))

    monkeypatch.setattr(
        "openjiuwen.core.foundation.llm.utils.responses_transport.logger.warning",
        fake_warning,
    )
    return captured


class _Endpoint:
    """Fake Responses endpoint that records each request body and may refuse ``stop``."""

    def __init__(self, refusal: tuple[int, object] | None = None):
        self.refusal = refusal
        self.bodies: list[dict] = []

    async def handler(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode())
        self.bodies.append(body)
        if self.refusal and "stop" in body:
            status, payload = self.refusal
            if isinstance(payload, bytes):
                return httpx.Response(status, content=payload, headers={"content-type": "application/json"})
            if isinstance(payload, str):
                return httpx.Response(status, text=payload)
            return httpx.Response(status, json=payload)
        return httpx.Response(200, content=_OK_STREAM, headers={"content-type": "text/event-stream"})

    def transport(self, base_url: str = "https://api.openai.com/v1") -> OpenAIAccountResponsesTransport:
        return OpenAIAccountResponsesTransport(base_url=base_url, transport=httpx.MockTransport(self.handler))


def _body(model: str = "gpt-5.6-luna") -> dict:
    return build_request_body(model=model, messages="hi", stop="\n\n")


def test_stop_is_sent_by_the_body_builder():
    """``stop`` is not dropped up front: some Responses endpoints (e.g. Qwen via OpenRouter) honour it."""
    assert _body()["stop"] == "\n\n"


def test_configured_stop_reaches_the_api_key_body():
    """``ModelRequestConfig.stop`` is used when the call passes none."""
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


def test_configured_stop_reaches_the_account_body():
    client = OpenAIAccountModelClient(
        ModelRequestConfig(model="gpt-5.6-luna", stop="END"),
        ModelClientConfig(
            client_provider=ProviderType.OpenAIAccount,
            api_key="",
            api_base=DEFAULT_OPENAI_ACCOUNT_BASE_URL,
            verify_ssl=False,
        ),
    )

    body = client._build_openai_account_request_body(
        messages="hi",
        tools=None,
        temperature=None,
        top_p=None,
        model=None,
        max_tokens=None,
        stop=None,
    )

    assert body["stop"] == "END"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "refusal",
    [
        (400, _OPENAI_STOP_ERROR),
        (400, {"detail": "Unsupported parameter: stop"}),
        (400, "Unrecognized parameter: `stop`"),
        (400, _OPENROUTER_WRAPPED_ERROR),
        (422, {"error": {"message": "stop is not supported for this model"}}),
        (400, {"error": {"code": "InvalidParameter", "message": "参数 stop 不支持"}}),
        (400, _ESCAPED_CHINESE_ERROR),
    ],
    ids=["openai_error_param", "detail_message", "plain_text", "openrouter_wrapped", "422", "chinese", "escaped"],
)
async def test_refused_stop_is_retried_without_it(refusal, warnings):
    """OpenAI's ``/v1/responses`` answers ``400 Unknown parameter: 'stop'``; the retry drops it."""
    endpoint = _Endpoint(refusal=refusal)

    message = await endpoint.transport().create_response(body=_body(), access_token="access-token")

    assert message.content == "ok"
    assert ["stop" in body for body in endpoint.bodies] == [True, False]
    assert len(warnings) == 1
    _, args, fields = warnings[0]
    assert args == ("stop",)
    assert fields["event_type"] == LogEventType.LLM_CALL_ERROR
    assert fields["model_name"] == "gpt-5.6-luna"
    assert fields["is_stream"] is False
    assert fields["extra_params"]["dropped_param"] == "stop"


@pytest.mark.asyncio
async def test_refusal_is_remembered_for_later_requests(warnings):
    endpoint = _Endpoint(refusal=(400, _OPENAI_STOP_ERROR))

    await endpoint.transport().create_response(body=_body(), access_token="access-token")
    # A fresh transport, as the API-key client builds one per call.
    await endpoint.transport().create_response(body=_body(), access_token="access-token")

    assert ["stop" in body for body in endpoint.bodies] == [True, False, False]
    assert len(warnings) == 1


@pytest.mark.asyncio
async def test_memory_is_per_endpoint_and_model():
    endpoint = _Endpoint(refusal=(400, _OPENAI_STOP_ERROR))
    await endpoint.transport().create_response(body=_body(), access_token="access-token")

    accepting = _Endpoint()
    await accepting.transport().create_response(body=_body(model="other-model"), access_token="access-token")
    await accepting.transport("https://openrouter.ai/api/v1").create_response(body=_body(), access_token="access-token")

    assert all("stop" in body for body in accepting.bodies)


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
    assert responses_transport._REFUSED_PARAMS == set()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "refusal",
    [
        (400, {"error": {"message": "Unsupported parameter: 'temperature'", "param": "temperature"}}),
        (400, {"error": {"message": "Unknown parameter: 'stop_sequences'"}}),
        (400, "Provider returned error"),
        (400, _OPENROUTER_STOP_VALUE_ERROR),
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
        "zhipu_generic",
        "forbidden",
        "rate_limit",
        "server_error",
    ],
)
async def test_other_errors_are_raised_without_retry(refusal):
    endpoint = _Endpoint(refusal=refusal)

    with pytest.raises(OpenAIAccountResponsesError):
        await endpoint.transport().create_response(body=_body(), access_token="access-token")

    assert len(endpoint.bodies) == 1
    assert responses_transport._REFUSED_PARAMS == set()


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
