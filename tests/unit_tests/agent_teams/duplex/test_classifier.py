# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Three classifier formats: request shape and probability parsing (mock transport, no network)."""

from __future__ import annotations

import json
import math
from typing import Any, Callable

import httpx
import pytest

from openjiuwen.agent_teams.duplex import DuplexConfig
from openjiuwen.agent_teams.duplex.classifier import build_classifier
from openjiuwen.core.common.exception.errors import BaseError

KEY_ENV = "DUPLEX_TEST_KEY"


def _config(fmt: str, api_base: str) -> DuplexConfig:
    return DuplexConfig(enabled=True, mode="active", format=fmt, api_base=api_base, model="m", api_key_env=KEY_ENV)


def _transport(requests: list[dict], respond: Callable[[dict], dict]) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        requests.append({"url": str(request.url), "auth": request.headers.get("authorization"), "body": body})
        return httpx.Response(200, json=respond(body))

    return httpx.MockTransport(handler)


@pytest.fixture(autouse=True)
def _key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(KEY_ENV, "mock-api-key")


@pytest.mark.level0
@pytest.mark.asyncio
async def test_openai_format_uses_letter_logprobs() -> None:
    import openai

    requests: list[dict] = []
    with_logprobs = {"value": True}

    def respond(body: dict) -> dict:
        logprobs = {
            "content": [
                {
                    "token": "A",
                    "logprob": math.log(0.75),
                    "bytes": None,
                    "top_logprobs": [
                        {"token": "A", "logprob": math.log(0.75), "bytes": None},
                        {"token": "B", "logprob": math.log(0.2), "bytes": None},
                        {"token": "Hello", "logprob": math.log(0.05), "bytes": None},
                    ],
                }
            ]
        }
        choice: dict[str, Any] = {
            "index": 0,
            "finish_reason": "length",
            "message": {"role": "assistant", "content": "A"},
        }
        if with_logprobs["value"]:
            choice["logprobs"] = logprobs
        return {"id": "c1", "object": "chat.completion", "created": 0, "model": "m", "choices": [choice]}

    classifier = build_classifier(_config("openai", "http://relay.invalid/v1"))
    sdk = openai.AsyncOpenAI(
        api_key="mock-api-key",
        base_url="http://relay.invalid/v1",
        max_retries=0,
        http_client=httpx.AsyncClient(transport=_transport(requests, respond)),
    )
    classifier.client._create_async_openai_client = lambda timeout=None: sdk

    answers = await classifier.classify(("urgency",), "{}", ["from peer: use Redis"])
    body = requests[0]["body"]
    assert requests[0]["url"].endswith("/chat/completions") and requests[0]["auth"] == "Bearer mock-api-key"
    assert body["max_tokens"] == 1 and body["logprobs"] is True and body["top_logprobs"] == 20
    assert "A = INTERRUPT" in body["messages"][0]["content"]
    assert answers["urgency"].choice == "INTERRUPT"
    assert answers["urgency"].probabilities["INTERRUPT"] == pytest.approx(0.75 / 0.95)

    with_logprobs["value"] = False  # no logprobs -> error, never a text fallback
    with pytest.raises(BaseError):
        await classifier.classify(("urgency",), "{}", ["from peer: use Redis"])


@pytest.mark.level0
@pytest.mark.asyncio
async def test_anthropic_format_forces_choice_tool() -> None:
    import anthropic

    requests: list[dict] = []

    def respond(body: dict) -> dict:
        tool_input = {"choice": "SKIP", "probabilities": {"DELIVER": 0.1, "SKIP": 0.9}}
        return {
            "id": "msg_1",
            "type": "message",
            "role": "assistant",
            "model": "m",
            "content": [{"type": "tool_use", "id": "tu_1", "name": "record_choice", "input": tool_input}],
            "stop_reason": "tool_use",
            "stop_sequence": None,
            "usage": {"input_tokens": 1, "output_tokens": 1},
        }

    classifier = build_classifier(_config("anthropic", "http://relay.invalid"))
    sdk = anthropic.AsyncAnthropic(
        api_key="mock-api-key",
        base_url="http://relay.invalid",
        max_retries=0,
        http_client=anthropic.DefaultAsyncHttpxClient(transport=_transport(requests, respond)),
    )
    classifier.client._create_async_anthropic_client = lambda timeout=None: sdk

    answers = await classifier.classify(("relevance",), "{}", ["from peer: lunch?"])
    body = requests[0]["body"]
    assert body["tool_choice"] == {"type": "tool", "name": "record_choice"}
    schema = body["tools"][0]["input_schema"]
    assert schema["properties"]["choice"]["enum"] == ["DELIVER", "SKIP"]
    assert answers["relevance"].choice == "SKIP" and answers["relevance"].probabilities["SKIP"] == pytest.approx(0.9)


@pytest.mark.level0
@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("api_base", "model", "url", "key_env"),
    [
        ("", "", "https://api.typesafe.ai/v1/systemone", "TYPESAFE_API_KEY"),
        ("https://openrouter.ai/api/alpha", "", "https://openrouter.ai/api/alpha/decisions", "OPENROUTER_API_KEY"),
        ("https://mcl.example.com/v2", "m", "https://mcl.example.com/v2/decisions", "DUPLEX_ROUTER_API_KEY"),
    ],
)
async def test_jev_endpoint_follows_api_base(
    api_base: str, model: str, url: str, key_env: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(key_env, "mock-api-key")
    requests: list[dict] = []

    def respond(body: dict) -> dict:
        answers = {
            name: {
                "type": "choice",
                "choice": labels[0],
                "confidence": 0.92,
                "probabilities": {labels[0]: 0.92, labels[1]: 0.08},
            }
            for name, labels in (("urgency", ["INTERRUPT", "APPEND"]), ("relevance", ["DELIVER", "SKIP"]))
        }
        return {"model": "m", "answers": answers, "usage": {"input_tokens": 1, "output_tokens": 1}}

    config = DuplexConfig(enabled=True, mode="active", format="jev", api_base=api_base, model=model)
    assert config.api_key_env == key_env and config.model
    from openjiuwen.agent_teams.duplex.classifier import JevChoiceClassifier

    classifier = JevChoiceClassifier(config, http_client=httpx.AsyncClient(transport=_transport(requests, respond)))
    answers = await classifier.classify(("relevance", "urgency"), '{"phase": "running"}', ["from peer: x"])
    body = requests[0]["body"]
    assert requests[0]["url"] == url
    assert set(body["questions"]) == {"relevance", "urgency"} and body["state"]["messages"] == ["from peer: x"]
    assert answers["urgency"].choice == "INTERRUPT" and answers["relevance"].choice == "DELIVER"
