from __future__ import annotations

import json

import httpx
import pytest

from openjiuwen.agent_evolving.agent_rl.online.verification_gateway import (
    HTTPVerificationUpstream,
    build_verification_gateway,
)
from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import BaseError


class _Pipeline:
    def __init__(self) -> None:
        self.before_calls: list[dict] = []
        self.after_calls: list[dict] = []
        self.discard_calls: list[dict] = []

    async def before(self, rl_task_id, capture_id, agent_turn_id, request):
        self.before_calls.append(
            {
                "rl_task_id": rl_task_id,
                "capture_id": capture_id,
                "agent_turn_id": agent_turn_id,
                "request": request,
            }
        )
        return {**request, "logprobs": True, "return_token_ids": True}

    async def after(self, rl_task_id, capture_id, agent_turn_id, request, response):
        self.after_calls.append(
            {
                "rl_task_id": rl_task_id,
                "capture_id": capture_id,
                "agent_turn_id": agent_turn_id,
                "request": request,
                "response": response,
            }
        )

    async def discard(self, rl_task_id, capture_id, agent_turn_id):
        self.discard_calls.append(
            {
                "rl_task_id": rl_task_id,
                "capture_id": capture_id,
                "agent_turn_id": agent_turn_id,
            }
        )


class _JSONUpstream:
    def __init__(self, response: dict) -> None:
        self.response = response
        self.requests: list[dict] = []

    async def complete(self, request: dict):
        self.requests.append(request)
        return self.response


def _response() -> dict:
    return {
        "id": "chatcmpl-1",
        "object": "chat.completion",
        "model": "base",
        "prompt_token_ids": [1],
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": "pong"},
                "finish_reason": "stop",
                "token_ids": [2],
                "logprobs": {"content": [{"token": "pong", "logprob": -0.1}]},
            }
        ],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    }


@pytest.mark.asyncio
async def test_http_upstream_maps_transport_failure_to_stable_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(502, request=request, text="upstream unavailable")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        upstream = HTTPVerificationUpstream(endpoint="http://vllm.local", http_client=http_client)
        with pytest.raises(BaseError) as exc_info:
            await upstream.complete({"model": "base", "messages": []})

    assert exc_info.value.status is StatusCode.AGENT_RL_VERIFICATION_UPSTREAM_CALL_FAILED
    assert isinstance(exc_info.value.cause, httpx.HTTPStatusError)


@pytest.mark.asyncio
async def test_http_upstream_rejects_non_object_json_response() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, request=request, json=[])

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        upstream = HTTPVerificationUpstream(endpoint="http://vllm.local", http_client=http_client)
        with pytest.raises(BaseError) as exc_info:
            await upstream.complete({"model": "base", "messages": []})

    assert exc_info.value.status is StatusCode.AGENT_RL_VERIFICATION_UPSTREAM_CALL_FAILED


@pytest.mark.asyncio
async def test_json_completion_runs_before_upstream_after() -> None:
    pipeline = _Pipeline()
    upstream = _JSONUpstream(_response())
    app = build_verification_gateway(capture_pipeline=pipeline, upstream=upstream)

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://verify.local") as client:
        response = await client.post(
            "/v1/chat/completions",
            headers={"X-RL-Task-Id": "task-1", "X-Capture-Id": "capture-1"},
            json={"model": "base", "messages": [{"role": "user", "content": "ping"}]},
        )

    assert response.status_code == 200
    assert response.json()["choices"][0]["message"]["content"] == "pong"
    assert upstream.requests[0]["logprobs"] is True
    assert pipeline.after_calls[0]["request"] == upstream.requests[0]
    assert pipeline.after_calls[0]["response"] == _response()


