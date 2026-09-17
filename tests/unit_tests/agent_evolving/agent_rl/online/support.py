"""Reusable in-process adapters for online-RL tests."""

from __future__ import annotations

from typing import Any

from fakeredis.aioredis import FakeRedis


class InMemoryRedis(FakeRedis):
    """Decoded async Redis fake shared by online-RL tests."""

    def __init__(self) -> None:
        super().__init__(decode_responses=True)


def openai_response(
    *,
    prompt_ids: list[int] | None = None,
    token_ids: list[int] | None = None,
    text: str = "pong",
    finish_reason: str = "stop",
    model: str = "model-1",
    response_id: str = "chatcmpl-test",
    logprobs: list[float] | None = None,
) -> dict[str, Any]:
    prompt_ids = [101] if prompt_ids is None else prompt_ids
    token_ids = [201] if token_ids is None else token_ids
    logprobs = [-0.1] * len(token_ids) if logprobs is None else logprobs
    return {
        "id": response_id,
        "model": model,
        "prompt_token_ids": prompt_ids,
        "choices": [
            {
                "message": {"role": "assistant", "content": text},
                "finish_reason": finish_reason,
                "token_ids": token_ids,
                "logprobs": {"content": [{"token": text, "logprob": value} for value in logprobs]},
            }
        ],
        "usage": {
            "prompt_tokens": len(prompt_ids),
            "completion_tokens": len(token_ids),
            "total_tokens": len(prompt_ids) + len(token_ids),
        },
    }
