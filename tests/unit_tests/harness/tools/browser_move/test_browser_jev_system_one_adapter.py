# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Shared System One transport must preserve browser safety and request budgets."""

import asyncio
import json
import time

import httpx
import pytest

from openjiuwen.core.foundation.llm.system_one import JevSystemOneClient
from openjiuwen.harness.tools.browser_move.decision import jev_client as adapter_module
from openjiuwen.harness.tools.browser_move.decision.config import BrowserDecisionConfig
from openjiuwen.harness.tools.browser_move.decision.jev_client import DecisionUnavailable, JevClient
from tests.unit_tests.harness.tools.browser_move.test_browser_jev_policy import (
    TOOLS,
    answer,
    messages_for,
    request_payload,
    setup_policy,
)


@pytest.fixture(params=["typesafe", "openrouter"])
def config(request, monkeypatch):
    monkeypatch.setenv("TEST_SHARED_JEV_KEY", "synthetic-provider-key")
    return BrowserDecisionConfig(provider=request.param, mode="hybrid", api_key_env="TEST_SHARED_JEV_KEY")


def response_data(config, **changes):
    model = config.model + ("-20260917" if config.provider == "openrouter" else "")
    return {**answer(flat=True, **changes), "model": model}


@pytest.mark.asyncio
async def test_both_providers_use_core_protocol_without_nested_retries(config, monkeypatch):
    original = JevSystemOneClient.system_one
    core_calls, requests = [], []

    async def observed(self, *args, **kwargs):
        core_calls.append((self.max_retries, self.endpoint_path))
        return await original(self, *args, **kwargs)

    monkeypatch.setattr(JevSystemOneClient, "system_one", observed)

    def handler(request):
        requests.append(request)
        if len(requests) == 1:
            return httpx.Response(529, headers={"Retry-After": "0"})
        return httpx.Response(200, json=response_data(config))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        result = await JevClient(config, client=http).evaluate(
            request_payload(config.model), deadline_at=time.time() + 5,
        )
    path = "/decisions" if config.provider == "openrouter" else "/systemone"
    assert core_calls == [(0, path), (0, path)]
    assert len(requests) == 2
    assert str(requests[-1].url) == config.api_base + path
    assert json.loads(requests[-1].content) == request_payload(config.model)
    assert result["answers"]["action"]["choice"] == "a1"


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["confidence", "probability"])
@pytest.mark.parametrize("value", [True, "0.98"])
async def test_typed_response_cannot_coerce_bad_values_into_executable_decisions(config, field, value):
    data = response_data(config)
    action = data["answers"]["action"]
    if field == "confidence":
        action["confidence"] = value
    else:
        action["probabilities"]["a1"] = value
    policy, llm, _, runtime, context, captured = setup_policy()
    policy.decision_config = config
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json=data)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        policy.jev = JevClient(config, client=http)
        for _ in range(2):
            result = await policy.invoke(await messages_for(policy, context, captured), tools=TOOLS)
            assert result.metadata["browser_policy"]["reason"] == "invalid_jev_response"
            assert not result.tool_calls
    assert len(requests) == 1
    assert llm.invoke.await_count == 2
    assert not policy._guards
    runtime._call_playwright_run_code_unsafe.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure,reason", [
    ("json", "invalid_jev_json"), ("schema", "invalid_jev_response"),
    ("timeout", "jev_timeout"), ("connection", "jev_transport_error"),
])
async def test_shared_errors_keep_safe_browser_diagnostics(config, failure, reason):
    def handler(_):
        if failure == "timeout":
            raise httpx.ReadTimeout("private credential and response")
        if failure == "connection":
            raise httpx.ConnectError("private credential and response")
        if failure == "json":
            return httpx.Response(200, text="private credential and response")
        return httpx.Response(200, json={"answers": {"private": "credential and response"}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        with pytest.raises(DecisionUnavailable) as error:
            await JevClient(config, client=http).evaluate(request_payload(config.model), deadline_at=time.time() + 5)
    assert str(error.value) == reason
    assert error.value.__suppress_context__


@pytest.mark.asyncio
async def test_shared_deadline_cancels_retry_wait_without_another_request(config, monkeypatch):
    requests, waits = [], []

    async def pending_retry(delay):
        waits.append(delay)
        await asyncio.Event().wait()

    monkeypatch.setattr(adapter_module.asyncio, "sleep", pending_retry)

    def handler(request):
        requests.append(request)
        return httpx.Response(429, headers={"Retry-After": "0.1"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        with pytest.raises(DecisionUnavailable, match="jev_timeout"):
            await JevClient(config, client=http).evaluate(
                request_payload(config.model), deadline_at=time.time() + 0.1,
            )
    assert len(requests) == 1 and waits == [0.1]


@pytest.mark.asyncio
async def test_key_rotation_and_borrowed_connection_ownership_are_preserved(config, monkeypatch):
    credentials = []

    def handler(request):
        credentials.append(request.headers["Authorization"])
        return httpx.Response(200, json=response_data(config))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = JevClient(config, client=http)
        await client.evaluate(request_payload(config.model), deadline_at=time.time() + 5)
        monkeypatch.setenv(config.api_key_env, "synthetic-rotated-key")
        await client.evaluate(request_payload(config.model), deadline_at=time.time() + 5)
        await client.aclose()
        assert not http.is_closed
    assert credentials == ["Bearer synthetic-provider-key", "Bearer synthetic-rotated-key"]


@pytest.mark.asyncio
async def test_owned_connection_is_closed_after_shared_client_use(config, monkeypatch):
    http = httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, json=response_data(config))))
    monkeypatch.setattr(adapter_module.httpx, "AsyncClient", lambda **kwargs: http)
    client = JevClient(config)
    try:
        await client.evaluate(request_payload(config.model), deadline_at=time.time() + 5)
    finally:
        await client.aclose()
    assert http.is_closed


@pytest.mark.asyncio
async def test_invalid_request_is_rejected_before_network(config):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json=response_data(config))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        with pytest.raises(DecisionUnavailable, match="invalid_jev_request"):
            await JevClient(config, client=http).evaluate(
                {"state": "synthetic", "questions": {}}, deadline_at=time.time() + 5,
            )
    assert not requests
