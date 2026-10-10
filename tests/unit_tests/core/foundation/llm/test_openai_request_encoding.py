# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Exercise request encoding with the real SDK and an in-memory HTTP transport."""

import json
from copy import deepcopy
from unittest.mock import Mock

import httpx
import pytest

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import BaseError
from openjiuwen.core.foundation.llm import ModelClientConfig, ModelRequestConfig, ToolMessage
from openjiuwen.core.foundation.llm.model_clients.openai_model_client import OpenAIModelClient


@pytest.fixture
def request_client(monkeypatch):
    requests = []
    clients = []
    original_init = httpx.AsyncClient.__init__
    response_status = [200]

    def handler(request):
        body = json.loads(request.content)
        requests.append(body)
        if response_status[0] == 0:
            return httpx.Response(200, text="invalid JSON", headers={"content-type": "application/json"})
        if response_status[0] != 200:
            return httpx.Response(response_status[0], json={"error": {"message": "provider failed"}})
        if body.get("stream"):
            chunk = {
                "id": "chat-test",
                "object": "chat.completion.chunk",
                "created": 0,
                "model": "test-model",
                "choices": [{"index": 0, "delta": {"content": "ok"}, "finish_reason": "stop"}],
            }
            return httpx.Response(
                200,
                text=f"data: {json.dumps(chunk)}\n\ndata: [DONE]\n\n",
                headers={"content-type": "text/event-stream"},
            )
        return httpx.Response(
            200,
            json={
                "id": "chat-test",
                "object": "chat.completion",
                "created": 0,
                "model": "test-model",
                "choices": [{"index": 0, "message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}],
            },
        )

    def init_with_transport(client, *args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        kwargs.pop("proxy", None)
        original_init(client, *args, **kwargs)
        clients.append(client)

    monkeypatch.setattr(httpx.AsyncClient, "__init__", init_with_transport)
    client = OpenAIModelClient(
        ModelRequestConfig(model="test-model"),
        ModelClientConfig(
            client_provider="OpenAI",
            api_key="mock-api-key",
            api_base="https://example.invalid/v1",
            verify_ssl=False,
            use_shared_llm_http_client=False,
        ),
    )
    yield client, requests, response_status
    assert all(client.is_closed for client in clients)


async def call_model(client, messages, stream, **kwargs):
    if stream:
        return "".join([chunk.content async for chunk in client.stream(messages, **kwargs)])
    return (await client.invoke(messages, **kwargs)).content


@pytest.mark.asyncio
@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("message_kind", ["str", "dict", "tool", "multimodal"])
async def test_sanitizes_surrogates_before_http_without_mutating_history(
    request_client, monkeypatch, stream, message_kind
):
    client, requests, _ = request_client
    damaged = "风险 🔴 e\u0301 " + json.loads('"\\ud83d"') + "\udfff"
    expected = "风险 🔴 e\u0301 \ufffd\ufffd"
    if message_kind == "str":
        messages = damaged
    elif message_kind == "tool":
        messages = [ToolMessage(content=damaged, tool_call_id="call-test")]
    elif message_kind == "multimodal":
        messages = [{"role": "user", "content": [{"type": "text", "text": damaged}]}]
    else:
        messages = [{"role": "tool", "tool_call_id": "call-test", "content": damaged}]
    original = deepcopy(messages)
    warning = Mock()
    monkeypatch.setattr("openjiuwen.core.common.logging.llm_logger.warning", warning)

    # A checkpoint can replay the same damaged history on a later turn.
    for _ in range(2):
        assert await call_model(client, messages, stream) == "ok"
        assert messages == original
    for body in requests:
        message = body["messages"][0]
        content = message["content"]
        assert (content[0]["text"] if isinstance(content, list) else content) == expected
    assert warning.call_count == 2
    rendered = warning.call_args.args[0] % warning.call_args.args[1:]
    assert "role=" in rendered
    if message_kind in {"dict", "tool"}:
        assert "call-test" in rendered
    assert "风险" not in rendered


@pytest.mark.asyncio
@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("bad_value", [object(), float("nan"), "\ud83d"])
async def test_local_serialization_error_is_parameter_error(request_client, stream, bad_value):
    client, requests, _ = request_client
    with pytest.raises(BaseError) as caught:
        await call_model(client, "hello", stream, extra_body={"provider_option": bad_value})
    assert caught.value.status == StatusCode.MODEL_INVOKE_PARAM_ERROR
    assert isinstance(caught.value.__cause__, (TypeError, ValueError, UnicodeEncodeError))
    assert requests == []


@pytest.mark.asyncio
@pytest.mark.parametrize("stream", [False, True])
async def test_provider_error_remains_model_call_error(request_client, stream):
    client, requests, response_status = request_client
    response_status[0] = 500
    with pytest.raises(BaseError) as caught:
        await call_model(client, "hello", stream)
    assert caught.value.status == StatusCode.MODEL_CALL_FAILED
    assert len(requests) == 1


@pytest.mark.asyncio
async def test_valid_unicode_is_preserved_without_warning(request_client, monkeypatch):
    client, requests, _ = request_client
    warning = Mock()
    monkeypatch.setattr("openjiuwen.core.common.logging.llm_logger.warning", warning)
    text = "中文 🔴 👨‍👩‍👧‍👦 e\u0301"
    assert await call_model(client, text, False) == "ok"
    assert requests[0]["messages"][0]["content"] == text
    warning.assert_not_called()


@pytest.mark.asyncio
async def test_response_decoding_error_is_not_a_request_parameter_error(request_client):
    client, requests, response_status = request_client
    response_status[0] = 0
    with pytest.raises(BaseError) as caught:
        await call_model(client, "hello", False)
    assert caught.value.status == StatusCode.MODEL_CALL_FAILED
    assert isinstance(caught.value.__cause__, json.JSONDecodeError)
    assert len(requests) == 1


@pytest.mark.asyncio
async def test_nested_tool_call_values_are_repaired(request_client):
    client, requests, _ = request_client
    messages = [
        {
            "role": "assistant",
            "content": None,
            "reasoning_content": "reason \ud83d",
            "tool_calls": [
                {
                    "id": "call-test",
                    "type": "function",
                    "function": {"name": "lookup", "arguments": '{"text":"\udfff"}'},
                }
            ],
        }
    ]
    original = deepcopy(messages)
    assert await call_model(client, messages, False) == "ok"
    sent = requests[0]["messages"][0]
    assert sent["reasoning_content"] == "reason \ufffd"
    assert sent["tool_calls"][0]["function"]["arguments"] == '{"text":"\ufffd"}'
    assert sent["content"] is None
    assert messages == original
