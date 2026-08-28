# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Dev/test-only OpenAI adapter for exercising the RL completion hooks."""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncIterator, Mapping
from typing import Any, Protocol

import httpx
from fastapi import Body, FastAPI, Header, HTTPException
from fastapi.responses import JSONResponse, Response, StreamingResponse

from openjiuwen.agent_evolving.agent_rl.online.capture_pipeline import CapturePipeline
from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import BaseError, build_error


class VerificationUpstream(Protocol):
    """Fake or real OpenAI upstream used only by the verification adapter."""

    async def complete(self, request: dict[str, Any]) -> Mapping[str, Any] | AsyncIterator[Mapping[str, Any]]:
        """Return one JSON completion or an async sequence of SSE chunks."""

        ...


class HTTPVerificationUpstream:
    """Small OpenAI transport for manually pointing the adapter at a real vLLM."""

    def __init__(self, *, endpoint: str, http_client: httpx.AsyncClient, api_key: str = "") -> None:
        self._url = f"{endpoint.rstrip('/')}/v1/chat/completions"
        self._http_client = http_client
        self._headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}

    async def complete(self, request: dict[str, Any]) -> Mapping[str, Any] | AsyncIterator[Mapping[str, Any]]:
        """Forward one JSON or streaming OpenAI request."""

        if request.get("stream") is not True:
            try:
                response = await self._http_client.post(self._url, json=request, headers=self._headers)
                response.raise_for_status()
                payload = response.json()
            except (httpx.HTTPError, ValueError, TypeError) as exc:
                raise build_error(
                    StatusCode.AGENT_RL_VERIFICATION_UPSTREAM_CALL_FAILED,
                    cause=exc,
                    error_msg=str(exc),
                ) from exc
            if not isinstance(payload, Mapping):
                raise build_error(
                    StatusCode.AGENT_RL_VERIFICATION_UPSTREAM_CALL_FAILED,
                    error_msg="upstream response JSON must be an object",
                )
            return payload

        async def chunks() -> AsyncIterator[Mapping[str, Any]]:
            try:
                async with self._http_client.stream("POST", self._url, json=request, headers=self._headers) as response:
                    response.raise_for_status()
                    async for line in response.aiter_lines():
                        if not line.startswith("data:"):
                            continue
                        data = line.removeprefix("data:").strip()
                        if not data or data == "[DONE]":
                            continue
                        payload = json.loads(data)
                        if not isinstance(payload, Mapping):
                            raise build_error(
                                StatusCode.AGENT_RL_VERIFICATION_UPSTREAM_CALL_FAILED,
                                error_msg="upstream SSE data must be a JSON object",
                            )
                        yield payload
            except (httpx.HTTPError, ValueError, TypeError) as exc:
                raise build_error(
                    StatusCode.AGENT_RL_VERIFICATION_UPSTREAM_CALL_FAILED,
                    cause=exc,
                    error_msg=str(exc),
                ) from exc

        return chunks()


class _CompletionAggregate:
    def __init__(self) -> None:
        self.response_id = ""
        self.model = ""
        self.prompt_token_ids: list[int] = []
        self.content: list[str] = []
        self.reasoning: list[str] = []
        self.token_ids: list[int] = []
        self.logprobs: list[dict[str, Any]] = []
        self.tool_calls: dict[int, dict[str, Any]] = {}
        self.finish_reason: str | None = None
        self.usage: dict[str, Any] = {}

    def add(self, chunk: Mapping[str, Any]) -> None:
        """Merge one OpenAI SSE chunk into the final response."""

        self.response_id = str(chunk.get("id") or self.response_id)
        self.model = str(chunk.get("model") or self.model)
        prompt_token_ids = chunk.get("prompt_token_ids")
        if isinstance(prompt_token_ids, list):
            self.prompt_token_ids = list(prompt_token_ids)
        usage = chunk.get("usage")
        if isinstance(usage, Mapping):
            self.usage = dict(usage)
        choices = chunk.get("choices")
        if not isinstance(choices, list) or not choices:
            return
        choice = choices[0]
        if not isinstance(choice, Mapping):
            return
        delta = choice.get("delta")
        if not isinstance(delta, Mapping):
            delta = {}
        content = delta.get("content")
        if content is not None:
            self.content.append(str(content))
        reasoning = delta.get("reasoning_content")
        if reasoning is not None:
            self.reasoning.append(str(reasoning))
        token_ids = choice.get("token_ids")
        if isinstance(token_ids, list):
            self.token_ids.extend(token_ids)
        logprobs = choice.get("logprobs")
        logprob_content = logprobs.get("content") if isinstance(logprobs, Mapping) else None
        if isinstance(logprob_content, list):
            self.logprobs.extend(item for item in logprob_content if isinstance(item, dict))
        self._merge_tool_calls(delta.get("tool_calls"))
        finish_reason = choice.get("finish_reason")
        if finish_reason is not None:
            self.finish_reason = str(finish_reason)

    def response(self) -> dict[str, Any]:
        """Return the standard non-streaming response used by after()."""

        message: dict[str, Any] = {"role": "assistant", "content": "".join(self.content)}
        if self.reasoning:
            message["reasoning_content"] = "".join(self.reasoning)
        if self.tool_calls:
            message["tool_calls"] = [self.tool_calls[index] for index in sorted(self.tool_calls)]
        return {
            "id": self.response_id,
            "object": "chat.completion",
            "model": self.model,
            "prompt_token_ids": self.prompt_token_ids,
            "choices": [
                {
                    "index": 0,
                    "message": message,
                    "finish_reason": self.finish_reason,
                    "token_ids": self.token_ids,
                    "logprobs": {"content": self.logprobs},
                }
            ],
            "usage": self.usage,
        }

    def _merge_tool_calls(self, tool_calls: Any) -> None:
        if not isinstance(tool_calls, list):
            return
        for position, fragment in enumerate(tool_calls):
            if not isinstance(fragment, Mapping):
                continue
            index = int(fragment.get("index", position))
            target = self.tool_calls.setdefault(index, {"id": "", "type": "function", "function": {}})
            fragment_id = fragment.get("id")
            if fragment_id:
                target["id"] = str(fragment_id)
            fragment_type = fragment.get("type")
            if fragment_type:
                target["type"] = str(fragment_type)
            function = fragment.get("function")
            if isinstance(function, Mapping):
                target_function = target["function"]
                function_name = function.get("name")
                if function_name:
                    target_function["name"] = str(function_name)
                arguments = function.get("arguments")
                if arguments is not None:
                    target_function["arguments"] = str(target_function.get("arguments") or "") + str(
                        arguments,
                    )


