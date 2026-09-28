# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import json
from unittest.mock import AsyncMock, patch

import pytest

from openjiuwen.core.common.exception.errors import BaseError
from openjiuwen.core.foundation.llm.model_clients.openai_model_client import (
    OpenAIModelClient,
    _chat_completions_url,
    _format_exception_detail,
    _normalize_openai_base_url,
    _parse_gateway_stream_line,
    _should_omit_authorization,
)
from openjiuwen.core.foundation.llm.schema.config import (
    LLMAuthMode,
    ModelClientConfig,
    ModelRequestConfig,
    ReasoningConfig,
)


class _Obj:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


class _HTTPError(Exception):
    def __init__(self, message: str, *, status_code: int):
        super().__init__(message)
        self.status_code = status_code


def _affinity_client(
    api_base: str = "https://example.test",
    *,
    model: str = "qwen",
    reasoning: ReasoningConfig | None = None,
) -> OpenAIModelClient:
    return OpenAIModelClient(
        ModelRequestConfig(model=model, reasoning=reasoning),
        ModelClientConfig(
            client_provider="OpenAI",
            api_base=api_base,
            auth_mode=LLMAuthMode.CustomHeaders,
            extensions={"kv_cache": {"mode": "affinity"}},
            verify_ssl=False,
        ),
    )


@pytest.mark.parametrize(
    ("api_base", "expected"),
    [
        ("http://host:8000", "http://host:8000"),
        ("http://host:8000/v1", "http://host:8000/v1"),
        ("http://host:8000/v1/", "http://host:8000/v1"),
        ("http://host:8000/v1/chat/completions", "http://host:8000/v1"),
        ("http://host:8000/chat/completions", "http://host:8000"),
        ("https://gw.internal/llm", "https://gw.internal/llm"),
        ("https://host/v1/infers/xxx", "https://host/v1/infers/xxx"),
        ("https://host/v2", "https://host/v2"),
    ],
)
def test_normalize_openai_base_url(api_base, expected):
    assert _normalize_openai_base_url(api_base) == expected
    assert _chat_completions_url(api_base) == f"{expected}/chat/completions"


def test_custom_headers_without_key_omits_authorization():
    config = ModelClientConfig(
        client_provider="OpenAI",
        api_base="https://example.test",
        auth_mode=LLMAuthMode.CustomHeaders,
        verify_ssl=False,
    )
    assert _should_omit_authorization(config) is True
    assert OpenAIModelClient(ModelRequestConfig(model="qwen"), config)._resolved_api_key() == "EMPTY"


def test_connection_key_encodes_omit_in_api_key_slot():
    omit_cfg = ModelClientConfig(
        client_provider="OpenAI",
        api_base="https://example.test/v1",
        auth_mode=LLMAuthMode.CustomHeaders,
        verify_ssl=False,
    )
    none_auth_cfg = ModelClientConfig(
        client_provider="OpenAI",
        api_base="https://example.test/v1",
        auth_mode=LLMAuthMode.NoneAuth,
        verify_ssl=False,
    )
    literal_empty_cfg = ModelClientConfig(
        client_provider="OpenAI",
        api_base="https://example.test/v1",
        api_key="EMPTY",
        auth_mode=LLMAuthMode.CustomHeaders,
        verify_ssl=False,
    )
    omit_key = OpenAIModelClient.connection_key(omit_cfg)
    assert omit_key == OpenAIModelClient.connection_key(none_auth_cfg)
    assert omit_key != OpenAIModelClient.connection_key(literal_empty_cfg)
    assert omit_key[0] is None
    assert len(omit_key) == 4


def test_none_auth_omits_authorization():
    config = ModelClientConfig(
        client_provider="OpenAI",
        endpoint_profile="ollama",
        api_base="http://localhost:11434/v1",
        auth_mode=LLMAuthMode.NoneAuth,
        verify_ssl=False,
    )
    assert _should_omit_authorization(config) is True


def test_affinity_headers_omit_authorization_when_key_empty():
    headers = _affinity_client()._affinity_http_headers(stream=True)
    assert "Authorization" not in headers
    assert headers["Accept"] == "text/event-stream"


def test_format_exception_detail_includes_type_when_message_empty():
    assert _format_exception_detail(TimeoutError()) == "TimeoutError"
    assert _format_exception_detail(TimeoutError("late")) == "TimeoutError: late"


