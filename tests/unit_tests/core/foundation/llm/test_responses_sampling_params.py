# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""The API-key Responses client must send temperature, top_p and the output limit like Chat mode."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from openjiuwen.core.common.exception.errors import BaseError
from openjiuwen.core.foundation.llm import Model, ModelClientConfig, ModelRequestConfig
from openjiuwen.core.foundation.llm.model_clients.openai_account_model_client import (
    DEFAULT_OPENAI_ACCOUNT_BASE_URL,
    OpenAIAccountModelClient,
)
from openjiuwen.core.foundation.llm.model_clients.openai_model_client import OpenAIModelClient
from openjiuwen.core.foundation.llm.schema.config import ProviderType, ReasoningConfig
from openjiuwen.core.foundation.llm.utils.request_parameter_fallback import RequestParameterFallback
from openjiuwen.core.foundation.llm.utils.responses_transport import OpenAIAccountResponsesTransport
from openjiuwen.core.foundation.llm.utils.responses_utils import OpenAIAccountResponsesError, build_request_body

_RESPONSES_SSE = (
    "event: response.output_text.delta\n"
    'data: {"delta":"ok"}\n\n'
    "event: response.completed\n"
    'data: {"response":{"id":"resp-1","status":"completed",'
    '"usage":{"input_tokens":1,"output_tokens":1,"total_tokens":2}}}\n\n'
).encode()


_OPENROUTER_BASE = "https://openrouter.ai/api/v1"


def _client_config(api_base: str = _OPENROUTER_BASE) -> ModelClientConfig:
    return ModelClientConfig(
        client_provider="OpenAI",
        api_key="sk-test",
        api_base=api_base,
        api_mode="responses",
        verify_ssl=False,
    )


def _body(config: ModelRequestConfig, api_base: str = _OPENROUTER_BASE, **call_kwargs: Any) -> dict[str, Any]:
    client = OpenAIModelClient(config, _client_config(api_base))
    params: dict[str, Any] = {"temperature": None, "top_p": None, "max_tokens": None}
    params.update(call_kwargs)
    return client._build_responses_request_body(
        messages="hello",
        tools=None,
        model=None,
        stop=None,
        **params,
    )


def test_configured_sampling_and_output_limit_are_sent():
    body = _body(ModelRequestConfig(model="deepseek-v4.1-flash", temperature=0.2, top_p=0.8, max_tokens=512))

    assert body["temperature"] == 0.2
    assert body["top_p"] == 0.8
    assert body["max_output_tokens"] == 512


def test_call_values_override_configured_values():
    body = _body(
        ModelRequestConfig(model="deepseek-v4.1-flash", temperature=0.2, top_p=0.8, max_tokens=512),
        temperature=0.7,
        top_p=0.5,
        max_tokens=64,
    )

    assert body["temperature"] == 0.7
    assert body["top_p"] == 0.5
    assert body["max_output_tokens"] == 64


def test_unset_values_are_left_out():
    body = _body(ModelRequestConfig(model="deepseek-v4.1-flash"))

    assert "temperature" not in body
    assert "top_p" not in body
    assert "max_output_tokens" not in body


@pytest.mark.parametrize("value", [0, 0.0])
def test_zero_temperature_is_sent(value):
    body = _body(ModelRequestConfig(model="deepseek-v4.1-flash"), temperature=value)

    assert body["temperature"] == value


def test_callers_can_still_opt_out():
    body = _body(
        ModelRequestConfig(model="gpt-6.1-sol", temperature=0.2, top_p=0.8, max_tokens=512),
        send_sampling_params=False,
        send_max_output_tokens=False,
    )

    assert "temperature" not in body
    assert "top_p" not in body
    assert "max_output_tokens" not in body
    assert "send_sampling_params" not in body
    assert "send_max_output_tokens" not in body


def test_opt_out_flags_are_independent():
    config = ModelRequestConfig(model="gpt-6.1-sol", temperature=0.2, top_p=0.8, max_tokens=512)

    no_sampling = _body(config, send_sampling_params=False)
    no_limit = _body(config, send_max_output_tokens=False)

    assert "temperature" not in no_sampling
    assert "top_p" not in no_sampling
    assert no_sampling["max_output_tokens"] == 512
    assert no_limit["temperature"] == 0.2
    assert no_limit["top_p"] == 0.8
    assert "max_output_tokens" not in no_limit


