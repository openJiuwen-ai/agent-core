# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Three classifier formats: request shape and probability parsing (mock transport, no network)."""

from __future__ import annotations

import asyncio
import json
import math
from typing import Any, Callable

import httpx
import pytest

from openjiuwen.agent_teams.duplex import DuplexConfig
from openjiuwen.agent_teams.duplex.classifier import build_classifier, logprobs_support, reset_logprobs_support
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
    reset_logprobs_support()


@pytest.mark.level0
@pytest.mark.asyncio
async def test_openai_format_uses_letter_logprobs() -> None:
    import openai

    requests: list[dict] = []

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
            "logprobs": logprobs,
        }
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
    letter = next(r for r in requests if "tools" not in r["body"])
    body = letter["body"]
    assert letter["url"].endswith("/chat/completions") and letter["auth"] == "Bearer mock-api-key"
    assert body["max_tokens"] == 1 and body["logprobs"] is True and body["top_logprobs"] == 20
    assert "A = INTERRUPT" in body["messages"][0]["content"]
    assert answers["urgency"].choice == "INTERRUPT"
    assert answers["urgency"].probabilities["INTERRUPT"] == pytest.approx(0.75 / 0.95)


def _openai_sdk(requests: list[dict], respond: Callable[[dict], dict]) -> Any:
    import openai

    return openai.AsyncOpenAI(
        api_key="mock-api-key",
        base_url="http://relay.invalid/v1",
        max_retries=0,
        http_client=httpx.AsyncClient(transport=_transport(requests, respond)),
    )


def _openai_reply(message: dict) -> dict:
    choice = {"index": 0, "finish_reason": "stop", "message": {"role": "assistant", **message}}
    return {"id": "c1", "object": "chat.completion", "created": 0, "model": "m", "choices": [choice]}


def _openai_tool_reply(arguments: dict) -> dict:
    call = {
        "id": "call_1",
        "type": "function",
        "function": {"name": "record_choice", "arguments": json.dumps(arguments)},
    }
    return _openai_reply({"content": None, "tool_calls": [call]})


@pytest.mark.level0
@pytest.mark.asyncio
async def test_openai_without_logprobs_switches_to_choice_tool() -> None:
    requests: list[dict] = []

    def respond(body: dict) -> dict:
        if "tools" not in body:  # the relay drops logprobs
            return _openai_reply({"content": "A"})
        first, second = body["tools"][0]["function"]["parameters"]["properties"]["choice"]["enum"]
        return _openai_tool_reply({"choice": first, "probabilities": {first: 0.8, second: 0.2}})

    classifier = build_classifier(_config("openai", "http://relay.invalid/v1"))
    sdk = _openai_sdk(requests, respond)
    classifier.client._create_async_openai_client = lambda timeout=None: sdk

    answers = await classifier.classify(("urgency",), "{}", ["from peer: use Redis"])
    assert sorted(("tools" in r["body"], r["body"].get("logprobs")) for r in requests) == [(False, True), (True, None)]
    tool_body = next(r["body"] for r in requests if "tools" in r["body"])
    assert tool_body["tool_choice"] == "required"
    assert tool_body["tools"][0]["function"]["name"] == "record_choice"
    assert tool_body["tools"][0]["function"]["parameters"]["properties"]["choice"]["enum"] == ["INTERRUPT", "APPEND"]
    assert answers["urgency"].choice == "INTERRUPT"
    assert answers["urgency"].probabilities["INTERRUPT"] == pytest.approx(0.8)

    requests.clear()  # remembered: later questions go straight to the tool
    answers = await classifier.classify(("urgency", "relevance"), "{}", ["from peer: use Redis"])
    assert answers["relevance"].choice == "DELIVER"
    assert len(requests) == 2 and all("tools" in r["body"] and "logprobs" not in r["body"] for r in requests)


@pytest.mark.level0
@pytest.mark.asyncio
async def test_openai_choice_tool_failure_raises() -> None:
    requests: list[dict] = []

    def respond(body: dict) -> dict:
        return _openai_reply({"content": "INTERRUPT"})  # no logprobs and no tool call

    classifier = build_classifier(_config("openai", "http://relay.invalid/v1"))
    sdk = _openai_sdk(requests, respond)
    classifier.client._create_async_openai_client = lambda timeout=None: sdk

    with pytest.raises(BaseError):
        await classifier.classify(("urgency",), "{}", ["from peer: use Redis"])
    assert sorted("tools" in r["body"] for r in requests) == [False, True]


def _letter_reply(letter: str) -> dict:
    top = [{"token": letter, "logprob": math.log(0.9), "bytes": None}]
    choice = {
        "index": 0,
        "finish_reason": "length",
        "message": {"role": "assistant", "content": letter},
        "logprobs": {"content": [{"token": letter, "logprob": math.log(0.9), "bytes": None, "top_logprobs": top}]},
    }
    return {"id": "c1", "object": "chat.completion", "created": 0, "model": "m", "choices": [choice]}


def _async_openai_sdk(requests: list[dict], respond: Callable[[dict], Any]) -> Any:
    """SDK whose relay answers through an async handler, so a request can wait or hang."""
    import openai

    async def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        requests.append({"url": str(request.url), "body": body})
        return httpx.Response(200, json=await respond(body))

    return openai.AsyncOpenAI(
        api_key="mock-api-key",
        base_url="http://relay.invalid/v1",
        max_retries=0,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )


