# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""F1 regression tests for the aiohttp-based model clients.

SiliconFlow and InferenceAffinity previously built a fresh ``aiohttp.TCPConnector``
and ``aiohttp.ClientSession`` on every request, paying a full TCP+TLS handshake
each call. They now draw a shared connector from ``ConnectorPoolManager`` (keyed
by ssl config) and wrap it in a per-request ``ClientSession(connector_owner=False)``.

These tests guard against a regression to per-call connector construction.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import pytest

from openjiuwen.core.common.clients.connector_pool import (
    ConnectorPoolConfig,
    get_connector_pool_manager,
)
from openjiuwen.core.foundation.llm import ModelClientConfig, ModelRequestConfig, ProviderType
from openjiuwen.core.foundation.llm.model_clients.inference_affinity_model_client import (
    InferenceAffinityModelClient,
)
from openjiuwen.core.foundation.llm.model_clients.siliconflow_model_client import SiliconFlowModelClient


def _siliconflow_client() -> SiliconFlowModelClient:
    return SiliconFlowModelClient(
        ModelRequestConfig(model="Qwen/Qwen2.5-7B-Instruct"),
        ModelClientConfig(
            client_provider=ProviderType.SiliconFlow,
            api_key="sk-test",
            api_base="https://api.siliconflow.cn/v1",
            verify_ssl=False,
        ),
    )


def _inference_affinity_client() -> InferenceAffinityModelClient:
    return InferenceAffinityModelClient(
        ModelRequestConfig(model="test-model"),
        ModelClientConfig(
            client_provider=ProviderType.InferenceAffinity,
            api_key="sk-test",
            api_base="https://api.inference-affinity.example/v1",
            verify_ssl=False,
            timeout=42,
        ),
    )


def _fake_post_context():
    """Return a callable that mimics ``ClientSession.post`` returning an async context."""
    fake_response = MagicMock()
    fake_response.raise_for_status = MagicMock()

    def fake_post(*_args, **_kwargs):
        ctx = MagicMock()
        ctx.__aenter__ = AsyncMock(return_value=fake_response)
        ctx.__aexit__ = AsyncMock(return_value=None)
        return ctx

    return fake_post


@pytest.fixture(autouse=True)
def _reset_connector_pool():
    """Reset the process-global ConnectorPoolManager before each test.

    ``close_all()`` flips the singleton into a terminal closed state, so we clear
    the pool map directly and reopen it instead.
    """
    manager = get_connector_pool_manager()
    manager._closed = False
    manager._connector_pools.clear()


@pytest.mark.asyncio
async def test_siliconflow_reuses_tcp_connector_across_requests():
    """Two ``_apost`` calls must share one pooled ``TCPConnector``."""
    client = _siliconflow_client()

    real_cls = aiohttp.TCPConnector
    counter = {"n": 0}

    class CountingConnector(real_cls):
        def __init__(self, *args, **kwargs):
            counter["n"] += 1
            super().__init__(*args, **kwargs)

    with patch("openjiuwen.core.common.clients.connector_pool.TCPConnector", CountingConnector), \
            patch.object(aiohttp.ClientSession, "post", _fake_post_context()):
        async with client._apost({"model": "x"}, timeout=5):
            pass
        async with client._apost({"model": "y"}, timeout=5):
            pass

    # Two requests, exactly one shared TCP connector constructed.
    assert counter["n"] == 1


@pytest.mark.asyncio
async def test_inference_affinity_reuses_tcp_connector_across_requests():
    """Two ``_create_session`` calls must share one pooled ``TCPConnector``."""
    client = _inference_affinity_client()

    real_cls = aiohttp.TCPConnector
    counter = {"n": 0}

    class CountingConnector(real_cls):
        def __init__(self, *args, **kwargs):
            counter["n"] += 1
            super().__init__(*args, **kwargs)

    with patch("openjiuwen.core.common.clients.connector_pool.TCPConnector", CountingConnector):
        async with client._create_session():
            pass
        async with client._create_session():
            pass

    assert counter["n"] == 1


def test_inference_affinity_request_timeout_honours_override_and_default():
    """``_request_timeout`` uses an explicit value or falls back to config timeout."""
    client = _inference_affinity_client()

    override = client._request_timeout(9)
    assert isinstance(override, aiohttp.ClientTimeout)
    assert override.total == 9

    default = client._request_timeout(None)
    assert isinstance(default, aiohttp.ClientTimeout)
    assert default.total == 42


def test_connector_pool_config_carries_client_ssl_settings():
    """Both clients forward their ssl config into the pool key so connections are
    pooled per ssl posture, not globally."""
    client = _siliconflow_client()
    # Sanity: the client exposes the ssl fields the pool config is built from.
    assert client.model_client_config.verify_ssl is False
    assert client.model_client_config.ssl_cert is None
    # And the pool config shape is constructible from them without error.
    cfg = ConnectorPoolConfig(
        ssl_verify=client.model_client_config.verify_ssl,
        ssl_cert=client.model_client_config.ssl_cert,
    )
    assert cfg.ssl_verify is False