@pytest.mark.parametrize(
    "temperature, top_p, expected",
    [
        (0.2, 0.8, {"temperature": 0.2}),
        (None, 0.8, {"top_p": 0.8}),
        (0.2, None, {"temperature": 0.2}),
    ],
)
def test_openai_com_keeps_only_one_of_temperature_and_top_p(temperature, top_p, expected):
    """Same rule as Chat mode for api.openai.com: temperature wins when both are set."""
    config = ModelRequestConfig(model="gpt-5.6-luna")
    body = _body(config, api_base="https://api.openai.com/v1", temperature=temperature, top_p=top_p)

    assert {key: body[key] for key in ("temperature", "top_p") if key in body} == expected


def test_raw_max_output_tokens_still_wins():
    body = _body(ModelRequestConfig(model="deepseek-v4.1-flash", max_tokens=512), max_output_tokens=128)

    assert body["max_output_tokens"] == 128


def _responses_model(sent_bodies: list[dict], config: ModelRequestConfig) -> Model:
    """A Responses-mode Model whose HTTP calls are answered locally and recorded."""

    async def handler(request: httpx.Request) -> httpx.Response:
        sent_bodies.append(json.loads(request.content.decode()))
        return httpx.Response(200, content=_RESPONSES_SSE, headers={"content-type": "text/event-stream"})

    model = Model(_client_config(), config)
    client = model._client
    client._make_responses_transport = lambda **_: OpenAIAccountResponsesTransport(
        transport=httpx.MockTransport(handler),
        request_fallback=client._responses_request_fallback,
    )
    return model


@pytest.mark.asyncio
async def test_invoke_sends_sampling_and_output_limit():
    sent_bodies: list[dict] = []
    model = _responses_model(sent_bodies, ModelRequestConfig(model="deepseek-v4.1-flash", max_tokens=512))

    response = await model.invoke("hello", temperature=0.3, top_p=0.9)

    assert response.content == "ok"
    assert len(sent_bodies) == 1
    assert sent_bodies[0]["temperature"] == 0.3
    assert sent_bodies[0]["top_p"] == 0.9
    assert sent_bodies[0]["max_output_tokens"] == 512


@pytest.mark.asyncio
async def test_stream_sends_sampling_and_output_limit():
    sent_bodies: list[dict] = []
    model = _responses_model(sent_bodies, ModelRequestConfig(model="deepseek-v4.1-flash", temperature=0.4, top_p=0.6))

    chunks = [chunk async for chunk in model.stream("hello", max_tokens=256)]

    assert "".join(chunk.content or "" for chunk in chunks) == "ok"
    assert len(sent_bodies) == 1
    assert sent_bodies[0]["temperature"] == 0.4
    assert sent_bodies[0]["top_p"] == 0.6
    assert sent_bodies[0]["max_output_tokens"] == 256


_TEMPERATURE_ERROR = {
    "error": {
        "message": "Unsupported parameter: 'temperature' is not supported with this model.",
        "type": "invalid_request_error",
        "param": "temperature",
        "code": "unsupported_parameter",
    }
}
_TOP_P_ERROR = {
    "error": {
        "message": "Unsupported parameter: 'top_p' is not supported with this model.",
        "type": "invalid_request_error",
        "param": "top_p",
        "code": "unsupported_parameter",
    }
}
# The ChatGPT-login backend refuses unknown fields in this shape.
_DETAIL_TOP_P_ERROR = {"detail": "Unsupported parameter: top_p"}
# Value errors also set ``param``; they are about the value, not a refusal of the field.
_TOP_P_VALUE_ERROR = {
    "error": {
        "message": "Invalid 'top_p': decimal above maximum value. Expected a value <= 1, but got 2 instead.",
        "type": "invalid_request_error",
        "param": "top_p",
        "code": "decimal_above_max_value",
    }
}
_MAX_OUTPUT_TOKENS_ERROR = {
    "error": {
        "message": "Unsupported parameter: 'max_output_tokens' is not supported with this model.",
        "type": "invalid_request_error",
        "param": "max_output_tokens",
        "code": "unsupported_parameter",
    }
}


