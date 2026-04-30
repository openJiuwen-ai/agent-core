from __future__ import annotations

import httpx
import pytest

from openjiuwen.agent_evolving.agent_rl.online.gateway.upstream.forwarder import Forwarder


class _FakeResponse:
    def __init__(self, payload: dict, status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code
        self.text = "upstream error"
        self.request = httpx.Request("POST", "http://upstream.test/v1/chat/completions")

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            response = httpx.Response(
                self.status_code,
                request=self.request,
                text=self.text,
            )
            raise httpx.HTTPStatusError("boom", request=self.request, response=response)

    def json(self) -> dict:
        return self._payload


class _FakeUpstreamClient:
    def __init__(self, response: _FakeResponse) -> None:
        self.response = response
        self.calls: list[dict] = []

    async def post_chat_completions(self, *, json_body: dict, headers: dict[str, str]) -> _FakeResponse:
        self.calls.append({"json_body": json_body, "headers": headers})
        return self.response


@pytest.mark.asyncio
async def test_forwarder_keeps_structured_tool_calls_unchanged():
    payload = {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "id": "call_1",
                            "type": "function",
                            "function": {"name": "read", "arguments": "{\"file_path\":\"/tmp/a\"}"},
                        }
                    ],
                },
                "finish_reason": "tool_calls",
            }
        ]
    }
    upstream_client = _FakeUpstreamClient(_FakeResponse(payload))
    forwarder = Forwarder(upstream_client=upstream_client, model_id="m1")

    result = await forwarder.forward({"messages": [{"role": "user", "content": "hi"}]}, {})

    assert result == payload
    assert upstream_client.calls[0]["json_body"]["model"] == "m1"
    assert upstream_client.calls[0]["json_body"]["logprobs"] is True