def test_session_manage_request_omits_max_tokens():
    client = _affinity_client()
    params = client._build_request_params(
        messages=[{"role": "user", "content": "hello"}],
        tools=None,
        temperature=None,
        top_p=None,
        model=None,
        stop=None,
        max_tokens=512,
        stream=False,
        session_id="sess",
        parent_session_id="parent",
        kv_action="evict",
        target="session",
        manage_request=True,
    )
    assert "max_tokens" not in params
    assert params["messages"] == []


def test_normal_affinity_request_keeps_explicit_max_tokens():
    client = _affinity_client()
    params = client._build_request_params(
        messages=[{"role": "user", "content": "hello"}],
        tools=None,
        temperature=None,
        top_p=None,
        model=None,
        stop=None,
        max_tokens=512,
        stream=True,
        session_id="sess",
        parent_session_id="parent",
    )
    assert params["max_tokens"] == 512


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["evict_kvc", "offload_kvc", "prefetch_kvc"])
async def test_session_affinity_action_builds_one_messages_argument(action):
    client = _affinity_client()
    sdk_client = AsyncMock()
    sdk_client.chat.completions.create = AsyncMock(return_value=_Obj())

    with patch.object(client, "_create_async_openai_client", return_value=sdk_client):
        result = await getattr(client, action)(
            session_id="child",
            parent_session_id="parent",
            messages=None,
            tools=None,
        )

    assert result is True
    sent = sdk_client.chat.completions.create.call_args.kwargs
    assert sent["messages"] == []
    assert sent["extra_body"]["agent_hint"] == {
        "session_id": "child",
        "parent_session_id": "parent",
        "context_management": {
            "edits": [{"type": action.removesuffix("_kvc"), "target": "session"}],
            "manage_request": True,
        },
    }


@pytest.mark.asyncio
async def test_affinity_action_retries_once_without_resolved_disabled_reasoning():
    client = _affinity_client(
        model="GLM-5.3",
        reasoning=ReasoningConfig(mode="disabled"),
    )
    sdk_client = AsyncMock()
    sdk_client.chat.completions.create = AsyncMock(
        side_effect=[
            _HTTPError(
                "reasoning model does not support thinking.type=disabled",
                status_code=400,
            ),
            _Obj(),
        ],
    )

    with patch.object(client, "_create_async_openai_client", return_value=sdk_client):
        result = await client.evict_kvc(
            session_id="child",
            parent_session_id="parent",
            timeout=12.0,
            max_attempts=3,
        )

    assert result is True
    assert sdk_client.chat.completions.create.call_count == 2
    first_call, fallback_call = sdk_client.chat.completions.create.call_args_list
    assert first_call.kwargs["extra_body"]["thinking"] == {"type": "disabled"}
    assert "thinking" not in fallback_call.kwargs["extra_body"]
    assert first_call.kwargs["extra_body"]["agent_hint"] == fallback_call.kwargs["extra_body"]["agent_hint"]
    assert first_call.kwargs["timeout"] == fallback_call.kwargs["timeout"] == 12.0


@pytest.mark.asyncio
async def test_affinity_action_failed_semantic_fallback_does_not_enter_action_retries():
    client = _affinity_client(
        model="GLM-5.3",
        reasoning=ReasoningConfig(mode="disabled"),
    )
    sdk_client = AsyncMock()
    sdk_client.chat.completions.create = AsyncMock(
        side_effect=[
            _HTTPError(
                "该模型始终思考，不支持关闭思考",
                status_code=400,
            ),
            RuntimeError("fallback failed"),
            _Obj(),
        ],
    )

    with patch.object(client, "_create_async_openai_client", return_value=sdk_client):
        with pytest.raises(BaseError, match="fallback failed"):
            await client.evict_kvc(
                session_id="child",
                parent_session_id="parent",
                max_attempts=3,
            )

    assert sdk_client.chat.completions.create.call_count == 2


def test_gateway_parser_accepts_token_text_and_reasoning():
    line = json.dumps({
        "choices": [{
            "message": {
                "token_text": "answer-token",
                "reasoning_token_text": "thinking-token",
            },
            "finish_reason": None,
        }]
    })
    chunk = _parse_gateway_stream_line(line)
    assert chunk is not None
    assert chunk.content == "answer-token"
    assert chunk.reasoning_content == "thinking-token"


