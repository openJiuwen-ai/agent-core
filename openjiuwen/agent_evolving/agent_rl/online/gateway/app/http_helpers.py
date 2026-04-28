# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""HTTP-facing helpers for gateway app layer."""

from __future__ import annotations

import json
import time
from typing import Any, Optional

from fastapi import HTTPException, Request


async def ensure_gateway_auth(gateway_api_key: str, authorization: Optional[str]) -> None:
    """Validate Bearer token when gateway auth is enabled."""
    if not gateway_api_key:
        return
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="missing bearer token")
    token = authorization.split(" ", 1)[1].strip()
    if token != gateway_api_key:
        raise HTTPException(status_code=403, detail="invalid bearer token")


def build_upstream_headers(request: Request, *, llm_api_key: str) -> dict[str, str]:
    """Filter inbound headers and inject upstream API key if configured."""
    headers: dict[str, str] = {}
    for key, value in request.headers.items():
        lk = key.lower()
        if lk in {"host", "content-length", "connection"}:
            continue
        if lk.startswith("x-forwarded-"):
            continue
        headers[key] = value
    if llm_api_key:
        headers["Authorization"] = f"Bearer {llm_api_key}"
    return headers


async def stream_chat_response(response_json: dict[str, Any], *, model_id: str):
    """Wrap non-streaming chat response into synthetic SSE stream."""
    created = int(response_json.get("created", int(time.time())))
    resp_id = response_json.get("id", f"chatcmpl-gw-{created}")
    model = response_json.get("model", model_id)
    usage = response_json.get("usage")
    prompt_token_ids = response_json.get("prompt_token_ids")
    choices = response_json.get("choices")
    choice = choices[0] if isinstance(choices, list) and choices else {}
    message = choice.get("message", {}) if isinstance(choice, dict) else {}
    finish_reason = choice.get("finish_reason", "stop") if isinstance(choice, dict) else "stop"
    token_ids = choice.get("token_ids") if isinstance(choice, dict) else None
    logprobs = choice.get("logprobs") if isinstance(choice, dict) else None

    delta: dict[str, Any] = {}
    role = message.get("role")
    if role:
        delta["role"] = role
    content = message.get("content")
    if isinstance(content, str) and content:
        delta["content"] = content
    if message.get("tool_calls"):
        delta["tool_calls"] = message["tool_calls"]
    if message.get("reasoning_content"):
        delta["reasoning_content"] = message["reasoning_content"]

    first = {
        "id": resp_id,
        "object": "chat.completion.chunk",
        "created": created,
        "model": model,
        "choices": [{
            "index": 0,
            "delta": delta,
            "finish_reason": None,
            "token_ids": token_ids,
            "logprobs": logprobs,
        }],
    }
    if prompt_token_ids is not None:
        first["prompt_token_ids"] = prompt_token_ids
    last = {
        "id": resp_id,
        "object": "chat.completion.chunk",
        "created": created,
        "model": model,
        "choices": [{"index": 0, "delta": {}, "finish_reason": finish_reason}],
    }
    if usage is not None:
        last["usage"] = usage
    yield f"data: {json.dumps(first, ensure_ascii=False)}\n\n"
    yield f"data: {json.dumps(last, ensure_ascii=False)}\n\n"
    yield "data: [DONE]\n\n"
