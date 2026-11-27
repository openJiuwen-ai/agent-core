# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Responses requests must send ``tool_choice`` and tool ``strict`` in the Responses shape."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from openjiuwen.core.foundation.llm import Model, ModelClientConfig, ModelRequestConfig
from openjiuwen.core.foundation.llm.utils.responses_transport import OpenAIAccountResponsesTransport
from openjiuwen.core.foundation.llm.utils.responses_utils import build_request_body
from openjiuwen.core.foundation.tool import ToolInfo

_PARAMETERS = {
    "type": "object",
    "properties": {"city": {"type": "string"}},
    "required": ["city"],
    "additionalProperties": False,
}
_CHAT_TOOL = {
    "type": "function",
    "function": {"name": "get_weather", "description": "Weather lookup", "parameters": _PARAMETERS},
}
_CHAT_TOOL_CHOICE = {"type": "function", "function": {"name": "get_weather"}}
_RESPONSES_TOOL_CHOICE = {"type": "function", "name": "get_weather"}

_RESPONSES_SSE = (
    "event: response.output_text.delta\n"
    'data: {"delta":"ok"}\n\n'
    "event: response.completed\n"
    'data: {"response":{"id":"resp-1","status":"completed",'
    '"usage":{"input_tokens":1,"output_tokens":1,"total_tokens":2}}}\n\n'
).encode()


def _chat_tool(**function_fields: Any) -> dict[str, Any]:
    return {"type": "function", "function": {**_CHAT_TOOL["function"], **function_fields}}


def _body(**kwargs: Any) -> dict[str, Any]:
    kwargs.setdefault("tools", [_CHAT_TOOL])
    return build_request_body(model="gpt-5.6-luna", messages="hello", **kwargs)


def test_chat_style_named_tool_choice_is_flattened():
    body = _body(tool_choice=_CHAT_TOOL_CHOICE)

    assert body["tool_choice"] == _RESPONSES_TOOL_CHOICE


def test_chat_style_named_tool_choice_from_extra_body_is_flattened():
    body = _body(extra_body={"tool_choice": _CHAT_TOOL_CHOICE})

    assert body["tool_choice"] == _RESPONSES_TOOL_CHOICE


def test_chat_style_tool_choice_does_not_mutate_the_caller_value():
    tool_choice = {"type": "function", "function": {"name": "get_weather"}}

    _body(tool_choice=tool_choice)

    assert tool_choice == {"type": "function", "function": {"name": "get_weather"}}


@pytest.mark.parametrize(
    "tool_choice",
    [
        "auto",
        "none",
        "required",
        _RESPONSES_TOOL_CHOICE,
        {"type": "allowed_tools", "mode": "auto", "tools": [_RESPONSES_TOOL_CHOICE]},
        {"type": "function", "function": {}},
    ],
)
def test_other_tool_choice_values_are_sent_unchanged(tool_choice):
    body = _body(tool_choice=tool_choice)

    assert body["tool_choice"] == tool_choice


@pytest.mark.parametrize("strict", [True, False])
def test_chat_style_tool_keeps_strict(strict):
    body = _body(tools=[_chat_tool(strict=strict)])

    assert body["tools"][0]["strict"] is strict
    assert body["tools"][0]["name"] == "get_weather"


@pytest.mark.parametrize("strict", [True, False])
def test_responses_style_tool_keeps_strict(strict):
    tool = {"type": "function", "name": "get_weather", "parameters": _PARAMETERS, "strict": strict}

    body = _body(tools=[tool])

    assert body["tools"][0]["strict"] is strict


@pytest.mark.parametrize(
    "tool",
    [
        _CHAT_TOOL,
        _chat_tool(strict=None),
        {"type": "function", "name": "get_weather", "parameters": _PARAMETERS},
        ToolInfo(name="get_weather", description="Weather lookup", parameters=_PARAMETERS),
    ],
)
def test_tool_without_strict_sends_no_strict(tool):
    """Responses reads a missing ``strict`` as "strict when the schema allows", so none is invented."""
    body = _body(tools=[tool])

    assert "strict" not in body["tools"][0]


def _client_config() -> ModelClientConfig:
    return ModelClientConfig(
        client_provider="OpenAI",
        api_key="sk-test",
        api_base="https://api.openai.com/v1",
        api_mode="responses",
        verify_ssl=False,
    )


def _responses_model(sent_bodies: list[dict]) -> Model:
    """A Responses-mode Model whose HTTP calls are answered locally and recorded."""

    async def handler(request: httpx.Request) -> httpx.Response:
        sent_bodies.append(json.loads(request.content.decode()))
        return httpx.Response(200, content=_RESPONSES_SSE, headers={"content-type": "text/event-stream"})

    model = Model(_client_config(), ModelRequestConfig(model="gpt-5.6-luna"))
    client = model._client
    client._make_responses_transport = lambda **_: OpenAIAccountResponsesTransport(
        transport=httpx.MockTransport(handler),
        request_fallback=client._responses_request_fallback,
    )
    return model


@pytest.mark.asyncio
async def test_invoke_sends_responses_tool_settings():
    sent_bodies: list[dict] = []
    model = _responses_model(sent_bodies)

    response = await model.invoke("hello", tools=[_chat_tool(strict=False)], tool_choice=_CHAT_TOOL_CHOICE)

    assert response.content == "ok"
    assert len(sent_bodies) == 1
    assert sent_bodies[0]["tool_choice"] == _RESPONSES_TOOL_CHOICE
    assert sent_bodies[0]["tools"][0]["strict"] is False


@pytest.mark.asyncio
async def test_stream_sends_responses_tool_settings():
    sent_bodies: list[dict] = []
    model = _responses_model(sent_bodies)

    chunks = [
        chunk async for chunk in model.stream("hello", tools=[_chat_tool(strict=True)], tool_choice=_CHAT_TOOL_CHOICE)
    ]

    assert "".join(chunk.content or "" for chunk in chunks) == "ok"
    assert len(sent_bodies) == 1
    assert sent_bodies[0]["tool_choice"] == _RESPONSES_TOOL_CHOICE
    assert sent_bodies[0]["tools"][0]["strict"] is True
