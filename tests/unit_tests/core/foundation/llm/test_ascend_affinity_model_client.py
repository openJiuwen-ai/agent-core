# coding: utf-8

import asyncio
import inspect
import json
from unittest.mock import AsyncMock

import pytest

from openjiuwen.core.foundation.llm import Model
from openjiuwen.core.foundation.llm.model_clients import create_model_client
from openjiuwen.core.foundation.llm.model_clients.ascend_affinity_model_client import (
    AscendAffinityModelClient,
)
from openjiuwen.core.foundation.llm.schema.config import (
    ModelClientConfig,
    ModelRequestConfig,
    ProviderType,
)
from openjiuwen.core.foundation.kv_cache import (
    KVC_MANAGEMENT_MAX_ATTEMPTS,
    KVC_SESSION_OFFLOAD_PREFETCH_TIMEOUT_SECONDS,
    KV_CACHE_EPHEMERAL_TAIL_METADATA,
)
from openjiuwen.core.foundation.llm import UserMessage


def _client() -> AscendAffinityModelClient:
    return AscendAffinityModelClient(
        model_config=ModelRequestConfig(model="test-model"),
        model_client_config=ModelClientConfig(
            client_provider=ProviderType.AscendAffinity,
            api_key="test-key",
            api_base="https://example.test",
            verify_ssl=False,
        ),
    )


def test_factory_creates_ascend_affinity_client():
    client = create_model_client(
        client_config=ModelClientConfig(
            client_provider="AscendAffinity",
            api_key="test-key",
            api_base="https://example.test",
            verify_ssl=False,
        ),
        model_config=ModelRequestConfig(model="test-model"),
    )

    assert isinstance(client, AscendAffinityModelClient)
    assert client.supports_kv_cache_affinity() is True


def test_request_without_affinity_hint_does_not_serialize_internal_attachment_metadata():
    params = _client()._build_request_params(
        messages=[
            UserMessage(content="hello"),
            UserMessage(
                content="<system-reminder>attachment</system-reminder>",
                metadata={KV_CACHE_EPHEMERAL_TAIL_METADATA: True},
            ),
        ],
        tools=None,
        temperature=None,
        top_p=None,
        model=None,
        stop=None,
        max_tokens=None,
        stream=True,
    )

    assert "agent_hint" not in params
    assert params["messages"] == [
        {"role": "user", "content": "hello"},
        {
            "role": "user",
            "content": "<system-reminder>attachment</system-reminder>",
        },
    ]


def _sse(payload: dict, *, space_after_colon: bool = True) -> str:
    separator = " " if space_after_colon else ""
    return f"data:{separator}{json.dumps(payload)}"


class _FakeResponse:
    def __init__(self, body: str, content_type: str = "application/json") -> None:
        self.status = 200
        self.headers = {"Content-Type": content_type}
        self._body = body
        self.content = _FakeContent(body)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def text(self):
        return self._body


class _FakeSession:
    def __init__(self, response: _FakeResponse) -> None:
        self.response = response

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    def post(self, *_args, **_kwargs):
        return self.response


class _FakeContent:
    def __init__(self, body: str) -> None:
        self._lines = iter(body.splitlines(keepends=True))

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            return next(self._lines).encode("utf-8")
        except StopIteration as exc:
            raise StopAsyncIteration from exc


def test_stream_parser_accepts_standard_delta_content_and_reasoning():
    chunk = _client()._parse_stream_chunk(
        _sse(
            {
                "choices": [
                    {
                        "delta": {
                            "content": "answer",
                            "reasoning_content": "thinking",
                        },
                        "finish_reason": None,
                    }
                ]
            }
        )
    )

    assert chunk is not None
    assert chunk.content == "answer"
    assert chunk.reasoning_content == "thinking"


def test_stream_parser_accepts_gateway_message_token_fields():
    chunk = _client()._parse_stream_chunk(
        _sse(
            {
                "choices": [
                    {
                        "message": {
                            "token_text": "answer-token",
                            "reasoning_token_text": "thinking-token",
                        },
                        "finish_reason": None,
                    }
                ]
            }
        )
    )

    assert chunk is not None
    assert chunk.content == "answer-token"
    assert chunk.reasoning_content == "thinking-token"


