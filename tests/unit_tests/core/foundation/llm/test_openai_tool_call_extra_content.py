# coding: utf-8
"""Regression coverage for OpenAI-compatible per-tool-call metadata."""

from types import SimpleNamespace
from typing import Any

import pytest
from openai.types.chat import ChatCompletionMessageFunctionToolCall

from openjiuwen.core.foundation.llm.model_clients.base_model_client import BaseModelClient
from openjiuwen.core.foundation.llm.model_clients.openai_model_client import OpenAIModelClient
from openjiuwen.core.foundation.llm.schema.config import ModelClientConfig, ModelRequestConfig, ProviderType
from openjiuwen.core.foundation.llm.schema.message import AssistantMessage, BaseMessage

_ABSENT = object()


def _client() -> OpenAIModelClient:
    return OpenAIModelClient(
        ModelRequestConfig(model="gemini-test"),
        ModelClientConfig(
            client_provider=ProviderType.OpenAI,
            api_key="test-key",
            api_base="https://example.test/v1",
            verify_ssl=False,
        ),
    )


def _serialize(message: BaseMessage) -> dict[str, Any]:
    return BaseModelClient._convert_messages_to_dict([message])[0]


def _raw_tool_call(
    *,
    call_id: str,
    name: str,
    arguments: str,
    index: int,
    extra_content: Any = _ABSENT,
    via_model_extra: bool = False,
) -> SimpleNamespace:
    fields: dict[str, Any] = {
        "id": call_id,
        "index": index,
        "function": SimpleNamespace(name=name, arguments=arguments),
    }
    if extra_content is not _ABSENT:
        if via_model_extra:
            fields["model_extra"] = {"extra_content": extra_content}
        else:
            fields["extra_content"] = extra_content
    return SimpleNamespace(**fields)


def _response(*tool_calls: Any) -> SimpleNamespace:
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(
                    content=None,
                    tool_calls=list(tool_calls),
                    reasoning_content=None,
                ),
                logprobs=None,
                token_ids=None,
            )
        ],
        usage=None,
        prompt_token_ids=None,
    )


def _stream_chunk(*tool_calls: Any, finish_reason: str | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                delta=SimpleNamespace(
                    content=None,
                    reasoning_content=None,
                    tool_calls=list(tool_calls),
                ),
                finish_reason=finish_reason,
                token_ids=None,
                logprobs=None,
            )
        ],
        usage=None,
        prompt_token_ids=None,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("via_model_extra", [False, True])
async def test_tool_call_extra_content_survives_history_round_trip(via_model_extra: bool) -> None:
    extra_content = {"google": {"thought_signature": "signature-a"}}
    raw_tool_call = _raw_tool_call(
        call_id="call-1",
        name="list_files",
        arguments='{"path":"."}',
        index=0,
        extra_content=extra_content,
        via_model_extra=via_model_extra,
    )

    parsed = await _client()._parse_response(_response(raw_tool_call))

    assert parsed.tool_calls is not None
    assert parsed.tool_calls[0].extra_content == extra_content
    message = _serialize(parsed)
    assert message["tool_calls"][0]["extra_content"] == extra_content


@pytest.mark.asyncio
async def test_openai_sdk_tool_call_extra_content_survives_history_round_trip() -> None:
    extra_content = {"google": {"thought_signature": "signature-a"}}
    raw_tool_call = ChatCompletionMessageFunctionToolCall.model_validate(
        {
            "id": "call-1",
            "type": "function",
            "function": {"name": "list_files", "arguments": "{}"},
            "extra_content": extra_content,
        }
    )

    parsed = await _client()._parse_response(_response(raw_tool_call))

    assert parsed.tool_calls is not None
    assert parsed.tool_calls[0].extra_content == extra_content
    message = _serialize(parsed)
    assert message["tool_calls"][0]["extra_content"] == extra_content


def test_nested_openai_tool_call_preserves_extra_content_in_model_dump() -> None:
    extra_content = {"google": {"thought_signature": "signature-a"}}
    message = AssistantMessage.model_validate(
        {
            "content": "",
            "tool_calls": [
                {
                    "id": "call-1",
                    "type": "function",
                    "function": {"name": "list_files", "arguments": "{}"},
                    "extra_content": extra_content,
                }
            ],
        }
    )

    assert message.tool_calls is not None
    assert message.tool_calls[0].extra_content == extra_content
    assert message.model_dump()["tool_calls"][0]["extra_content"] == extra_content


def test_stream_tool_call_extra_content_survives_parse_merge_and_history() -> None:
    extra_content = {"google": {"thought_signature": "signature-a"}}
    first = _client()._parse_stream_chunk(
        _stream_chunk(
            _raw_tool_call(
                call_id="call-1",
                name="list_files",
                arguments="{",
                index=0,
                extra_content=extra_content,
            )
        )
    )
    second = _client()._parse_stream_chunk(
        _stream_chunk(
            _raw_tool_call(
                call_id="call-1",
                name="",
                arguments='"path":"."}',
                index=0,
            ),
            finish_reason="tool_calls",
        )
    )

    assert first is not None
    assert second is not None
    merged = first + second
    assert merged.tool_calls is not None
    assert merged.tool_calls[0].arguments == '{"path":"."}'
    assert merged.tool_calls[0].extra_content == extra_content
    message = _serialize(merged)
    assert message["tool_calls"][0]["extra_content"] == extra_content


@pytest.mark.asyncio
async def test_parallel_tool_calls_keep_metadata_attached_to_original_call() -> None:
    extra_content = {"google": {"thought_signature": "signature-a"}}
    parsed = await _client()._parse_response(
        _response(
            _raw_tool_call(
                call_id="call-1",
                name="weather",
                arguments='{"city":"Paris"}',
                index=0,
                extra_content=extra_content,
            ),
            _raw_tool_call(
                call_id="call-2",
                name="weather",
                arguments='{"city":"London"}',
                index=1,
            ),
        )
    )

    message = _serialize(parsed)
    serialized_calls = message["tool_calls"]
    assert serialized_calls[0]["extra_content"] == extra_content
    assert "extra_content" not in serialized_calls[1]


@pytest.mark.asyncio
async def test_standard_openai_tool_call_does_not_emit_extra_content() -> None:
    parsed = await _client()._parse_response(
        _response(
            _raw_tool_call(
                call_id="call-1",
                name="list_files",
                arguments="{}",
                index=0,
            )
        )
    )

    message = _serialize(parsed)
    assert "extra_content" not in message["tool_calls"][0]
