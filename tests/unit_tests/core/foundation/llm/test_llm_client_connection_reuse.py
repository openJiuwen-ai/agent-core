# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Connection-reuse tests for the LLM client factory (F1).

These verify the load-bearing property of the F1 fix: the OpenAI/Anthropic SDK
clients built by ``create_async_openai_client`` / ``create_async_anthropic_client``
ride a *shared* HTTPX transport drawn from ``ConnectorPoolManager``, so repeated
calls reuse the same TCP/TLS connection pool instead of opening a new one per
request. They also guard the regression where ``invoke``/``stream`` close the
shared client (which would tear down the pool).
"""

from unittest.mock import AsyncMock, MagicMock, patch
import importlib.util

import pytest

from openjiuwen.core.common.clients.connector_pool import get_connector_pool_manager
from openjiuwen.core.common.clients.llm_client import (
    create_async_anthropic_client,
    create_async_openai_client,
)
from openjiuwen.core.foundation.llm import Model, ModelClientConfig, ModelRequestConfig, UserMessage

_ANTHROPIC_AVAILABLE = importlib.util.find_spec("anthropic") is not None


@pytest.fixture(autouse=True)
def _isolate_connector_pools():
    """Keep the global ConnectorPoolManager clean across these tests.

    The manager is a process-global singleton; other test modules assert exact
    pool counts. These tests create real httpx pools, so remove only the pools
    added during each test afterward. httpx pools need no explicit close
    (``_do_close`` is a no-op; the underlying httpcore pool is GC'd).
    """
    manager = get_connector_pool_manager()
    before = set(manager._connector_pools)
    yield
    pools = manager._connector_pools
    for key in list(pools):
        if key not in before:
            pools.pop(key, None)


def _transport(client) -> object:
    """Best-effort access to the SDK client's underlying httpx transport.

    Both the openai and anthropic SDKs store the httpx client at ``_client``;
    httpx stores its transport at ``_transport``.
    """
    httpx_client = getattr(client, "_client", client)
    return getattr(httpx_client, "_transport", httpx_client)


@pytest.mark.asyncio
async def test_openai_factory_shares_transport_across_calls():
    cfg = ModelClientConfig(
        client_provider="openai",
        api_key="sk-openai-reuse",
        api_base="https://reuse.openai.test/v1",
        verify_ssl=False,
    )
    c1 = await create_async_openai_client(cfg)
    c2 = await create_async_openai_client(cfg)
    # Same config => both clients must share one transport (one pool).
    assert _transport(c1) is _transport(c2)


@pytest.mark.asyncio
@pytest.mark.skipif(not _ANTHROPIC_AVAILABLE, reason="anthropic SDK not installed")
async def test_anthropic_factory_shares_transport_across_calls():
    cfg = ModelClientConfig(
        client_provider="anthropic",
        api_key="sk-anthropic-reuse",
        api_base="https://reuse.anthropic.test",
        verify_ssl=False,
    )
    c1 = await create_async_anthropic_client(cfg)
    c2 = await create_async_anthropic_client(cfg)
    assert _transport(c1) is _transport(c2)


@pytest.mark.asyncio
async def test_repeated_factory_calls_do_not_create_pool_per_call():
    """Multiple calls must not create a new pool per call.

    The HTTPX pool is keyed by ssl/proxy/pool config (httpcore then multiplexes
    per-host connections within it), so 3 calls with the same config reuse one
    pool — at most one new key appears (zero if a compatible pool already
    existed from a prior test). The regression this guards is a pool-per-call
    explosion.
    """
    manager = get_connector_pool_manager()
    cfg = ModelClientConfig(
        client_provider="openai",
        api_key="sk-pool-count",
        api_base="https://pool-count.openai.test/v1",
        verify_ssl=False,
    )
    before = set(manager._connector_pools)
    for _ in range(3):
        await create_async_openai_client(cfg)
    after = set(manager._connector_pools)
    assert len(after - before) <= 1


@pytest.mark.asyncio
async def test_invoke_does_not_close_pooled_client():
    """Regression guard: invoke must not close the shared, pooled SDK client."""
    model = Model(
        model_client_config=ModelClientConfig(
            client_provider="openai",
            api_key="sk-no-close",
            api_base="https://no-close.openai.test/v1",
            verify_ssl=False,
        ),
        model_config=ModelRequestConfig(model="gpt-4o-mini"),
    )

    mock_client = AsyncMock()
    response = MagicMock()
    response.choices = [MagicMock()]
    response.choices[0].message = MagicMock()
    response.choices[0].message.content = "ok"
    response.choices[0].message.tool_calls = None
    response.choices[0].message.reasoning_content = None
    response.usage = MagicMock()
    response.usage.prompt_tokens = 1
    response.usage.completion_tokens = 1
    response.usage.total_tokens = 2
    response.usage.prompt_tokens_details = None
    mock_client.chat.completions.create = AsyncMock(return_value=response)

    with patch.object(
        model._client,
        "_create_async_openai_client",
        new=AsyncMock(return_value=mock_client),
    ):
        await model.invoke(messages=[UserMessage(content="hello")])

    mock_client.close.assert_not_called()
    mock_client.aclose.assert_not_called()