def test_stream_parser_accepts_data_without_space_and_finish_only_chunk():
    chunk = _client()._parse_stream_chunk(
        _sse(
            {
                "choices": [
                    {
                        "delta": {},
                        "finish_reason": "length",
                    }
                ]
            },
            space_after_colon=False,
        )
    )

    assert chunk is not None
    assert chunk.content == ""
    assert chunk.finish_reason == "length"


def test_stream_parser_accepts_plain_json_chat_completion_fallback():
    line = json.dumps(
        {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": "plain-json-answer",
                    },
                    "finish_reason": "stop",
                }
            ]
        }
    )

    chunk = _client()._parse_stream_chunk(line)

    assert chunk is not None
    assert chunk.content == "plain-json-answer"
    assert chunk.finish_reason == "stop"


@pytest.mark.asyncio
async def test_non_stream_parser_preserves_length_finish_reason():
    response = await _client()._parse_response(
        {
            "choices": [
                {
                    "message": {"role": "assistant", "content": "partial"},
                    "finish_reason": "length",
                }
            ]
        }
    )

    assert response.content == "partial"
    assert response.finish_reason == "length"


@pytest.mark.asyncio
async def test_stream_transport_accepts_plain_json_response():
    client = _client()
    body = json.dumps(
        {
            "choices": [
                {
                    "message": {"role": "assistant", "content": "fallback"},
                    "finish_reason": "stop",
                }
            ]
        }
    )
    client._create_session = lambda **_kwargs: _FakeSession(_FakeResponse(body))

    chunks = [chunk async for chunk in client._stream_response({"model": "test"})]

    assert len(chunks) == 1
    assert chunks[0].content == "fallback"


@pytest.mark.asyncio
async def test_stream_transport_accepts_real_vllm_sse_shape():
    client = _client()
    body = "\n\n".join(
        [
            _sse(
                {
                    "choices": [
                        {
                            "delta": {"role": "assistant", "content": ""},
                            "finish_reason": None,
                        }
                    ]
                }
            ),
            _sse(
                {
                    "choices": [
                        {
                            "delta": {"content": "<think>"},
                            "finish_reason": None,
                        }
                    ]
                }
            ),
            _sse(
                {
                    "choices": [
                        {
                            "delta": {"content": "hello"},
                            "finish_reason": None,
                        }
                    ]
                }
            ),
            _sse(
                {
                    "choices": [
                        {
                            "delta": {},
                            "finish_reason": "stop",
                        }
                    ]
                }
            ),
            "data: [DONE]",
        ]
    )
    client._create_session = lambda **_kwargs: _FakeSession(
        _FakeResponse(body, "text/event-stream; charset=utf-8")
    )

    chunks = [chunk async for chunk in client._stream_response({"model": "test"})]

    assert "".join(chunk.content or "" for chunk in chunks) == "<think>hello"
    assert chunks[-1].finish_reason == "stop"


@pytest.mark.asyncio
async def test_stream_transport_rejects_usage_only_response():
    client = _client()
    body = json.dumps(
        {
            "choices": [],
            "usage": {
                "prompt_tokens": 1,
                "completion_tokens": 0,
                "total_tokens": 1,
            },
        }
    )
    client._create_session = lambda **_kwargs: _FakeSession(_FakeResponse(body))

    with pytest.raises(ValueError, match="raw_samples=.*choices"):
        _ = [
            chunk
            async for chunk in client._stream_response({"model": "test"})
        ]


@pytest.mark.asyncio
async def test_stream_error_preserves_exception_type_when_message_is_empty():
    client = _client()

    async def broken_stream(*_args, **_kwargs):
        if False:
            yield None
        raise TimeoutError()

    client._stream_response = broken_stream

    with pytest.raises(Exception, match="TimeoutError"):
        _ = [
            chunk
            async for chunk in client.stream(
                [UserMessage(content="hello")]
            )
        ]


@pytest.mark.asyncio
async def test_invoke_error_preserves_exception_type_when_message_is_empty():
    client = _client()
    client._make_ascend_affinity_request = AsyncMock(
        side_effect=TimeoutError()
    )

    with pytest.raises(Exception, match="TimeoutError"):
        await client.invoke([UserMessage(content="hello")])