@pytest.mark.asyncio
async def test_sse_completion_streams_chunks_and_after_receives_aggregate() -> None:
    pipeline = _Pipeline()

    class _SSEUpstream:
        async def complete(self, request: dict):
            assert request["stream"] is True

            async def chunks():
                yield {
                    "id": "chatcmpl-1",
                    "object": "chat.completion.chunk",
                    "model": "base",
                    "prompt_token_ids": [1],
                    "choices": [
                        {
                            "index": 0,
                            "delta": {"role": "assistant", "content": "po"},
                            "token_ids": [2],
                            "logprobs": {"content": [{"token": "po", "logprob": -0.1}]},
                        }
                    ],
                }
                yield {
                    "id": "chatcmpl-1",
                    "object": "chat.completion.chunk",
                    "model": "base",
                    "choices": [
                        {
                            "index": 0,
                            "delta": {"content": "ng"},
                            "finish_reason": "stop",
                            "token_ids": [3],
                            "logprobs": {"content": [{"token": "ng", "logprob": -0.2}]},
                        }
                    ],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3},
                }

            return chunks()

    app = build_verification_gateway(capture_pipeline=pipeline, upstream=_SSEUpstream())
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://verify.local") as client:
        response = await client.post(
            "/v1/chat/completions",
            headers={"X-RL-Task-Id": "task-1", "X-Capture-Id": "capture-1"},
            json={"model": "base", "stream": True, "messages": [{"role": "user", "content": "ping"}]},
        )

    payloads = [
        json.loads(line.removeprefix("data: ")) for line in response.text.splitlines() if line.startswith("data: {")
    ]
    assert response.status_code == 200
    assert len(payloads) == 2
    aggregate = pipeline.after_calls[0]["response"]
    assert aggregate["object"] == "chat.completion"
    assert aggregate["choices"][0]["message"]["content"] == "pong"
    assert aggregate["choices"][0]["token_ids"] == [2, 3]
    assert aggregate["usage"]["total_tokens"] == 3


@pytest.mark.asyncio
async def test_upstream_failure_discards_open_capture() -> None:
    pipeline = _Pipeline()

    class _FailingUpstream:
        async def complete(self, request: dict):
            del request
            raise RuntimeError("upstream failed")

    app = build_verification_gateway(capture_pipeline=pipeline, upstream=_FailingUpstream())
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://verify.local") as client:
        with pytest.raises(BaseError) as exc_info:
            await client.post(
                "/v1/chat/completions",
                headers={"X-RL-Task-Id": "task-1", "X-Capture-Id": "capture-1"},
                json={"model": "base", "messages": [{"role": "user", "content": "ping"}]},
            )

    assert exc_info.value.status is StatusCode.AGENT_RL_VERIFICATION_UPSTREAM_CALL_FAILED
    assert isinstance(exc_info.value.cause, RuntimeError)
    assert pipeline.discard_calls == [{"rl_task_id": "task-1", "capture_id": "capture-1", "agent_turn_id": None}]


@pytest.mark.asyncio
async def test_stream_failure_discards_open_capture() -> None:
    pipeline = _Pipeline()

    class _FailingStreamUpstream:
        async def complete(self, request: dict):
            del request

            async def chunks():
                yield {"id": "chatcmpl-1", "choices": []}
                raise RuntimeError("stream failed")

            return chunks()

    app = build_verification_gateway(capture_pipeline=pipeline, upstream=_FailingStreamUpstream())
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://verify.local") as client:
        await client.post(
            "/v1/chat/completions",
            headers={"X-RL-Task-Id": "task-1", "X-Capture-Id": "capture-1"},
            json={"model": "base", "stream": True, "messages": [{"role": "user", "content": "ping"}]},
        )

    assert pipeline.discard_calls == [{"rl_task_id": "task-1", "capture_id": "capture-1", "agent_turn_id": None}]


@pytest.mark.asyncio
async def test_verification_gateway_has_no_anthropic_route() -> None:
    app = build_verification_gateway(capture_pipeline=_Pipeline(), upstream=_JSONUpstream(_response()))

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://verify.local") as client:
        response = await client.post("/v1/messages", json={})

    assert response.status_code == 404
