# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Reproducers for issue #1976: call_tool's ``timeout`` parameter must be
forwarded to the underlying MCP session for every transport client.

The framework's MCP clients declared a ``timeout`` keyword on ``call_tool``
but never used it, so minute-long tool calls (e.g. simulation solves) were
cut off by the connect-level HTTP timeout even when the caller asked for a
larger per-call budget. The SSE client already forwards the timeout via
``asyncio.wait_for``; these tests pin the same contract for the other
transports: an explicit timeout bounds the call, ``NO_TIMEOUT`` leaves it
unbounded, and ``MCPTool.invoke`` forwards the caller's timeout.
"""
import asyncio
import unittest
from types import SimpleNamespace
from typing import Optional

from openjiuwen.core.foundation.tool import McpServerConfig, McpToolCard
from openjiuwen.core.foundation.tool.mcp.base import NO_TIMEOUT, MCPTool
from openjiuwen.core.foundation.tool.mcp.client.playwright_client import PlaywrightClient
from openjiuwen.core.foundation.tool.mcp.client.stdio_client import StdioClient
from openjiuwen.core.foundation.tool.mcp.client.streamable_http_client import (
    StreamableHttpClient,
)


class _RecordingSession:
    """Fake ``ClientSession`` that optionally hangs, recording the call."""

    def __init__(self, delay: float = 0.0):
        self._delay = delay
        self.called: bool = False

    async def call_tool(self, tool_name, arguments=None, **kwargs):
        self.called = True
        if self._delay:
            await asyncio.sleep(self._delay)
        return SimpleNamespace(content=[SimpleNamespace(text="ok")])


class _RaisingTimeoutSession:
    """Fake session whose call raises a TimeoutError from below (SDK/read timeout)."""

    def __init__(self, message: str = "underlying MCP read timeout"):
        self._message = message
        self.called: bool = False

    async def call_tool(self, tool_name, arguments=None, **kwargs):
        self.called = True
        raise TimeoutError(self._message)


def _make_config(server_name: str, server_path: str, client_type: str) -> McpServerConfig:
    return McpServerConfig(
        server_name=server_name,
        server_path=server_path,
        client_type=client_type,
    )


class TestCallToolTimeoutForwarding(unittest.IsolatedAsyncioTestCase):
    async def test_streamable_http_explicit_timeout_bounds_hung_call(self):
        client = StreamableHttpClient(
            _make_config("streamable-server", "http://127.0.0.1:8930/mcp", "streamable-http")
        )
        client._session = _RecordingSession(delay=10)

        with self.assertRaises(TimeoutError):
            await client.call_tool("solve", {}, timeout=0.05)

    async def test_stdio_explicit_timeout_bounds_hung_call(self):
        client = StdioClient(_make_config("stdio-server", "python server.py", "stdio"))
        client._session = _RecordingSession(delay=10)

        with self.assertRaises(TimeoutError):
            await client.call_tool("solve", {}, timeout=0.05)

    async def test_playwright_explicit_timeout_bounds_hung_call(self):
        client = PlaywrightClient(
            _make_config("playwright-server", "http://localhost:9222", "playwright")
        )
        client._session = _RecordingSession(delay=10)

        with self.assertRaises(TimeoutError):
            await client.call_tool("solve", {}, timeout=0.05)

    async def _assert_completes(self, client) -> None:
        client._session = _RecordingSession(delay=0.01)
        result = await client.call_tool("solve", {}, timeout=5.0)
        self.assertEqual(result, "ok")

    async def test_streamable_http_completes_within_explicit_timeout(self):
        client = StreamableHttpClient(
            _make_config("streamable-server", "http://127.0.0.1:8930/mcp", "streamable-http")
        )
        await self._assert_completes(client)

    async def test_stdio_completes_within_explicit_timeout(self):
        client = StdioClient(_make_config("stdio-server", "python server.py", "stdio"))
        await self._assert_completes(client)

    async def test_playwright_completes_within_explicit_timeout(self):
        client = PlaywrightClient(
            _make_config("playwright-server", "http://localhost:9222", "playwright")
        )
        await self._assert_completes(client)

    async def test_no_timeout_does_not_cut_slow_call(self):
        """With the default NO_TIMEOUT a slower-than-usual call must still
        finish: no hidden per-call ceiling may be installed (matches
        StreamableHttpClient and the pre-fix stdio/playwright behavior)."""
        for client in (
            StdioClient(_make_config("stdio-server", "python server.py", "stdio")),
            PlaywrightClient(_make_config("playwright-server", "http://localhost:9222", "playwright")),
        ):
            with self.subTest(client=client.__client_name__):
                client._session = _RecordingSession(delay=0.1)
                result = await client.call_tool("solve", {})
                self.assertEqual(result, "ok")

    async def test_configured_timeout_s_bounds_call_without_explicit_timeout(self):
        """``params["timeout_s"]`` bounds the call when the caller did not
        pass an explicit timeout (issue #1976: config path must work too)."""
        for client_type, client_cls in (("stdio", StdioClient), ("playwright", PlaywrightClient)):
            with self.subTest(client=client_type):
                config = McpServerConfig(
                    server_name="cfg-server",
                    server_path="python server.py" if client_type == "stdio" else "http://localhost:9222",
                    client_type=client_type,
                    params={"timeout_s": 0.05},
                )
                client = client_cls(config)
                client._session = _RecordingSession(delay=10)
                with self.assertRaises(TimeoutError):
                    await client.call_tool("solve", {})

    async def test_unbounded_call_preserves_underlying_timeout_error(self):
        """With ``NO_TIMEOUT`` and no configured ``timeout_s`` (bound=None),
        a ``TimeoutError`` raised by the underlying session/SDK must surface
        as a ``TimeoutError`` (review finding: the old message formatting
        turned None into a TypeError and masked the original error)."""
        for client_type, client_cls in (("stdio", StdioClient), ("playwright", PlaywrightClient)):
            with self.subTest(client=client_type):
                client = client_cls(
                    _make_config(
                        "ub-server",
                        "python server.py" if client_type == "stdio" else "http://localhost:9222",
                        client_type,
                    )
                )
                client._session = _RaisingTimeoutSession("underlying MCP read timeout")

                with self.assertRaises(TimeoutError) as ctx:
                    await client.call_tool("solve", {})

                message = str(ctx.exception)
                self.assertIn("underlying MCP read timeout", message)
                self.assertNotIn("None", message)

    async def test_mcp_tool_invoke_forwards_timeout_kwarg(self):
        """MCPTool.invoke must pass the caller's ``timeout`` through to the
        client's call_tool (issue #1976 breakpoint 1)."""
        received: Optional[dict] = None

        class _RecordingClient:
            async def call_tool(self, tool_name, arguments, *, timeout=NO_TIMEOUT):
                nonlocal received
                received = {"tool_name": tool_name, "timeout": timeout}
                return "done"

        card = McpToolCard(
            name="solve",
            description="",
            input_params={"type": "object", "properties": {}},
            server_name="sim-server",
        )
        tool = MCPTool(_RecordingClient(), card)

        result = await tool.invoke({}, timeout=300.0)

        self.assertEqual(result, {"result": "done"})
        self.assertEqual(received, {"tool_name": "solve", "timeout": 300.0})


if __name__ == "__main__":
    unittest.main()
