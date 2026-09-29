# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""OpenRouter Decisions protocol, dated model identity and existing fallback."""

import asyncio
import json
import time

import httpx
import pytest

from openjiuwen.harness.tools.browser_move.decision.config import BrowserDecisionConfig
from openjiuwen.harness.tools.browser_move.decision.jev_client import DecisionUnavailable, JevClient
from tests.unit_tests.harness.tools.browser_move.test_browser_jev_policy import (
    TOOLS,
    answer,
    messages_for,
    request_payload,
    setup_policy,
)


def router_config(**kwargs):
    return BrowserDecisionConfig(provider="openrouter", api_key_env="TEST_JEV_ROUTER_KEY", **kwargs)


def router_answer(model="typesafe/jev-1.13-20260917", *, flat=True):
    return {**answer(flat=flat), "model": model, "id": "gen-dec-synthetic", "provider": "TypeSafe"}


def test_provider_defaults_select_the_matching_endpoint_model_and_key():
    native = BrowserDecisionConfig()
    assert (native.mode, native.model, native.api_base, native.api_key_env) == (
        "llm", "jev-1.13.0", "https://api.typesafe.ai/v1", "TYPESAFE_API_KEY",
    )
    router = BrowserDecisionConfig(provider="openrouter")
    assert (router.mode, router.model, router.api_base, router.api_key_env) == (
        "llm", "typesafe/jev-1.13", "https://openrouter.ai/api/alpha", "OPENROUTER_API_KEY",
    )


@pytest.mark.parametrize("model", ["jev-1.13.0", "typesafe/jev-latest", "unrelated/chat-model"])
def test_openrouter_rejects_native_or_chat_model_names(model):
    with pytest.raises(ValueError, match="OpenRouter requires"):
        router_config(model=model)


@pytest.mark.asyncio
@pytest.mark.parametrize("requested,resolved", [
    ("typesafe/jev-1.13", "typesafe/jev-1.13"),
    ("typesafe/jev-1.13", "typesafe/jev-1.13-20260917"),
    ("typesafe/jev-1.13-20260917", "typesafe/jev-1.13-20260917"),
    ("~typesafe/jev-latest", "typesafe/jev-1.14-20260923"),
])
async def test_decisions_endpoint_preserves_typed_contract_and_resolved_model(monkeypatch, requested, resolved):
    monkeypatch.setenv("TEST_JEV_ROUTER_KEY", "synthetic-router-key")
    payload = {"model": requested, "state": {"goal": "Click Sales"}, "questions": {
        "action": {"type": "choice", "instructions": "Choose the next action", "criteria": {
            "a1": "Click Sales", "HANDOFF": "Delegate", "FINISH": "Answer",
        }},
    }}

    def handler(request):
        assert str(request.url) == "https://openrouter.ai/api/alpha/decisions"
        assert request.headers["Authorization"] == "Bearer synthetic-router-key"
        assert json.loads(request.content) == payload
        return httpx.Response(200, json=router_answer(resolved))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        result = await JevClient(router_config(model=requested), client=http).evaluate(
            payload, deadline_at=time.time() + 5,
        )
    assert result["model"] == resolved
    assert result["answers"]["action"]["choice"] == "a1"
    assert result["usage"]["input_tokens"] == 30


@pytest.mark.asyncio
@pytest.mark.parametrize("requested,resolved", [
    ("typesafe/jev-1.13", "typesafe/jev-1.14-20260917"),
    ("typesafe/jev-1.13", "typesafe/jev-1.130-20260917"),
    ("typesafe/jev-1.13-20260917", "typesafe/jev-1.13-20260918"),
    ("typesafe/jev-1.13", "jev-1.13.0"),
    ("typesafe/jev-1.13", "typesafe/jev-1.13-untrusted"),
    ("~typesafe/jev-latest", "unrelated/model"),
])
async def test_model_validation_never_accepts_a_different_version(monkeypatch, requested, resolved):
    monkeypatch.setenv("TEST_JEV_ROUTER_KEY", "synthetic-router-key")
    async with httpx.AsyncClient(transport=httpx.MockTransport(
        lambda _: httpx.Response(200, json=router_answer(resolved)),
    )) as http:
        with pytest.raises(DecisionUnavailable, match="model"):
            await JevClient(router_config(model=requested), client=http).evaluate(
                request_payload(requested), deadline_at=time.time() + 5,
            )


@pytest.mark.asyncio
@pytest.mark.parametrize("streaming", [False, True])
async def test_openrouter_success_compiles_through_the_existing_policy(monkeypatch, streaming):
    monkeypatch.setenv("TEST_JEV_ROUTER_KEY", "synthetic-router-key")
    policy, llm, _, runtime, context, captured = setup_policy()
    policy.decision_config = router_config(mode="hybrid")
    async with httpx.AsyncClient(transport=httpx.MockTransport(
        lambda _: httpx.Response(200, json=router_answer(flat=False)),
    )) as http:
        policy.jev = JevClient(policy.decision_config, client=http)
        messages = await messages_for(policy, context, captured)
        if streaming:
            result = [chunk async for chunk in policy.stream(messages, tools=TOOLS)][0]
        else:
            result = await policy.invoke(messages, tools=TOOLS)
    assert result.metadata["browser_policy"]["provider"] == "openrouter"
    assert result.metadata["browser_policy"]["route"] == "jev"
    assert result.response_model == "typesafe/jev-1.13-20260917"
    assert result.tool_calls[0].name == "browser_batch_interact"
    llm.invoke.assert_not_awaited()
    llm.stream.assert_not_called()
    runtime._call_playwright_run_code_unsafe.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [401, 402, 403, 429, 503])
async def test_openrouter_failures_keep_task_on_original_llm_with_bounded_retries(monkeypatch, status):
    monkeypatch.setenv("TEST_JEV_ROUTER_KEY", "synthetic-router-key")
    policy, llm, _, runtime, context, captured = setup_policy()
    policy.decision_config = router_config(mode="hybrid")
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(status, text="private provider response", headers={"Retry-After": "0"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        policy.jev = JevClient(policy.decision_config, client=http)
        for _ in range(2):
            result = await policy.invoke(await messages_for(policy, context, captured), tools=TOOLS)
            assert result.content == "original LLM answer"
            assert result.metadata["browser_policy"]["reason"] == f"jev_http_{status}"
            assert "private" not in str(result.metadata)
    assert len(calls) == (2 if status in {429, 503} else 1)
    assert llm.invoke.await_count == 2
    assert not policy._guards


@pytest.mark.asyncio
async def test_openrouter_timeout_and_cancellation_respect_existing_control_flow(monkeypatch):
    monkeypatch.setenv("TEST_JEV_ROUTER_KEY", "synthetic-router-key")

    async def pending(_):
        await asyncio.Event().wait()

    async with httpx.AsyncClient(transport=httpx.MockTransport(pending)) as http:
        client = JevClient(router_config(request_timeout_ms=100), client=http)
        with pytest.raises(DecisionUnavailable, match="jev_timeout"):
            await client.evaluate(request_payload("typesafe/jev-1.13"), deadline_at=time.time() + 5)
        task = asyncio.create_task(client.evaluate(request_payload("typesafe/jev-1.13"), deadline_at=time.time() + 5))
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