def test_gateway_parser_accepts_data_without_space():
    payload = json.dumps({
        "choices": [{"delta": {}, "finish_reason": "length"}]
    })
    chunk = _parse_gateway_stream_line(f"data:{payload}")
    assert chunk is not None
    assert chunk.finish_reason == "length"


def test_gateway_parser_preserves_streamed_tool_calls():
    payload = json.dumps({
        "choices": [{
            "delta": {
                "tool_calls": [{
                    "index": 0,
                    "id": "call-1",
                    "type": "function",
                    "function": {
                        "name": "write_file",
                        "arguments": '{"file_path":"test.py"}',
                    },
                }],
            },
            "finish_reason": "tool_calls",
        }]
    })

    chunk = _parse_gateway_stream_line(f"data: {payload}")

    assert chunk is not None
    assert chunk.finish_reason == "tool_calls"
    assert chunk.tool_calls is not None
    assert chunk.tool_calls[0].id == "call-1"
    assert chunk.tool_calls[0].name == "write_file"
    assert chunk.tool_calls[0].arguments == '{"file_path":"test.py"}'


def test_gateway_parser_surfaces_nested_upstream_error():
    line = "data: " + json.dumps({
        "message": json.dumps({
            "error": {"message": "This model's maximum context length is 16384 tokens."}
        })
    })
    with pytest.raises(ValueError, match="maximum context length is 16384"):
        _parse_gateway_stream_line(line)


def test_gateway_parser_accepts_plain_json_chat_completion():
    line = json.dumps({
        "choices": [{
            "message": {"role": "assistant", "content": "plain-json-answer"},
            "finish_reason": "stop",
        }]
    })
    chunk = _parse_gateway_stream_line(line)
    assert chunk is not None
    assert chunk.content == "plain-json-answer"
    assert chunk.finish_reason == "stop"


def test_parse_stream_chunk_keeps_usage_only_chunk():
    client = OpenAIModelClient(
        ModelRequestConfig(model="qwen"),
        ModelClientConfig(
            client_provider="OpenAI",
            api_key="sk-test",
            api_base="https://api.openai.com/v1",
            verify_ssl=False,
        ),
    )
    chunk = _Obj(
        usage=_Obj(prompt_tokens=1, completion_tokens=0, total_tokens=1),
        prompt_token_ids=None,
        choices=[],
    )
    parsed = client._parse_stream_chunk(chunk)
    assert parsed is not None
    assert parsed.content == ""
    assert parsed.usage_metadata is not None
    assert parsed.usage_metadata.total_tokens == 1


def test_parse_stream_chunk_reads_token_text():
    client = _affinity_client()
    chunk = _Obj(
        usage=None,
        prompt_token_ids=None,
        choices=[
            _Obj(
                delta=_Obj(content=""),
                message=_Obj(token_text="tok", reasoning_token_text="think"),
                finish_reason=None,
                token_ids=None,
                logprobs=None,
            )
        ],
    )
    parsed = client._parse_stream_chunk(chunk)
    assert parsed is not None
    assert parsed.content == "tok"
    assert parsed.reasoning_content == "think"


def _mock_http_client(response):
    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        def stream(self, *_args, **_kwargs):
            return response

    return lambda **_kwargs: _Client()


def _mock_http_client_sequence(responses, seen_bodies):
    remaining = list(responses)

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        def stream(self, *_args, **kwargs):
            seen_bodies.append(kwargs["json"])
            return remaining.pop(0)

    return lambda **_kwargs: _Client()


class _FakeResponse:
    def __init__(self, *, status_code=200, headers=None, body=b"", lines=None):
        self.status_code = status_code
        self.headers = headers or {}
        self._body = body
        self._lines = lines

    async def aread(self):
        return self._body

    async def aiter_lines(self):
        for line in self._lines or []:
            yield line

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None


@pytest.mark.asyncio
async def test_affinity_stream_parses_sse_when_content_type_wrong(monkeypatch):
    client = _affinity_client()
    payload = json.dumps({
        "choices": [{"delta": {"content": "hello"}, "finish_reason": None}]
    })
    body = f"data: {payload}\n\ndata: [DONE]\n".encode("utf-8")
    monkeypatch.setattr(
        "openjiuwen.core.foundation.llm.model_clients.openai_model_client.httpx.AsyncClient",
        _mock_http_client(_FakeResponse(
            headers={"Content-Type": "application/octet-stream"},
            body=body,
        )),
    )
    chunks = [
        chunk
        async for chunk in client._iter_affinity_gateway_stream({"model": "qwen"})
    ]
    assert [chunk.content for chunk in chunks] == ["hello"]