def test_normal_request_carries_agent_hint():
    params = _client()._build_request_params(
        messages=[{"role": "user", "content": "hello"}],
        tools=None,
        temperature=None,
        top_p=None,
        model=None,
        stop=None,
        max_tokens=None,
        stream=False,
        session_id="sess-a",
        parent_session_id="parent-a",
    )

    assert params["agent_hint"] == {
        "session_id": "sess-a",
        "parent_session_id": "parent-a",
    }


def test_normal_request_without_session_omits_agent_hint():
    params = _client()._build_request_params(
        messages=[{"role": "user", "content": "hello"}],
        tools=None,
        temperature=None,
        top_p=None,
        model=None,
        stop=None,
        max_tokens=None,
        stream=False,
    )

    assert "agent_hint" not in params
    assert "max_tokens" not in params


def test_normal_request_preserves_explicit_max_tokens():
    params = _client()._build_request_params(
        messages=[{"role": "user", "content": "hello"}],
        tools=None,
        temperature=None,
        top_p=None,
        model=None,
        stop=None,
        max_tokens=512,
        stream=True,
    )

    assert params["max_tokens"] == 512


def test_stream_parser_surfaces_nested_upstream_error_message():
    upstream_message = (
        "This model's maximum context length is 16384 tokens. "
        "However, the request requires 16385 tokens."
    )
    line = _sse(
        {
            "message": json.dumps(
                {"error": {"message": upstream_message}},
            )
        }
    )

    with pytest.raises(ValueError, match="maximum context length is 16384"):
        _client()._parse_stream_chunk(line)


def test_session_management_request_uses_empty_messages_and_context_management():
    client = _client()
    params = client._build_request_params(
        messages=[],
        tools=None,
        temperature=None,
        top_p=None,
        model=None,
        stop=None,
        max_tokens=None,
        stream=False,
        session_id="sess-a",
        parent_session_id="parent-a",
        kv_action="evict",
        target="session",
        manage_request=True,
    )

    assert params["messages"] == []
    assert "tools" not in params
    assert "max_tokens" not in params
    assert params["agent_hint"] == {
        "session_id": "sess-a",
        "parent_session_id": "parent-a",
        "context_management": {
            "manage_request": True,
            "edits": [{"type": "evict", "target": "session"}],
        },
    }


def test_normal_inference_can_evict_attachment_tail_after_inference():
    params = _client()._build_request_params(
        messages=[
            {"role": "user", "content": "hello"},
            {"role": "user", "content": "<system-reminder>attachment</system-reminder>"},
        ],
        tools=None,
        temperature=None,
        top_p=None,
        model=None,
        stop=None,
        max_tokens=None,
        stream=True,
        session_id="sess-a",
        parent_session_id="parent-a",
        kv_action="evict",
        target="messages",
        manage_request=False,
        msg_start=1,
        msg_end=2,
    )

    assert len(params["messages"]) == 2
    assert params["stream"] is True
    assert params["agent_hint"]["context_management"] == {
        "manage_request": False,
        "edits": [
            {"type": "evict", "target": "messages", "start": 1, "end": 2},
        ],
    }


def test_management_request_defaults_to_session_target():
    params = _client()._build_request_params(
        messages=[],
        tools=None,
        temperature=None,
        top_p=None,
        model=None,
        stop=None,
        max_tokens=None,
        stream=False,
        session_id="sess-a",
        kv_action="offload",
        manage_request=True,
    )

    assert params["messages"] == []
    assert params["agent_hint"]["parent_session_id"] == "sess-a"
    assert params["agent_hint"]["context_management"]["edits"] == [
        {"type": "offload", "target": "session"}
    ]


def test_message_and_tools_management_builds_two_edits():
    params = _client()._build_request_params(
        messages=[{"role": "user", "content": "hello"}],
        tools=[{"type": "function", "function": {"name": "x", "parameters": {}}}],
        temperature=None,
        top_p=None,
        model=None,
        stop=None,
        max_tokens=None,
        stream=False,
        session_id="sess-a",
        kv_action="evict",
        target="messages",
        manage_request=True,
        msg_start=2,
        msg_end=3,
        include_tools=True,
        tools_start=0,
        tools_end=1,
    )

    edits = params["agent_hint"]["context_management"]["edits"]
    assert edits == [
        {"type": "evict", "target": "messages", "start": 2, "end": 3},
        {"type": "evict", "target": "tools", "start": 0, "end": 1},
    ]


