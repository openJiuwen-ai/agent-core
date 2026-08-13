#!/usr/bin/env python3
# coding: utf-8

"""Small OpenAI-compatible mock server for SFT control-plane E2E tests."""

from __future__ import annotations

import argparse
import json
import time
import uuid
from typing import Any

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse


def _text_from_messages(messages: list[dict[str, Any]]) -> str:
    for message in reversed(messages):
        content = message.get("content")
        if isinstance(content, str) and content.strip():
            return content.strip()
        if isinstance(content, list):
            parts = [str(item.get("text", "")) for item in content if isinstance(item, dict)]
            text = " ".join(part for part in parts if part)
            if text:
                return text
    return "empty prompt"


def _fake_token_ids(text: str, *, offset: int = 1000) -> list[int]:
    values = [offset + (ord(ch) % 2000) for ch in text[:128]]
    return values or [offset]


def build_app(*, model_name: str) -> FastAPI:
    app = FastAPI(title="SFT mock OpenAI server")

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/v1/models")
    async def models() -> dict[str, Any]:
        return {"object": "list", "data": [{"id": model_name, "object": "model", "root": model_name}]}

    @app.post("/v1/chat/completions")
    async def chat_completions(request: Request):
        body = await request.json()
        messages = body.get("messages") if isinstance(body.get("messages"), list) else []
        prompt_text = _text_from_messages(messages)
        response_text = f"mock supervisor answer for: {prompt_text[:160]}"
        prompt_ids = _fake_token_ids(json.dumps(messages, ensure_ascii=False), offset=10)
        completion_ids = _fake_token_ids(response_text, offset=5000)
        model = str(body.get("model") or model_name)
        choice = {
            "index": 0,
            "message": {"role": "assistant", "content": response_text},
            "finish_reason": "stop",
            "token_ids": completion_ids,
            "logprobs": {"content": [{"token": str(tid), "logprob": -0.01} for tid in completion_ids]},
        }
        payload = {
            "id": "chatcmpl-" + uuid.uuid4().hex[:12],
            "object": "chat.completion",
            "created": int(time.time()),
            "model": model,
            "choices": [choice],
            "prompt_token_ids": prompt_ids,
            "usage": {
                "prompt_tokens": len(prompt_ids),
                "completion_tokens": len(completion_ids),
                "total_tokens": len(prompt_ids) + len(completion_ids),
            },
        }
        if body.get("stream"):
            async def _events():
                chunk = {
                    "id": payload["id"],
                    "object": "chat.completion.chunk",
                    "created": payload["created"],
                    "model": model,
                    "choices": [{"index": 0, "delta": {"content": response_text}, "finish_reason": None}],
                }
                yield f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"
                done = {
                    "id": payload["id"],
                    "object": "chat.completion.chunk",
                    "created": payload["created"],
                    "model": model,
                    "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                }
                yield f"data: {json.dumps(done, ensure_ascii=False)}\n\n"
                yield "data: [DONE]\n\n"

            return StreamingResponse(_events(), media_type="text/event-stream")
        return JSONResponse(payload)

    return app


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=18102)
    parser.add_argument("--model", default="mock-supervisor")
    args = parser.parse_args()
    uvicorn.run(build_app(model_name=args.model), host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
