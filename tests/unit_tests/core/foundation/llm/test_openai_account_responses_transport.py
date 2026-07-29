# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

import json

import httpx
import pytest

from openjiuwen.extensions.external_provider.openai_auth.openai_account_auth import DEFAULT_OPENAI_ACCOUNT_BASE_URL
from openjiuwen.core.foundation.llm.utils.responses_utils import (
    OpenAIAccountResponsesError,
    build_request_body,
    iter_sse_events,
    parse_response,
    parse_stream_event,
)
from openjiuwen.core.foundation.llm.utils.responses_transport import OpenAIAccountResponsesTransport
from openjiuwen.core.foundation.llm.schema.message import AssistantMessage, SystemMessage, ToolMessage, UserMessage
from openjiuwen.core.foundation.llm.schema.tool_call import ToolCall
from openjiuwen.core.foundation.tool.schema import ToolInfo


def test_build_request_body_converts_messages_and_tools():
    messages = [
        SystemMessage(content="You are concise."),
        UserMessage(content="Use the tool."),
        AssistantMessage(
            content="",
            tool_calls=[
                ToolCall(
                    id="call_1",
                    type="function",
                    name="search",
                    arguments='{"query":"OpenAI account"}',
                )
            ],
        ),
        ToolMessage(content="result text", tool_call_id="call_1"),
    ]
    tools = [
        ToolInfo(
            name="search",
            description="Search documents.",
            parameters={"type": "object", "properties": {"query": {"type": "string"}}},
        )
    ]

    body = build_request_body(
        model="gpt-5.4-mini",
        messages=messages,
        tools=tools,
        reasoning={"effort": "medium", "summary": "auto"},
    )

    assert body["model"] == "gpt-5.4-mini"
    assert body["instructions"] == "You are concise."
    assert body["input"][0] == {
        "role": "user",
        "content": [{"type": "input_text", "text": "Use the tool."}],
    }
    assert body["input"][1]["type"] == "function_call"
    assert body["input"][1]["call_id"] == "call_1"
    assert body["input"][2] == {
        "type": "function_call_output",
        "call_id": "call_1",
        "output": "result text",
    }
    assert body["tools"][0]["name"] == "search"
    assert body["tool_choice"] == "auto"
    assert body["parallel_tool_calls"] is True
    assert body["include"] == ["reasoning.encrypted_content"]


def test_build_request_body_accepts_openai_style_tool_dict():
    body = build_request_body(
        model="gpt-5.4-mini",
        messages="hello",
        tools=[
            {
                "type": "function",
                "function": {
                    "name": "lookup",
                    "description": "Lookup things.",
                    "parameters": {"type": "object"},
                },
            }
        ],
    )

    assert body["tools"] == [
        {
            "type": "function",
            "name": "lookup",
            "description": "Lookup things.",
            "parameters": {"type": "object"},
        }
    ]


def test_build_request_body_uses_global_tool_call_fallback_ids():
    body = build_request_body(
        model="gpt-5.4-mini",
        messages=[
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {"type": "function", "function": {"name": "first", "arguments": "{}"}},
                ],
            },
            {"role": "tool", "tool_call_id": "call_0", "content": "first result"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {"type": "function", "function": {"name": "second", "arguments": "{}"}},
                ],
            },
            {"role": "tool", "tool_call_id": "call_1", "content": "second result"},
        ],
    )

    assert body["input"][0]["call_id"] == "call_0"
    assert body["input"][1]["call_id"] == "call_0"
    assert body["input"][2]["call_id"] == "call_1"
    assert body["input"][3]["call_id"] == "call_1"


def test_parse_response_extracts_message_tool_calls_reasoning_and_usage():
    payload = {
        "status": "completed",
        "output": [
            {"type": "reasoning", "summary": [{"type": "summary_text", "text": "thinking"}]},
            {
                "type": "message",
                "content": [{"type": "output_text", "text": "hello"}],
            },
            {
                "type": "function_call",
                "id": "fc_1",
                "call_id": "call_1",
                "name": "search",
                "arguments": {"query": "OpenAI account"},
            },
        ],
        "usage": {
            "input_tokens": 10,
            "output_tokens": 5,
            "total_tokens": 15,
            "input_tokens_details": {"cached_tokens": 3},
        },
    }

    message = parse_response(payload, model_name="gpt-5.4-mini")

    assert message.content == "hello"
    assert message.reasoning_content == "thinking"
    assert message.finish_reason == "tool_calls"
    assert message.tool_calls[0].id == "call_1"
    assert message.tool_calls[0].response_item_id == "fc_1"
    assert message.tool_calls[0].name == "search"
    assert message.tool_calls[0].arguments == '{"query": "OpenAI account"}'
    assert message.usage_metadata.input_tokens == 10
    assert message.usage_metadata.cache_tokens == 3


def test_parse_response_uses_output_text_fallback_and_incomplete_length():
    message = parse_response(
        {"status": "incomplete", "output_text": "partial", "usage": {"input_tokens": 1, "output_tokens": 2}},
        model_name="gpt-5.4-mini",
    )

    assert message.content == "partial"
    assert message.finish_reason == "length"
    assert message.usage_metadata.total_tokens == 3


def test_parse_response_preserves_explicit_zero_total_tokens():
    message = parse_response(
        {"output_text": "ok", "usage": {"input_tokens": 2, "output_tokens": 3, "total_tokens": 0}},
        model_name="gpt-5.4-mini",
    )

    assert message.usage_metadata.input_tokens == 2
    assert message.usage_metadata.output_tokens == 3
    assert message.usage_metadata.total_tokens == 0


