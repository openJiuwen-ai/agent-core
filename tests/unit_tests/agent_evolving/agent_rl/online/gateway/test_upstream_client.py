# -*- coding: utf-8 -*-
"""Unit tests for upstream HTTP client abstraction and retry behavior."""

from __future__ import annotations

import httpx
import pytest

from openjiuwen.agent_evolving.agent_rl.online.gateway.upstream.upstream_client import (
    HTTPXUpstreamGatewayClient,
    RetryPolicy,
)


@pytest.mark.asyncio
async def test_post_chat_completions_retries_on_retryable_status():
    state = {"calls": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/chat/completions"
        state["calls"] += 1
        if state["calls"] == 1:
            return httpx.Response(503, json={"error": "busy"})
        return httpx.Response(200, json={"ok": True})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=3.0) as http_client:
        client = HTTPXUpstreamGatewayClient(
            http_client=http_client,
            llm_url="http://mock.local",
            retry_policy=RetryPolicy(max_retries=2, backoff_base_sec=0.0, backoff_max_sec=0.0),
        )
        resp = await client.post_chat_completions(json_body={"messages": []}, headers={})

    assert resp.status_code == 200
    assert state["calls"] == 2


@pytest.mark.asyncio
async def test_request_retries_on_connect_error_then_succeeds():
    state = {"calls": 0}

    def handler(_: httpx.Request) -> httpx.Response:
        state["calls"] += 1
        if state["calls"] == 1:
            raise httpx.ConnectError("dial failed")
        return httpx.Response(200, json={"ok": True})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=3.0) as http_client:
        client = HTTPXUpstreamGatewayClient(
            http_client=http_client,
            llm_url="http://mock.local",
            retry_policy=RetryPolicy(max_retries=3, backoff_base_sec=0.0, backoff_max_sec=0.0),
        )
        resp = await client.request(
            method="GET",
            url="http://mock.local/v1/models",
            params={},
            headers={},
            content=b"",
        )

    assert resp.status_code == 200
    assert state["calls"] == 2

