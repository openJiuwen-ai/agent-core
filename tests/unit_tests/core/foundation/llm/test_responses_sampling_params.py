# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""The API-key Responses client must send temperature, top_p and the output limit like Chat mode."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from openjiuwen.core.foundation.llm import Model, ModelClientConfig, ModelRequestConfig
from openjiuwen.core.foundation.llm.model_clients.openai_model_client import OpenAIModelClient
from openjiuwen.core.foundation.llm.utils.responses_transport import OpenAIAccountResponsesTransport

_RESPONSES_SSE = (
    "event: response.output_text.delta\n"
    'data: {"delta":"ok"}\n\n'
    "event: response.completed\n"
    'data: {"response":{"id":"resp-1","status":"completed",'
    '"usage":{"input_tokens":1,"output_tokens":1,"total_tokens":2}}}\n\n'
).encode()


def _client_config() -> ModelClientConfig:
    return ModelClientConfig(
        client_provider="OpenAI",
        api_key="sk-test",
        api_base="https://api.openai.com/v1",
        api_mode="responses",
        verify_ssl=False,
    )


def _body(config: ModelRequestConfig, **call_kwargs: Any) -> dict[str, Any]:
    client = OpenAIModelClient(config, _client_config())
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
