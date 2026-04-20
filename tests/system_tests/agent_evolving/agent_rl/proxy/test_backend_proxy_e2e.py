# -*- coding: utf-8 -*-
"""System tests for BackendProxy: start Flask, GET /health, POST /proxy/backends."""

import pytest

pytest.importorskip("flask")
pytest.importorskip("requests")

import requests

from openjiuwen.agent_evolving.agent_rl.proxy import BackendProxy


@pytest.fixture
def proxy():
    return BackendProxy()


@pytest.mark.asyncio
async def test_start_then_health_returns_200(proxy):
    await proxy.start()
    try:
        r = requests.get(f"{proxy.url}/health", timeout=2)
        assert r.status_code == 200
    finally:
        await proxy.stop()


@pytest.mark.asyncio
async def test_update_backends_via_post_proxy_backends(proxy):
    await proxy.start()
    try:
        r = requests.post(
            f"{proxy.url}/proxy/backends",
            json={"servers": ["http://127.0.0.1:8000"]},
            timeout=2,
        )
        assert r.status_code == 200
    finally:
        await proxy.stop()