def _refusing_model(sent_bodies: list[dict], refusals: dict[str, dict], **config: Any) -> Model:
    """A Responses-mode Model whose endpoint answers 400 to any request that carries a field in ``refusals``."""

    async def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode())
        sent_bodies.append(body)
        for field, error in refusals.items():
            if field in body:
                return httpx.Response(400, json=error)
        return httpx.Response(200, content=_RESPONSES_SSE, headers={"content-type": "text/event-stream"})

    settings = {"temperature": 0.7, "top_p": 0.9, "max_tokens": 64, **config}
    model = Model(_client_config(), ModelRequestConfig(model="gpt-6.1-sol", **settings))
    client = model._client
    client._make_responses_transport = lambda **_: OpenAIAccountResponsesTransport(
        transport=httpx.MockTransport(handler),
        request_fallback=client._responses_request_fallback,
    )
    return model


def test_only_the_api_key_responses_fallback_retries_without_temperature_or_top_p():
    """Chat mode, the ChatGPT-login client and the transport default keep the opt-in scope."""
    model = Model(_client_config(), ModelRequestConfig(model="gpt-6.1-sol"))
    account_client = OpenAIAccountModelClient(
        ModelRequestConfig(model="gpt-6.1-sol"),
        ModelClientConfig(
            client_provider=ProviderType.OpenAIAccount,
            api_key="",
            api_base=DEFAULT_OPENAI_ACCOUNT_BASE_URL,
            verify_ssl=False,
        ),
    )
    fallbacks = {
        "api_key_responses": model._client._responses_request_fallback,
        "chat": model._client._chat_request_fallback,
        "account": account_client._request_fallback,
        "transport_default": OpenAIAccountResponsesTransport()._request_fallback,
    }

    assert {name: fallback._retry_without_temperature for name, fallback in fallbacks.items()} == {
        "api_key_responses": True,
        "chat": False,
        "account": False,
        "transport_default": False,
    }
    assert {name: fallback._retry_without_top_p for name, fallback in fallbacks.items()} == {
        "api_key_responses": True,
        "chat": False,
        "account": False,
        "transport_default": False,
    }


@pytest.mark.asyncio
async def test_invoke_retries_without_a_refused_temperature_and_remembers_the_model():
    """Outside ``disabled_thinking_fallback_scope``, the API-key Responses client retries a refused temperature."""
    sent_bodies: list[dict] = []
    model = _refusing_model(sent_bodies, {"temperature": _TEMPERATURE_ERROR})

    first = await model.invoke("hello")
    second = await model.invoke("hello again")

    assert first.content == "ok"
    assert second.content == "ok"
    assert ["temperature" in body for body in sent_bodies] == [True, False, False]
    assert all(body["top_p"] == 0.9 for body in sent_bodies)


@pytest.mark.asyncio
async def test_stream_retries_without_a_refused_temperature():
    sent_bodies: list[dict] = []
    model = _refusing_model(sent_bodies, {"temperature": _TEMPERATURE_ERROR})

    chunks = [chunk async for chunk in model.stream("hello")]

    assert "".join(chunk.content or "" for chunk in chunks) == "ok"
    assert ["temperature" in body for body in sent_bodies] == [True, False]


@pytest.mark.asyncio
@pytest.mark.parametrize("refusal", [_TOP_P_ERROR, _DETAIL_TOP_P_ERROR], ids=["openai_error_param", "detail_message"])
async def test_invoke_retries_without_a_refused_top_p_and_remembers_the_model(refusal):
    sent_bodies: list[dict] = []
    model = _refusing_model(sent_bodies, {"top_p": refusal})

    first = await model.invoke("hello")
    second = await model.invoke("hello again")

    assert first.content == "ok"
    assert second.content == "ok"
    assert ["top_p" in body for body in sent_bodies] == [True, False, False]
    assert all(body["temperature"] == 0.7 for body in sent_bodies)


