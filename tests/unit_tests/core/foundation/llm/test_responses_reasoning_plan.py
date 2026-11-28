# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""The API-key Responses client must translate ``ReasoningConfig`` like the other clients."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from openjiuwen.core.foundation.llm import Model, ModelClientConfig, ModelRequestConfig
from openjiuwen.core.foundation.llm.model_clients.openai_model_client import OpenAIModelClient
from openjiuwen.core.foundation.llm.schema.config import ReasoningConfig
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


def _body(configured: Any = None, **call_kwargs: Any) -> dict[str, Any]:
    config = ModelRequestConfig(model="gpt-5.6-luna", reasoning=configured)
    client = OpenAIModelClient(config, _client_config())
    return client._build_responses_request_body(
        messages="hello",
        tools=None,
        temperature=None,
        top_p=None,
        model=None,
        max_tokens=None,
        stop=None,
        **call_kwargs,
    )


@pytest.mark.parametrize(
    "configured, expected",
    [
        (ReasoningConfig(effort="high"), {"effort": "high"}),
        (ReasoningConfig(mode="enabled", effort="low"), {"effort": "low"}),
        ({"mode": "auto", "effort": "medium"}, {"effort": "medium"}),
        ({"effort": "none"}, {"effort": "none"}),
    ],
)
def test_configured_reasoning_is_sent_as_responses_reasoning(configured, expected):
    body = _body(configured)

    assert body["reasoning"] == expected


@pytest.mark.parametrize(
    "configured",
    [
        ReasoningConfig(mode="disabled"),
        ReasoningConfig(mode="disabled", effort="high"),
        ReasoningConfig(mode="enabled"),
        ReasoningConfig(mode="enabled", budget_tokens=2000),
        ReasoningConfig(),
    ],
)
def test_configured_reasoning_without_a_responses_effort_is_omitted(configured):
    """``mode`` and ``budget_tokens`` have no Responses field; disabled means "omit reasoning"."""
    body = _body(configured)

    assert "reasoning" not in body
    assert "mode" not in body
    assert "budget_tokens" not in body


def test_call_reasoning_config_overrides_configured_reasoning():
    body = _body(ReasoningConfig(effort="high"), reasoning=ReasoningConfig(effort="low"))

    assert body["reasoning"] == {"effort": "low"}


def test_call_reasoning_dict_with_new_keys_is_translated():
    body = _body(reasoning={"mode": "enabled", "effort": "minimal"})

    assert body["reasoning"] == {"effort": "minimal"}


def test_call_reasoning_none_clears_configured_reasoning():
    body = _body(ReasoningConfig(effort="high"), reasoning=None)

    assert "reasoning" not in body


@pytest.mark.parametrize("where", ["config", "call"])
def test_raw_responses_reasoning_dict_is_sent_unchanged(where):
    """Dicts that already use Responses fields (e.g. ``summary``) are passed through as before."""
    raw = {"effort": "high", "summary": "auto"}
    body = _body(raw) if where == "config" else _body(reasoning=raw)

    assert body["reasoning"] == raw


def test_explicit_legacy_reasoning_controls_win_over_reasoning_config():
    """Same rule as Chat Completions: a raw ``thinking`` field means the config is ignored."""
    body = _body(ReasoningConfig(effort="high"), extra_body={"thinking": {"type": "enabled"}})

    assert body["thinking"] == {"type": "enabled"}
    assert "reasoning" not in body


def test_other_request_extras_are_still_forwarded():
    config = ModelRequestConfig(
        model="gpt-5.6-luna",
        reasoning=ReasoningConfig(effort="high"),
        prompt_cache_key="cache-1",
    )
    client = OpenAIModelClient(config, _client_config())
    body = client._build_responses_request_body(
        messages="hello",
        tools=None,
        temperature=None,
        top_p=None,
        model=None,
        max_tokens=None,
        stop=None,
        extra_body={"text": {"verbosity": "low"}},
    )

    assert body["reasoning"] == {"effort": "high"}
    assert body["prompt_cache_key"] == "cache-1"
    assert body["text"] == {"verbosity": "low"}


def _responses_model(sent_bodies: list[dict], reasoning: Any) -> Model:
    """A Responses-mode Model whose HTTP calls are answered locally and recorded."""

    async def handler(request: httpx.Request) -> httpx.Response:
        sent_bodies.append(json.loads(request.content.decode()))
        return httpx.Response(200, content=_RESPONSES_SSE, headers={"content-type": "text/event-stream"})

    model = Model(_client_config(), ModelRequestConfig(model="gpt-5.6-luna", reasoning=reasoning))
    client = model._client
    client._make_responses_transport = lambda **_: OpenAIAccountResponsesTransport(
        transport=httpx.MockTransport(handler),
        request_fallback=client._responses_request_fallback,
    )
    return model


@pytest.mark.asyncio
async def test_invoke_sends_translated_reasoning():
    sent_bodies: list[dict] = []
    model = _responses_model(sent_bodies, ReasoningConfig(mode="enabled", effort="high"))

    response = await model.invoke("hello")

    assert response.content == "ok"
    assert len(sent_bodies) == 1
    assert sent_bodies[0]["reasoning"] == {"effort": "high"}


@pytest.mark.asyncio
async def test_stream_sends_translated_reasoning():
    sent_bodies: list[dict] = []
    model = _responses_model(sent_bodies, ReasoningConfig(mode="disabled"))

    chunks = [chunk async for chunk in model.stream("hello")]

    assert "".join(chunk.content or "" for chunk in chunks) == "ok"
    assert len(sent_bodies) == 1
    assert "reasoning" not in sent_bodies[0]