def build_verification_gateway(*, capture_pipeline: CapturePipeline, upstream: VerificationUpstream) -> FastAPI:
    """Build the small OpenAI-only adapter used by unit and system tests."""

    app = FastAPI(title="OpenJiuwen RL Verification Gateway")

    @app.post("/v1/chat/completions")
    async def chat_completions(
        payload: dict[str, Any] = Body(...),
        rl_task_id: str | None = Header(default=None, alias="X-RL-Task-Id"),
        capture_id: str | None = Header(default=None, alias="X-Capture-Id"),
        agent_turn_id: str | None = Header(default=None, alias="X-Agent-Turn-Id"),
    ) -> Response:
        if not str(rl_task_id or "").strip():
            raise HTTPException(status_code=400, detail="X-RL-Task-Id is required")
        resolved_capture_id = str(capture_id or f"capture-{uuid.uuid4().hex[:12]}")
        injected = await capture_pipeline.before(
            str(rl_task_id),
            resolved_capture_id,
            agent_turn_id,
            payload,
        )
        try:
            result = await upstream.complete(injected)
        except asyncio.CancelledError:
            await capture_pipeline.discard(str(rl_task_id), resolved_capture_id, agent_turn_id)
            raise
        except BaseError:
            await capture_pipeline.discard(str(rl_task_id), resolved_capture_id, agent_turn_id)
            raise
        except Exception as exc:
            await capture_pipeline.discard(str(rl_task_id), resolved_capture_id, agent_turn_id)
            raise build_error(
                StatusCode.AGENT_RL_VERIFICATION_UPSTREAM_CALL_FAILED,
                cause=exc,
                error_msg=str(exc),
            ) from exc
        if isinstance(result, Mapping):
            await capture_pipeline.after(
                str(rl_task_id),
                resolved_capture_id,
                agent_turn_id,
                injected,
                result,
            )
            return JSONResponse(content=dict(result))

        async def stream() -> AsyncIterator[str]:
            aggregate = _CompletionAggregate()
            try:
                async for chunk in result:
                    aggregate.add(chunk)
                    yield f"data: {json.dumps(dict(chunk), ensure_ascii=False)}\n\n"
                await capture_pipeline.after(
                    str(rl_task_id),
                    resolved_capture_id,
                    agent_turn_id,
                    injected,
                    aggregate.response(),
                )
                yield "data: [DONE]\n\n"
            except (asyncio.CancelledError, GeneratorExit):
                await capture_pipeline.discard(str(rl_task_id), resolved_capture_id, agent_turn_id)
                raise
            except BaseError:
                await capture_pipeline.discard(str(rl_task_id), resolved_capture_id, agent_turn_id)
                raise
            except Exception as exc:
                await capture_pipeline.discard(str(rl_task_id), resolved_capture_id, agent_turn_id)
                raise build_error(
                    StatusCode.AGENT_RL_VERIFICATION_UPSTREAM_CALL_FAILED,
                    cause=exc,
                    error_msg=str(exc),
                ) from exc

        return StreamingResponse(stream(), media_type="text/event-stream")

    return app


__all__ = ["HTTPVerificationUpstream", "VerificationUpstream", "build_verification_gateway"]
