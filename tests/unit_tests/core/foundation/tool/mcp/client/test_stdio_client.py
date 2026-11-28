# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""StdioClient must bound the handshake and recycle the child on timeout/cancel."""

import asyncio
import sys
import types
import unittest
from unittest.mock import AsyncMock, patch

from openjiuwen.core.foundation.tool import McpServerConfig
from openjiuwen.core.foundation.tool.mcp.base import NO_TIMEOUT
from openjiuwen.core.foundation.tool.mcp.client.stdio_client import StdioClient


class _Transport:
    async def __aenter__(self):
        return "reader", "writer"

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _HangingSession:
    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def initialize(self):
        await asyncio.sleep(30)

    async def list_tools(self):
        await asyncio.sleep(30)


class _ConnectedSession(_HangingSession):
    async def initialize(self):
        return None


class _TransportTimeoutSession(_ConnectedSession):
    async def initialize(self):
        raise TimeoutError("transport handshake timed out")

    async def list_tools(self):
        raise TimeoutError("transport list timed out")


def _config() -> McpServerConfig:
    return McpServerConfig(
        server_name="mubu",
        client_type="stdio",
        server_path="stdio://mubu",
        params={"command": "npx", "args": []},
    )


def _fake_mcp_modules(session_cls):
    mcp = types.ModuleType("mcp")
    mcp.ClientSession = session_cls
    mcp.StdioServerParameters = lambda **kwargs: types.SimpleNamespace(**kwargs)
    client = types.ModuleType("mcp.client")
    stdio = types.ModuleType("mcp.client.stdio")
    stdio.stdio_client = lambda params: _Transport()
    client.stdio = stdio
    return {"mcp": mcp, "mcp.client": client, "mcp.client.stdio": stdio}


class TestStdioClientTimeouts(unittest.IsolatedAsyncioTestCase):
    async def test_connect_times_out_and_disconnects(self):
        client = StdioClient(_config())
        disconnect = AsyncMock(wraps=client.disconnect)
        with (
            patch.dict(sys.modules, _fake_mcp_modules(_HangingSession)),
            patch.object(client, "_resolve_timeout", lambda timeout: 0.05),
            patch.object(client, "disconnect", new=disconnect),
        ):
            self.assertFalse(await client.connect())
        self.assertEqual(disconnect.await_count, 1)
        self.assertIsInstance(client._last_connect_error, TimeoutError)

    async def test_retry_timeout_closes_new_session(self):
        client = StdioClient(_config())
        with (
            patch.dict(sys.modules, _fake_mcp_modules(_HangingSession)),
            patch.object(client, "_resolve_timeout", lambda timeout: 0.01),
        ):
            self.assertFalse(await client.connect())
            self.assertFalse(await client.connect())
        self.assertTrue(client._is_disconnected)
        self.assertIsNone(client._session)
        self.assertIsNone(client._client)

    async def test_transport_handshake_timeout_keeps_original_error(self):
        client = StdioClient(_config())
        with patch.dict(sys.modules, _fake_mcp_modules(_TransportTimeoutSession)):
            self.assertFalse(await client.connect())
        self.assertEqual(str(client._last_connect_error), "transport handshake timed out")
        self.assertIsNone(client._session)

    async def test_cancel_during_connect_disconnects_and_reraises(self):
        client = StdioClient(_config())
        disconnect = AsyncMock(wraps=client.disconnect)
        with (
            patch.dict(sys.modules, _fake_mcp_modules(_HangingSession)),
            patch.object(client, "disconnect", new=disconnect),
        ):
            task = asyncio.create_task(client.connect())
            await asyncio.sleep(0.05)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertEqual(disconnect.await_count, 1)

    async def test_cancellation_group_during_connect_is_reraised(self):
        class _GroupSession(_HangingSession):
            async def initialize(self):
                raise BaseExceptionGroup("teardown", [asyncio.CancelledError()])

        client = StdioClient(_config())
        disconnect = AsyncMock(wraps=client.disconnect)
        with (
            patch.dict(sys.modules, _fake_mcp_modules(_GroupSession)),
            patch.object(client, "disconnect", new=disconnect),
        ):
            with self.assertRaises(BaseExceptionGroup):
                await client.connect()
        self.assertEqual(disconnect.await_count, 1)

    async def test_list_tools_times_out(self):
        client = StdioClient(_config())
        with patch.dict(sys.modules, _fake_mcp_modules(_ConnectedSession)):
            self.assertTrue(await client.connect())
        with patch.object(client, "_resolve_timeout", lambda timeout: 0.05):
            with self.assertRaises(TimeoutError):
                await client.list_tools()
        await client.disconnect()

    async def test_transport_list_timeout_keeps_original_error(self):
        client = StdioClient(_config())
        with patch.dict(sys.modules, _fake_mcp_modules(_ConnectedSession)):
            self.assertTrue(await client.connect())
        client._session = _TransportTimeoutSession()
        with self.assertRaisesRegex(TimeoutError, "transport list timed out"):
            await client.list_tools()
        await client.disconnect()

    def test_resolve_timeout_prefers_config_then_60s(self):
        client = StdioClient(_config())
        self.assertEqual(client._resolve_timeout(NO_TIMEOUT), 60.0)
        self.assertEqual(client._resolve_timeout(12.5), 12.5)
        configured = StdioClient(
            McpServerConfig(
                server_name="canva",
                client_type="stdio",
                server_path="stdio://canva",
                params={"command": "npx", "args": [], "timeout_s": 300},
            )
        )
        self.assertEqual(configured._resolve_timeout(NO_TIMEOUT), 300.0)