@pytest.mark.asyncio
async def test_affinity_stream_rejects_usage_only_response(monkeypatch):
    client = _affinity_client()
    body = json.dumps({
        "choices": [],
        "usage": {"prompt_tokens": 1, "completion_tokens": 0, "total_tokens": 1},
    })

    monkeypatch.setattr(
        "openjiuwen.core.foundation.llm.model_clients.openai_model_client.httpx.AsyncClient",
        _mock_http_client(_FakeResponse(
            headers={"Content-Type": "application/json"},
            body=body.encode("utf-8"),
        )),
    )
    with pytest.raises(ValueError, match="raw_samples="):
        _ = [
            chunk
            async for chunk in client._iter_affinity_gateway_stream({"model": "qwen"})
        ]


@pytest.mark.asyncio
async def test_affinity_stream_retries_once_without_resolved_disabled_reasoning(monkeypatch):
    client = _affinity_client(
        model="GLM-5.3",
        reasoning=ReasoningConfig(mode="disabled"),
    )
    seen_bodies = []
    success_body = json.dumps({
        "choices": [{"message": {"content": "fallback"}, "finish_reason": "stop"}],
    }).encode()
    monkeypatch.setattr(client, "_create_async_openai_client", lambda timeout=None: object())
    monkeypatch.setattr(
        "openjiuwen.core.foundation.llm.model_clients.openai_model_client.httpx.AsyncClient",
        _mock_http_client_sequence(
            [
                _FakeResponse(
                    status_code=400,
                    body="该模型始终思考，不支持关闭思考".encode(),
                ),
                _FakeResponse(
                    headers={"Content-Type": "application/json"},
                    body=success_body,
                ),
            ],
            seen_bodies,
        ),
    )

    chunks = [chunk async for chunk in client.stream("hello")]

    assert [chunk.content for chunk in chunks] == ["fallback"]
    assert len(seen_bodies) == 2
    assert seen_bodies[0]["thinking"] == {"type": "disabled"}
    assert "thinking" not in seen_bodies[1]


@pytest.mark.asyncio
async def test_affinity_stream_second_failure_is_not_retried_again(monkeypatch):
    client = _affinity_client(
        model="GLM-5.3",
        reasoning=ReasoningConfig(mode="disabled"),
    )
    seen_bodies = []
    monkeypatch.setattr(client, "_create_async_openai_client", lambda timeout=None: object())
    monkeypatch.setattr(
        "openjiuwen.core.foundation.llm.model_clients.openai_model_client.httpx.AsyncClient",
        _mock_http_client_sequence(
            [
                _FakeResponse(
                    status_code=400,
                    body=b"reasoning model does not support thinking.type=disabled",
                ),
                _FakeResponse(status_code=400, body=b"fallback failed"),
            ],
            seen_bodies,
        ),
    )

    with pytest.raises(BaseError, match="fallback failed"):
        _ = [chunk async for chunk in client.stream("hello")]

    assert len(seen_bodies) == 2


@pytest.mark.asyncio
async def test_affinity_stream_does_not_remove_caller_raw_disabled_reasoning(monkeypatch):
    client = _affinity_client(model="GLM-5.3")
    seen_bodies = []
    monkeypatch.setattr(client, "_create_async_openai_client", lambda timeout=None: object())
    monkeypatch.setattr(
        "openjiuwen.core.foundation.llm.model_clients.openai_model_client.httpx.AsyncClient",
        _mock_http_client_sequence(
            [
                _FakeResponse(
                    status_code=400,
                    body="该模型始终思考，不支持关闭思考".encode(),
                ),
            ],
            seen_bodies,
        ),
    )

    with pytest.raises(BaseError):
        _ = [
            chunk
            async for chunk in client.stream(
                "hello",
                extra_body={"thinking": {"type": "disabled"}},
            )
        ]

    assert len(seen_bodies) == 1
    assert seen_bodies[0]["thinking"] == {"type": "disabled"}