@pytest.mark.asyncio
async def test_stream_retries_without_a_refused_top_p():
    sent_bodies: list[dict] = []
    model = _refusing_model(sent_bodies, {"top_p": _TOP_P_ERROR})

    chunks = [chunk async for chunk in model.stream("hello")]

    assert "".join(chunk.content or "" for chunk in chunks) == "ok"
    assert ["top_p" in body for body in sent_bodies] == [True, False]


@pytest.mark.asyncio
async def test_temperature_and_top_p_are_each_retried_once():
    """Like the ChatGPT-login backend, which refuses both, one field at a time."""
    sent_bodies: list[dict] = []
    model = _refusing_model(sent_bodies, {"temperature": _TEMPERATURE_ERROR, "top_p": _TOP_P_ERROR})

    response = await model.invoke("hello")

    assert response.content == "ok"
    assert [("temperature" in body, "top_p" in body) for body in sent_bodies] == [
        (True, True),
        (False, True),
        (False, False),
    ]


@pytest.mark.asyncio
async def test_top_p_memory_is_per_reasoning_setting():
    """OpenAI accepts top_p only at reasoning effort ``none``, so a refusal at another effort is not reused there."""
    sent_bodies: list[dict] = []
    model = _refusing_model(sent_bodies, {"top_p": _TOP_P_ERROR})

    await model.invoke("hello", reasoning=ReasoningConfig(effort="high"))
    await model.invoke("hello", reasoning=ReasoningConfig(effort="high"))
    await model.invoke("hello", reasoning=ReasoningConfig(effort="none"))

    assert [("top_p" in body, body["reasoning"]["effort"]) for body in sent_bodies] == [
        (True, "high"),
        (False, "high"),
        (False, "high"),
        (True, "none"),
        (False, "none"),
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field, refusal",
    [("max_output_tokens", _MAX_OUTPUT_TOKENS_ERROR), ("top_p", _TOP_P_VALUE_ERROR)],
    ids=["other_field", "top_p_value_error"],
)
async def test_other_errors_are_not_retried(field, refusal):
    sent_bodies: list[dict] = []
    model = _refusing_model(sent_bodies, {field: refusal})

    with pytest.raises(BaseError, match=field):
        await model.invoke("hello")

    assert len(sent_bodies) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field, refusal",
    [("temperature", _TEMPERATURE_ERROR), ("top_p", _TOP_P_ERROR)],
)
async def test_fallback_without_the_options_does_not_retry_outside_the_scope(field, refusal):
    sent_bodies: list[dict] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        sent_bodies.append(json.loads(request.content.decode()))
        return httpx.Response(400, json=refusal)

    transport = OpenAIAccountResponsesTransport(
        transport=httpx.MockTransport(handler),
        request_fallback=RequestParameterFallback("OpenAI", "OpenAI-compatible"),
    )
    body = build_request_body(model="gpt-6.1-sol", messages="hi", **{field: 0.7})

    with pytest.raises(OpenAIAccountResponsesError):
        await transport.create_response(body=body, access_token="access-token")

    assert len(sent_bodies) == 1


@pytest.mark.asyncio
async def test_refused_top_p_inside_extra_body_is_retried_without_it():
    """Like temperature, a refused top_p is also removed from ``extra_body``, and other extra fields are kept."""
    sent_bodies: list[dict] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode())
        sent_bodies.append(body)
        if "top_p" in body.get("extra_body", {}):
            return httpx.Response(400, json=_TOP_P_ERROR)
        return httpx.Response(200, content=_RESPONSES_SSE, headers={"content-type": "text/event-stream"})

    transport = OpenAIAccountResponsesTransport(
        transport=httpx.MockTransport(handler),
        request_fallback=RequestParameterFallback("OpenAI", "OpenAI Responses", retry_without_top_p=True),
    )
    body = {**build_request_body(model="gpt-6.1-sol", messages="hi"), "extra_body": {"top_p": 0.9, "routing": "keep"}}

    message = await transport.create_response(body=body, access_token="access-token")

    assert message.content == "ok"
    assert [sent.get("extra_body") for sent in sent_bodies] == [{"top_p": 0.9, "routing": "keep"}, {"routing": "keep"}]