@pytest.mark.parametrize(
    ("target", "msg_start", "msg_end", "tools_start", "tools_end"),
    [
        ("messages", 1, None, None, None),
        ("messages", None, 1, None, None),
        ("tools", None, None, 0, None),
        ("tools", None, None, None, 0),
    ],
)
def test_range_target_requires_both_start_and_end(
        target, msg_start, msg_end, tools_start, tools_end
):
    with pytest.raises(Exception):
        _client()._build_target_edits(
            action="evict",
            target=target,
            msg_start=msg_start,
            msg_end=msg_end,
            tools_start=tools_start,
            tools_end=tools_end,
        )


@pytest.mark.parametrize(
    ("start", "end"),
    [(-1, 0), (1, -1), (2, 1), (1, 1), (True, 1), (0, False)],
)
def test_range_target_rejects_invalid_half_open_range(start, end):
    with pytest.raises(Exception):
        _client()._build_target_edits(
            action="evict",
            target="messages",
            msg_start=start,
            msg_end=end,
        )


def test_session_management_rejects_ranges():
    with pytest.raises(Exception):
        _client()._build_request_params(
            messages=[],
            tools=None,
            temperature=None,
            top_p=None,
            model=None,
            stop=None,
            max_tokens=None,
            stream=False,
            session_id="sess-a",
            kv_action="evict",
            target="session",
            manage_request=True,
            msg_start=1,
        )


def test_model_reports_affinity_support_and_builds_invoke_kwargs():
    model = Model(
        model_client_config=ModelClientConfig(
            client_provider=ProviderType.AscendAffinity,
            api_key="test-key",
            api_base="https://example.test",
            verify_ssl=False,
        ),
        model_config=ModelRequestConfig(model="test-model"),
    )

    class Session:
        @staticmethod
        def get_session_id():
            return "sess-a"

    assert model.supports_kv_cache_affinity() is True
    # The generic legacy wrapper must not enable AscendAffinity. New callers use
    # the affinity-specific name below so release and affinity remain separate.
    assert model.build_kv_cache_invoke_kwargs(session=Session()) == {}
    assert model.build_kv_cache_affinity_invoke_kwargs(session=Session()) == {}
    assert model.build_kv_cache_affinity_invoke_kwargs(session=Session(), enable_kv_cache_affinity=True) == {
        "session_id": "sess-a",
        "parent_session_id": "sess-a",
    }


def test_kv_action_methods_have_explicit_parameters():
    expected = {
        "self",
        "session_id",
        "parent_session_id",
        "target",
        "messages",
        "tools",
        "model",
        "msg_start",
        "msg_end",
        "tools_start",
        "tools_end",
        "include_tools",
        "timeout",
    }

    for method_name in ("evict_kvc", "offload_kvc", "prefetch_kvc"):
        params = inspect.signature(getattr(AscendAffinityModelClient, method_name)).parameters
        assert set(params) == expected
        assert not any(param.kind == inspect.Parameter.VAR_KEYWORD for param in params.values())

        model_params = inspect.signature(getattr(Model, method_name)).parameters
        assert set(model_params) == expected
        assert not any(param.kind == inspect.Parameter.VAR_KEYWORD for param in model_params.values())


@pytest.mark.asyncio
async def test_kv_management_uses_shared_total_timeout_and_single_attempt():
    client = _client()
    request = AsyncMock(return_value={"choices": [{"message": {"content": ""}}]})
    client._make_ascend_affinity_request = request

    assert await client.offload_kvc(session_id="sess-a") is True

    assert request.await_args.kwargs["timeout"] == KVC_SESSION_OFFLOAD_PREFETCH_TIMEOUT_SECONDS
    assert request.await_args.kwargs["max_attempts"] == KVC_MANAGEMENT_MAX_ATTEMPTS


@pytest.mark.asyncio
async def test_kv_management_total_timeout_cancels_request():
    client = _client()
    cancelled = asyncio.Event()

    async def _slow_request(*_args, **_kwargs):
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    client._make_ascend_affinity_request = _slow_request

    with pytest.raises(Exception):
        await client.evict_kvc(session_id="sess-a", timeout=0.01)

    assert cancelled.is_set()