def _tool_answer(body: dict) -> dict:
    first, second = body["tools"][0]["function"]["parameters"]["properties"]["choice"]["enum"]
    return _openai_tool_reply({"choice": first, "probabilities": {first: 0.8, second: 0.2}})


def _openai_classifier(requests: list[dict], respond: Callable[[dict], Any], *, timeout: float = 8.0) -> Any:
    config = _config("openai", "http://relay.invalid/v1").model_copy(update={"timeout_seconds": timeout})
    classifier = build_classifier(config)
    sdk = _async_openai_sdk(requests, respond)
    classifier.client._create_async_openai_client = lambda timeout=None: sdk
    return classifier


@pytest.mark.level0
@pytest.mark.asyncio
async def test_openai_first_question_sends_logprobs_and_tool_together() -> None:
    """Unknown support: both requests are in flight at once; valid logprobs win and are remembered."""
    requests: list[dict] = []
    tool_seen = asyncio.Event()

    async def respond(body: dict) -> dict:
        if "tools" in body:
            tool_seen.set()
            await asyncio.sleep(5)  # slower than the logprobs answer
            return _tool_answer(body)
        await asyncio.wait_for(tool_seen.wait(), 1)  # only answers once the tool request is also out
        return _letter_reply("A")

    classifier = _openai_classifier(requests, respond)
    answers = await asyncio.wait_for(classifier.classify(("urgency",), "{}", ["from peer: use Redis"]), 2)
    assert answers["urgency"].choice == "INTERRUPT" and answers["urgency"].probabilities["INTERRUPT"] == 1.0
    assert sorted("tools" in r["body"] for r in requests) == [False, True]
    assert logprobs_support("http://relay.invalid/v1", "m") is True

    requests.clear()  # known to support logprobs: only the logprobs request from now on
    await classifier.classify(("urgency", "relevance"), "{}", ["from peer: use Redis"])
    assert len(requests) == 2 and all("tools" not in r["body"] for r in requests)


@pytest.mark.level0
@pytest.mark.asyncio
async def test_openai_hung_logprobs_does_not_spend_the_deadline() -> None:
    """A relay that hangs on the logprobs request: the tool answer comes back within the deadline."""
    requests: list[dict] = []

    async def respond(body: dict) -> dict:
        if "tools" not in body:
            await asyncio.sleep(60)  # the relay never answers this one
        return _tool_answer(body)

    classifier = _openai_classifier(requests, respond, timeout=0.5)
    loop = asyncio.get_running_loop()
    started = loop.time()
    answers = await classifier.classify(("urgency",), "{}", ["from peer: use Redis"])
    assert loop.time() - started < 0.4
    assert answers["urgency"].choice == "INTERRUPT"

    await asyncio.sleep(0.7)  # the background probe hits its own deadline and records no support
    assert logprobs_support("http://relay.invalid/v1", "m") is False
    requests.clear()
    await classifier.classify(("urgency",), "{}", ["from peer: use Redis"])
    assert len(requests) == 1 and "tools" in requests[0]["body"]


@pytest.mark.level0
@pytest.mark.asyncio
async def test_openai_logprobs_support_is_shared_across_classifiers() -> None:
    """What one member's classifier learns about an endpoint, every other member's classifier reuses."""
    requests: list[dict] = []

    async def respond(body: dict) -> dict:
        if "tools" not in body:
            return _openai_reply({"content": "A"})  # the relay drops logprobs
        return _tool_answer(body)

    first = _openai_classifier(requests, respond)
    await first.classify(("urgency",), "{}", ["from peer: use Redis"])
    assert logprobs_support("http://relay.invalid/v1", "m") is False

    requests.clear()
    second = _openai_classifier(requests, respond)
    answers = await second.classify(("urgency", "relevance"), "{}", ["from peer: use Redis"])
    assert answers["relevance"].choice == "DELIVER"
    assert len(requests) == 2 and all("tools" in r["body"] for r in requests)


@pytest.mark.level0
@pytest.mark.asyncio
async def test_openai_failure_on_both_paths_records_nothing() -> None:
    """A wrong key fails both requests: that says nothing about logprobs, so the next question races again."""
    import openai

    requests: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append({"body": json.loads(request.content)})
        return httpx.Response(401, json={"error": {"message": "invalid key"}})

    classifier = build_classifier(_config("openai", "http://relay.invalid/v1"))
    sdk = openai.AsyncOpenAI(
        api_key="bad",
        base_url="http://relay.invalid/v1",
        max_retries=0,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    classifier.client._create_async_openai_client = lambda timeout=None: sdk

    with pytest.raises(BaseError):
        await classifier.classify(("urgency",), "{}", ["from peer: use Redis"])
    assert sorted("tools" in r["body"] for r in requests) == [False, True]
    assert logprobs_support("http://relay.invalid/v1", "m") is None


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


@pytest.mark.level0
def test_default_timeout_covers_a_relay_round_trip() -> None:
    assert DuplexConfig().timeout_seconds == 8.0
