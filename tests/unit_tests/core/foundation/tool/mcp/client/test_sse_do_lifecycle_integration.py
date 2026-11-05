#!/usr/bin/env python
# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Integration tests for the *real* ``_do_connect`` / ``_do_disconnect`` paths.

The sibling ``test_sse_*.py`` suites stub ``_do_connect`` / ``_do_disconnect``
to assert the command-queue / concurrency invariants.  This file instead
exercises the actual lifecycle implementation: the ``sse_client`` async
context, ``ClientSession.initialize()`` and ``__aexit__`` timeout fallbacks,
and the ``_session = None`` state reset — by faking the ``mcp`` SDK via
``sys.modules`` patching (no real network).

coverage:
- connect success path wires reader/writer/session and clears _is_disconnected;
- a hung ``initialize()`` is bounded by the defensive ``wait_for`` and
  returns False after cleaning up partial state;
- disconnect's ``__aexit__`` timeout forces cleanup instead of hanging;
- disconnect resets ``_session`` / ``_client`` / ``_read`` / ``_write`` to None.
"""
from __future__ import annotations

import asyncio
import sys
import types
from typing import Any
from unittest.mock import AsyncMock, Mock, patch

import pytest

from openjiuwen.core.foundation.tool.mcp.base import NO_TIMEOUT, McpServerConfig
from openjiuwen.core.foundation.tool.mcp.client.sse_client import SseClient
from openjiuwen.core.runner import Runner


def _make_client() -> SseClient:
    config = McpServerConfig(
        server_name="test-sse",
        server_path="http://test.local/sse",
    )
    return SseClient(config)


def _install_fake_mcp(
    *,
    initialize_delay: float = 0.0,
    initialize_raises: BaseException | None = None,
    aexit_delay: float = 0.0,
    aexit_raises: BaseException | None = None,
) -> dict[str, Any]:
    """Inject a fake ``mcp`` package into ``sys.modules``; return call state.

    Returns a state dict capturing how the fake ``sse_client`` and
    ``ClientSession`` were driven, so assertions can inspect the wiring.
    """
    state: dict[str, Any] = {
        "sse_client_kwargs": None,
        "session_initialized": False,
        "session_entered": False,
        "session_aexit_called": False,
        "client_aexit_called": False,
    }

    class FakeTransportContext:
        async def __aenter__(self):
            return "fake-read", "fake-write"

        async def __aexit__(self, exc_type, exc, tb):
            state["client_aexit_called"] = True
            if aexit_delay:
                await asyncio.sleep(aexit_delay)
            if aexit_raises is not None:
                raise aexit_raises
            return False

    class FakeClientSession:
        def __init__(self, read, write, sampling_callback=None):
            self._read = read
            self._write = write

        async def __aenter__(self):
            state["session_entered"] = True
            return self

        async def __aexit__(self, exc_type, exc, tb):
            state["session_aexit_called"] = True
            if aexit_delay:
                await asyncio.sleep(aexit_delay)
            if aexit_raises is not None:
                raise aexit_raises
            return False

        async def initialize(self):
            if initialize_delay:
                await asyncio.sleep(initialize_delay)
            if initialize_raises is not None:
                raise initialize_raises
            state["session_initialized"] = True

    def fake_sse_client(server_path, timeout, auth=None):
        state["sse_client_kwargs"] = {
            "server_path": server_path,
            "timeout": timeout,
            "auth": auth,
        }
        return FakeTransportContext()

    fake_mcp = types.ModuleType("mcp")
    fake_mcp.ClientSession = FakeClientSession
    fake_mcp_client = types.ModuleType("mcp.client")
    fake_sse = types.ModuleType("mcp.client.sse")
    fake_sse.sse_client = fake_sse_client
    fake_mcp_client.sse = fake_sse

    patcher = patch.dict(
        sys.modules,
        {
            "mcp": fake_mcp,
            "mcp.client": fake_mcp_client,
            "mcp.client.sse": fake_sse,
        },
        clear=False,
    )
    patcher.start()
    state["patcher"] = patcher
    return state


async def _stop(client: SseClient) -> None:
    if client._owner_task is None or client._owner_task.done():
        return
    client._owner_task.cancel()
    try:
        await client._owner_task
    except (asyncio.CancelledError, Exception):
        pass


@pytest.mark.asyncio
@pytest.mark.parametrize("size", [8192, 8193, 9000])
async def test_real_sse_sdk_url_size_boundary_reaches_http_transport(monkeypatch, size):
    """Use the installed SSE SDK and HTTPX; replace only the network transport."""
    httpx = pytest.importorskip("httpx")
    sdk = pytest.importorskip("mcp.client.sse")
    from openjiuwen.core.foundation.tool.mcp.client.sse_client import logger as sse_logger
    from openjiuwen.harness.security import outbound
    from openjiuwen.harness.security.outbound import policy

    monkeypatch.setattr(policy, "_state", policy.get_host_exit_state())
    policy.publish_host_exit_policy({"net_guard": {"enabled": True, "defaults": "allow", "urls": {}}})
    prefix = "http://192.0.2.10:63656/sse?pad="
    url = prefix + "a" * (size - len(prefix))
    assert len(url) == size
    checked, sent = [], []
    original_check = outbound.check_mcp_server_url
    original_sdk = sdk.sse_client

    def check(value):
        checked.append(value)
        original_check(value)

    def respond(request):
        sent.append(str(request.url))
        # A rig need not implement MCP to prove the URL reached transport.
        return httpx.Response(400, text="synthetic rig response")

    def http_factory(**kwargs):
        return httpx.AsyncClient(transport=httpx.MockTransport(respond), follow_redirects=True, **kwargs)

    def sse_client(value, **kwargs):
        return original_sdk(value, httpx_client_factory=http_factory, **kwargs)

    monkeypatch.setattr(outbound, "check_mcp_server_url", check)
    monkeypatch.setattr(sdk, "sse_client", sse_client)
    errors = Mock()
    monkeypatch.setattr(sse_logger, "error", errors)
    client = SseClient(McpServerConfig(server_name=f"size{size}-mcp", server_path=url))
    try:
        with patch.object(Runner.callback_framework, "trigger", AsyncMock(return_value=None)):
            assert await client.connect(timeout=1) is False
        assert checked == [url]
        assert sent == ([url] if size == 8192 else [])
        error_args = errors.call_args.args
        assert error_args[1:3] == (f"size{size}-mcp", size)
        assert ("host_exit:url_size" in repr(error_args[-1])) is (size > 8192)
        if sent:
            assert len(sent[0]) == 8192
    finally:
        await _stop(client)


class TestSseDoLifecycleIntegration:
    @pytest.mark.asyncio
    async def test_do_connect_success_wires_session_and_clears_disconnected(self) -> None:
        """S5: The real _do_connect enters sse_client + ClientSession
        contexts, calls initialize(), and clears _is_disconnected on success."""
        state = _install_fake_mcp()
        try:
            client = _make_client()
            # Stub the auth trigger (keep the real framework so its module-level
            # @framework.on decorator still resolves); we only short-circuit
            # the TOOL_AUTH call so no real auth provider is consulted.
            with patch.object(Runner.callback_framework, "trigger", AsyncMock(return_value=None)):
                connected = await client.connect(timeout=NO_TIMEOUT)
            assert connected is True
            assert state["sse_client_kwargs"]["server_path"] == "http://test.local/sse"
            assert state["sse_client_kwargs"]["timeout"] == 60.0  # NO_TIMEOUT → 60s ceiling
            assert state["session_initialized"] is True
            assert client._session is not None
            assert client._read == "fake-read"
            assert client._write == "fake-write"
            assert client._is_disconnected is False
            await _stop(client)
        finally:
            state["patcher"].stop()

    @pytest.mark.asyncio
    async def test_do_connect_initialize_hung_is_bounded_and_returns_false(self) -> None:
        """S5/R2: A hung session.initialize() must be bounded by the
        defensive wait_for and surface as connect() == False after cleanup,
        not hang forever."""
        state = _install_fake_mcp(initialize_delay=10.0)
        try:
            client = _make_client()
            with patch.object(Runner.callback_framework, "trigger", AsyncMock(return_value=None)):
                connected = await client.connect(timeout=0.05)
            assert connected is False
            # Failed connect must not leave a live session behind.
            assert client._session is None
            await _stop(client)
        finally:
            state["patcher"].stop()

    @pytest.mark.asyncio
    async def test_do_disconnect_times_out_on_hung_aexit_and_resets_state(self) -> None:
        """S5: A hung __aexit__ on teardown is bounded by the 10s fallback;
        the test forces it via a short timeout and asserts state is reset and
        disconnect returns True (cleanup does not fail-fast)."""
        state = _install_fake_mcp(aexit_delay=10.0)
        try:
            client = _make_client()
            with patch.object(Runner.callback_framework, "trigger", AsyncMock(return_value=None)):
                await client.connect(timeout=NO_TIMEOUT)
                assert client._session is not None
                # Force the teardown to hit its timeout bound: we pass a tiny
                # explicit timeout so the hung __aexit__ is observable quickly
                # (rather than waiting out the 10s NO_TIMEOUT fallback).
                result = await client.disconnect(timeout=0.05)
            assert result is True
            assert state["session_aexit_called"] is True
            # Even though __aexit__ hung, the client state is reset.
            assert client._session is None
            assert client._client is None
            assert client._read is None
            assert client._write is None
            assert client._is_disconnected is True
            await _stop(client)
        finally:
            state["patcher"].stop()

    @pytest.mark.asyncio
    async def test_do_disconnect_after_failed_connect_resets_session_none(self) -> None:
        """S5: When _do_connect fails inside initialize(), the cleanup path
        runs _do_disconnect and must reset _session to None (no half-state)."""
        state = _install_fake_mcp(initialize_raises=RuntimeError("init blew up"))
        try:
            client = _make_client()
            with patch.object(Runner.callback_framework, "trigger", AsyncMock(return_value=None)):
                connected = await client.connect(timeout=NO_TIMEOUT)
            assert connected is False
            # The connect-failure cleanup path reset the session.
            assert client._session is None
            assert state["session_aexit_called"] is True or state["client_aexit_called"] is True
            await _stop(client)
        finally:
            state["patcher"].stop()
