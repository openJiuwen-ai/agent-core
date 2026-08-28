from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from openjiuwen.agent_evolving.agent_rl.online.gateway.app.anthropic_protocol import (
    convert_anthropic_request,
)
from tests.unit_tests.agent_evolving.agent_rl.online.support import (
    gateway_test_app,
    openai_response,
)


class _Forwarder:
    def __init__(self) -> None:
        self.bodies: list[dict[str, Any]] = []

    async def forward(self, body: dict[str, Any], headers: dict[str, str]) -> dict[str, Any]:
        del headers
        self.bodies.append(dict(body))
        return openai_response(
            prompt_ids=[101, 102],
            logprobs=[-0.2],
            model="upstream-model",
            response_id="chatcmpl-anthropic",
        )


def _build_app(
    *,
    forwarder: _Forwarder,
):
    return gateway_test_app(
        forwarder=forwarder,
        model_id="default-model",
    )


def test_anthropic_tool_history_arguments_are_serialized_for_vllm() -> None:
    expected = {
        "file_path": "/tmp/calculator.py",
        "old_string": "left - right",
        "new_string": "left + right",
    }
    converted = convert_anthropic_request(
        {
            "model": "client-model",
            "messages": [
                {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "tool-1",
                            "name": "Edit",
                            "input": {**expected, "replace_all": False},
                        }
                    ],
                }
            ],
            "tools": [
                {
                    "name": "Edit",
                    "description": "Edit a file",
                    "input_schema": {
                        "type": "object",
                        "properties": {"replace_all": {"type": "boolean", "default": False}},
                    },
                }
            ],
            "max_tokens": 32,
        }
    )
    vllm_arguments = converted["messages"][0]["tool_calls"][0]["function"]["arguments"]

    assert json.loads(vllm_arguments) == expected
    assert converted["tools"] == [
        {
            "type": "function",
            "function": {
                "name": "Edit",
                "description": "Edit a file",
                "parameters": {
                    "type": "object",
                    "properties": {"replace_all": {"type": "boolean", "default": False}},
                },
            },
        }
    ]


def test_anthropic_tool_result_is_lowered_to_openai_tool_message() -> None:
    converted = convert_anthropic_request(
        {
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "tool-1",
                            "content": "done",
                        }
                    ],
                }
            ],
            "max_tokens": 32,
        }
    )

    assert converted["messages"] == [{"role": "tool", "tool_call_id": "tool-1", "content": "done"}]


def test_anthropic_system_prompt_and_mid_turn_reminder_are_preserved() -> None:
    converted = convert_anthropic_request(
        {
            "system": "x-anthropic-billing-header: ignored\nYou are a coding agent.",
            "messages": [
                {"role": "user", "content": "Fix it."},
                {"role": "system", "content": "Run tests."},
                {"role": "assistant", "content": "Working."},
            ],
            "max_tokens": 32,
        }
    )

    assert converted["messages"][0] == {"role": "system", "content": "You are a coding agent."}
    assert converted["messages"][1]["content"] == "Fix it.\n<system-reminder>\nRun tests.\n</system-reminder>"


@pytest.mark.asyncio
async def test_anthropic_stream_uses_anthropic_sse_envelope() -> None:
    app = _build_app(forwarder=_Forwarder())
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://gateway") as client:
        response = await client.post(
            "/v1/messages",
            headers={"x-user-id": "user-1"},
            json={
                "model": "client-model",
                "messages": [{"role": "user", "content": "ping"}],
                "max_tokens": 32,
                "stream": True,
            },
        )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert "event: message_start" in response.text
    assert '"type": "text_delta", "text": "pong"' in response.text
    assert "event: message_stop" in response.text


@pytest.mark.asyncio
async def test_anthropic_invalid_request_returns_anthropic_error_shape() -> None:
    app = _build_app(forwarder=_Forwarder())
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://gateway") as client:
        response = await client.post(
            "/v1/messages",
            headers={"x-user-id": "user-1"},
            json={"model": "client-model", "messages": [], "max_tokens": 32},
        )

    assert response.status_code == 400
    assert response.json()["type"] == "error"
    assert response.json()["error"]["type"] == "invalid_request_error"
