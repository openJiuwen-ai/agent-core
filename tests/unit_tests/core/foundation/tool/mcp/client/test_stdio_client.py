# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""StdioClient must bound the handshake and recycle the child on timeout/cancel."""

import asyncio
import sys
import types
import unittest
from unittest.mock import AsyncMock, patch

from openjiuwen.core.foundation.tool import McpServerConfig, McpTimeoutError
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


class _HangingExitStack:
    def __init__(self):
        self.entered = asyncio.Event()

    async def aclose(self):
        self.entered.set()
        await asyncio.Event().wait()


class _RetryableExitStack:
    def __init__(self):
        self.calls = 0

    async def aclose(self):
        self.calls += 1
        if self.calls == 1:
            await asyncio.Event().wait()


class _WatchdogRaceExitStack:
    def __init__(self):
        self.entered = asyncio.Event()

    async def aclose(self):
        self.entered.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            await asyncio.Event().wait()


class _CancelScopeExitStack:
    async def aclose(self):
        raise RuntimeError("Attempted to exit cancel scope in a different task")


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
            with self.assertRaises(McpTimeoutError):
                await client.connect()
        self.assertEqual(disconnect.await_count, 1)
        self.assertIsInstance(client._last_connect_error, TimeoutError)

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

    async def test_disconnect_times_out_without_caller_cancellation(self):
        client = StdioClient(_config())
        stack = _HangingExitStack()
        client._exit_stack = stack

        result = await client.disconnect(timeout=0.05)

        self.assertFalse(result)
        self.assertTrue(stack.entered.is_set())
        self.assertFalse(client._is_disconnected)
        self.assertIsNotNone(client._exit_stack)

    async def test_disconnect_watchdog_preserves_preexisting_cancellation_count(self):
        client = StdioClient(_config())
        client._exit_stack = _HangingExitStack()
        task = asyncio.current_task()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await asyncio.sleep(0)
        cancelling_at_start = task.cancelling()

        result = await client.disconnect(timeout=0.05)

        self.assertFalse(result)
        self.assertEqual(task.cancelling(), cancelling_at_start)

    async def test_disconnect_reraises_cancellation_after_watchdog_fires(self):
        client = StdioClient(_config())
        stack = _WatchdogRaceExitStack()
        client._exit_stack = stack
        task = asyncio.create_task(client.disconnect(timeout=0.05))
        await stack.entered.wait()
        await asyncio.sleep(0.1)

        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task

    async def test_disconnect_reraises_caller_cancellation(self):
        client = StdioClient(_config())
        stack = _HangingExitStack()
        client._exit_stack = stack
        task = asyncio.create_task(client.disconnect(timeout=30.0))
        await stack.entered.wait()

        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task

    async def test_disconnect_does_not_reenter_failed_transport_context(self):
        client = StdioClient(_config())
        client._exit_stack = _CancelScopeExitStack()
        transport = AsyncMock()
        client._client = transport

        result = await client.disconnect(timeout=1.0)

        self.assertFalse(result)
        transport.__aexit__.assert_not_awaited()
        self.assertFalse(client._is_disconnected)

    async def test_failed_disconnect_can_be_retried_on_same_task(self):
        client = StdioClient(_config())
        stack = _RetryableExitStack()
        client._exit_stack = stack

        self.assertFalse(await client.disconnect(timeout=0.05))
        self.assertFalse(client._is_disconnected)
        self.assertTrue(await client.disconnect(timeout=1.0))
        self.assertEqual(stack.calls, 2)
        self.assertTrue(client._is_disconnected)

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
