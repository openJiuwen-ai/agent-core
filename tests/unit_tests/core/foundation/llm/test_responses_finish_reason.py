# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

import json
from unittest.mock import patch

import httpx
import pytest

from openjiuwen.core.foundation.llm import ModelClientConfig, ModelRequestConfig
from openjiuwen.core.foundation.llm.model_clients.openai_model_client import OpenAIModelClient
from openjiuwen.core.foundation.llm.utils.responses_transport import OpenAIAccountResponsesTransport
from openjiuwen.core.foundation.llm.utils.responses_utils import build_request_body, parse_response

_MODEL = "gpt-5.6-luna"
_CALL = {"type": "function_call", "id": "fc_1", "call_id": "call_1", "name": "save_note", "arguments": ""}


def _added() -> dict:
    return {"type": "response.output_item.added", "item": dict(_CALL)}


def _arguments_delta() -> dict:
    return {"type": "response.function_call_arguments.delta", "item_id": "fc_1", "delta": '{"text":"hi"}'}


def _call_done() -> dict:
    return {"type": "response.output_item.done", "item": {**_CALL, "arguments": '{"text":"hi"}'}}


def _text() -> dict:
    return {"type": "response.output_text.delta", "delta": "Once upon"}


def _completed() -> dict:
    return {"type": "response.completed", "response": {"status": "completed"}}


def _incomplete(reason) -> dict:
    response = {"status": "incomplete"}
    if reason is not None:
        response["incomplete_details"] = {"reason": reason}
    return {"type": "response.incomplete", "response": response}


# (case id, SSE events, finish_reason Chat Completions mode reports for the same outcome)
_CASES = [
    ("tool_call_completed", [_call_done(), _completed()], "tool_calls"),
    ("streamed_tool_call_completed", [_added(), _arguments_delta(), _call_done(), _completed()], "tool_calls"),
    ("text_completed", [_text(), _completed()], "stop"),
    ("tool_call_cut_off", [_added(), _arguments_delta(), _call_done(), _incomplete("max_output_tokens")], "length"),
    ("text_cut_off", [_text(), _incomplete("max_output_tokens")], "length"),
    ("text_content_filter", [_text(), _incomplete("content_filter")], "content_filter"),
    ("tool_call_content_filter", [_call_done(), _incomplete("content_filter")], "content_filter"),
    ("incomplete_without_reason", [_text(), _incomplete(None)], "length"),
    ("incomplete_other_reason", [_text(), _incomplete("max_messages")], "length"),
    # A gateway may end with response.completed but leave status "incomplete" in the payload.
    (
        "completed_event_with_incomplete_status",
        [_call_done(), {**_incomplete("max_output_tokens"), "type": "response.completed"}],
        "length",
    ),
    ("tool_call_without_done_event", [_added(), _arguments_delta(), _completed()], "tool_calls"),
]
_CASE_IDS = [case_id for case_id, _, _ in _CASES]


def _sse(events: list[dict]) -> bytes:
    blocks = []
    for event in events:
        data = {key: value for key, value in event.items() if key != "type"}
        blocks.append(f"event: {event['type']}\ndata: {json.dumps(data)}\n\n")
    return "".join(blocks).encode()


def _transport(events: list[dict]) -> OpenAIAccountResponsesTransport:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=_sse(events), headers={"content-type": "text/event-stream"})

    return OpenAIAccountResponsesTransport(transport=httpx.MockTransport(handler))


def _body() -> dict:
    return build_request_body(model=_MODEL, messages="Save a note.")


async def _merged_stream(chunks):
    merged = None
    async for chunk in chunks:
        merged = merged + chunk if merged else chunk
    return merged


@pytest.mark.asyncio
@pytest.mark.parametrize(("case_id", "events", "expected"), _CASES, ids=_CASE_IDS)
async def test_stream_reports_chat_finish_reason(case_id, events, expected):
    """Callers of stream() merge the chunks, as ReActAgent does."""
    merged = await _merged_stream(_transport(events).stream_response(body=_body(), access_token="token"))

    assert merged.finish_reason == expected


@pytest.mark.asyncio
@pytest.mark.parametrize(("case_id", "events", "expected"), _CASES, ids=_CASE_IDS)
async def test_invoke_reports_chat_finish_reason(case_id, events, expected):
    message = await _transport(events).create_response(body=_body(), access_token="token")

    assert message.finish_reason == expected


@pytest.mark.asyncio
async def test_tool_call_cut_off_keeps_tool_call_for_caller_to_inspect():
    message = await _transport(
        [_added(), _arguments_delta(), _call_done(), _incomplete("max_output_tokens")]
    ).create_response(body=_body(), access_token="token")

    assert message.finish_reason == "length"
    assert message.tool_calls[0].name == "save_note"


@pytest.mark.asyncio
async def test_raw_incomplete_reason_stays_in_provider_metadata():
    message = await _transport([_text(), _incomplete("content_filter")]).create_response(
        body=_body(), access_token="token"
    )

    assert message.provider_metadata["incomplete_details"] == {"reason": "content_filter"}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("events", "expected"),
    [([_call_done()], "tool_calls"), ([_text()], "stop")],
    ids=["tool_call", "text"],
)
async def test_invoke_without_terminal_event_is_unchanged(events, expected):
    message = await _transport(events).create_response(body=_body(), access_token="token")

    assert message.finish_reason == expected


def _responses_client() -> OpenAIModelClient:
    return OpenAIModelClient(
        ModelRequestConfig(model=_MODEL),
        ModelClientConfig(
            client_provider="OpenAI",
            api_key="sk-test-key",
            api_base="https://api.openai.com/v1",
            api_mode="responses",
            verify_ssl=False,
        ),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("events", "expected"),
    [
        ([_added(), _arguments_delta(), _call_done(), _completed()], "tool_calls"),
        ([_text(), _incomplete("content_filter")], "content_filter"),
    ],
    ids=["tool_call", "content_filter"],
)
async def test_model_client_stream_and_invoke_agree(events, expected):
    client = _responses_client()
    with patch.object(client, "_make_responses_transport", side_effect=lambda **_: _transport(events)):
        invoked = await client.invoke("Save a note.")
        streamed = await _merged_stream(client.stream("Save a note."))

    assert invoked.finish_reason == expected
    assert streamed.finish_reason == expected


_FUNCTION_CALL_OUTPUT = [{**_CALL, "arguments": '{"text":"hi"}'}]


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ({"status": "completed", "output": _FUNCTION_CALL_OUTPUT}, "tool_calls"),
        ({"status": "completed", "output_text": "hi"}, "stop"),
        (
            {
                "status": "incomplete",
                "incomplete_details": {"reason": "max_output_tokens"},
                "output": _FUNCTION_CALL_OUTPUT,
            },
            "length",
        ),
        ({"status": "incomplete", "incomplete_details": {"reason": "content_filter"}}, "content_filter"),
        ({"status": "incomplete", "output_text": "partial"}, "length"),
    ],
    ids=["tool_call", "text", "tool_call_cut_off", "content_filter", "incomplete_without_reason"],
)
def test_parse_response_reports_chat_finish_reason(payload, expected):
    assert parse_response(payload, model_name=_MODEL).finish_reason == expected