def test_iter_sse_events_and_parse_stream_event():
    lines = [
        "event: response.output_text.delta",
        'data: {"delta":"Hel"}',
        "",
        "event: response.output_item.done",
        'data: {"item":{"type":"function_call","call_id":"call_1","name":"search","arguments":"{}"}}',
        "",
        "event: response.completed",
        'data: {"response":{"usage":{"input_tokens":2,"output_tokens":3,"total_tokens":5}}}',
        "",
    ]

    events = list(iter_sse_events(lines))
    chunks = [parse_stream_event(event, model_name="gpt-5.4-mini") for event in events]

    assert chunks[0].content == "Hel"
    assert chunks[0].finish_reason == "null"
    assert chunks[1].tool_calls[0].id == "call_1"
    assert chunks[1].finish_reason == "tool_calls"
    assert chunks[2].usage_metadata.total_tokens == 5
    assert chunks[2].finish_reason == "stop"


def test_parse_stream_event_raises_on_error_event():
    with pytest.raises(OpenAIAccountResponsesError, match="bad request"):
        parse_stream_event({"type": "error", "error": {"message": "bad request"}})


@pytest.mark.asyncio
async def test_create_response_posts_to_openai_account_backend():
    async def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == f"{DEFAULT_OPENAI_ACCOUNT_BASE_URL}/responses"
        assert request.headers["Authorization"] == "Bearer access-token"
        assert request.headers["session_id"] == "session-1"
        body = json.loads(request.content.decode())
        assert body["model"] == "gpt-5.4-mini"
        assert body["stream"] is True
        return httpx.Response(
            200,
            content=(
                "event: response.output_text.delta\n"
                'data: {"delta":"o"}\n\n'
                "event: response.output_text.delta\n"
                'data: {"delta":"k"}\n\n'
                "event: response.completed\n"
                'data: {"response":{"usage":{"input_tokens":2,"output_tokens":1,"total_tokens":3}}}\n\n'
            ),
            headers={"content-type": "text/event-stream"},
        )

    transport = OpenAIAccountResponsesTransport(transport=httpx.MockTransport(handler))
    body = build_request_body(model="gpt-5.4-mini", messages="hello")

    message = await transport.create_response(
        body=body,
        access_token="access-token",
        session_id="session-1",
    )

    assert message.content == "ok"
    assert message.usage_metadata.total_tokens == 3


@pytest.mark.asyncio
async def test_create_response_preserves_tool_call_finish_reason_from_stream():
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=(
                "event: response.output_item.done\n"
                'data: {"item":{"type":"function_call","call_id":"call_1","name":"search","arguments":"{}"}}\n\n'
                "event: response.completed\n"
                'data: {"response":{"usage":{"input_tokens":2,"output_tokens":3,"total_tokens":5}}}\n\n'
            ),
            headers={"content-type": "text/event-stream"},
        )

    transport = OpenAIAccountResponsesTransport(transport=httpx.MockTransport(handler))
    body = build_request_body(model="gpt-5.4-mini", messages="hello")

    message = await transport.create_response(body=body, access_token="access-token")

    assert message.tool_calls[0].name == "search"
    assert message.finish_reason == "tool_calls"


@pytest.mark.asyncio
async def test_transport_reuses_async_client_between_requests():
    requests = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            content=(
                "event: response.output_text.delta\n"
                'data: {"delta":"ok"}\n\n'
                "event: response.completed\n"
                'data: {"response":{"usage":{"input_tokens":1,"output_tokens":1,"total_tokens":2}}}\n\n'
            ),
            headers={"content-type": "text/event-stream"},
        )

    class CountingTransport(OpenAIAccountResponsesTransport):
        def __init__(self):
            super().__init__(transport=httpx.MockTransport(handler))
            self.created_clients = 0

        def _make_client(self) -> httpx.AsyncClient:
            self.created_clients += 1
            return super()._make_client()

    transport = CountingTransport()
    body = build_request_body(model="gpt-5.4-mini", messages="hello")
    try:
        await transport.create_response(body=body, access_token="access-token")
        await transport.create_response(body=body, access_token="access-token")
    finally:
        await transport.aclose()

    assert len(requests) == 2
    assert transport.created_clients == 1


@pytest.mark.asyncio
async def test_transport_closes_closed_cached_client_before_replacing():
    class ClosedClient:
        is_closed = True

        def __init__(self):
            self.close_calls = 0

        async def aclose(self):
            self.close_calls += 1

    class ReplacementClient:
        is_closed = False

    class ReplacingTransport(OpenAIAccountResponsesTransport):
        def __init__(self):
            super().__init__()
            self.replacement_client = ReplacementClient()

        def _make_client(self):
            return self.replacement_client

    transport = ReplacingTransport()
    closed_client = ClosedClient()
    transport._client = closed_client

    client = await transport._get_client()

    assert client is transport.replacement_client
    assert closed_client.close_calls == 1


@pytest.mark.asyncio
async def test_transport_logs_closed_client_close_error_before_replacing(monkeypatch):
    warnings = []

    class ClosedClient:
        is_closed = True

        async def aclose(self):
            raise RuntimeError("close failed")

    class ReplacementClient:
        is_closed = False

    class ReplacingTransport(OpenAIAccountResponsesTransport):
        def __init__(self):
            super().__init__()
            self.replacement_client = ReplacementClient()

        def _make_client(self):
            return self.replacement_client

    def fake_warning(message, *args, **kwargs):
        warnings.append((message, args, kwargs))

    monkeypatch.setattr(
        "openjiuwen.core.foundation.llm.utils.responses_transport.logger.warning",
        fake_warning,
    )
    transport = ReplacingTransport()
    transport._client = ClosedClient()

    client = await transport._get_client()

    assert client is transport.replacement_client
    assert warnings
    assert "Failed to close stale OpenAI account Responses client" in warnings[0][0]
    assert isinstance(warnings[0][1][0], RuntimeError)
