# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Playwright MCP transport follows the common timeout and cancellation contract."""

import asyncio
import sys
import types
from unittest.mock import patch

import pytest

from openjiuwen.core.foundation.tool import McpServerConfig, McpTimeoutError
from openjiuwen.core.foundation.tool.mcp.base import NO_TIMEOUT
from openjiuwen.core.foundation.tool.mcp.client.playwright_client import PlaywrightClient


def _config() -> McpServerConfig:
    return McpServerConfig(
        server_name="playwright-test",
        client_type="playwright",
        server_path="http://playwright.test/sse",
    )


def _fake_modules(session_cls, *, transport_raises=None) -> dict[str, types.ModuleType]:
    class FakeStdioServerParameters:
        pass

    class FakeTransport:
        async def __aenter__(self):
            if transport_raises is not None:
                raise transport_raises
            return "reader", "writer"

        async def __aexit__(self, exc_type, exc, tb):
            return False

    fake_mcp = types.ModuleType("mcp")
    fake_mcp.ClientSession = session_cls
    fake_mcp.StdioServerParameters = FakeStdioServerParameters
    fake_client = types.ModuleType("mcp.client")
    fake_stdio = types.ModuleType("mcp.client.stdio")
    fake_stdio.stdio_client = lambda params: FakeTransport()
    fake_sse = types.ModuleType("mcp.client.sse")
    fake_sse.sse_client = lambda path: FakeTransport()
    fake_client.stdio = fake_stdio
    fake_client.sse = fake_sse
    return {
        "mcp": fake_mcp,
        "mcp.client": fake_client,
        "mcp.client.stdio": fake_stdio,
        "mcp.client.sse": fake_sse,
    }


class _IdleSession:
    async def list_tools(self):
        return types.SimpleNamespace(tools=[])

    async def call_tool(self, tool_name, arguments):
        return types.SimpleNamespace(content=[])


@pytest.mark.asyncio
async def test_no_timeout_default_does_not_install_a_deadline() -> None:
    client = PlaywrightClient(_config())
    client._session = _IdleSession()

    with patch("openjiuwen.core.foundation.tool.mcp.client.playwright_client.asyncio.timeout") as deadline:
        deadline.return_value.__aenter__.return_value = None
        deadline.return_value.__aexit__.return_value = None
        await client.list_tools(timeout=NO_TIMEOUT)
        await client.call_tool("browser_navigate", {}, timeout=NO_TIMEOUT)

    assert [call.args for call in deadline.call_args_list] == [(None,), (None,)]


class _HangingSession:
    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def initialize(self):
        await asyncio.sleep(30)


@pytest.mark.asyncio
async def test_connect_timeout_raises_typed_error_and_cleans_up() -> None:
    client = PlaywrightClient(_config())
    with patch.dict(sys.modules, _fake_modules(_HangingSession)):
        with pytest.raises(McpTimeoutError) as exc_info:
            await client.connect(timeout=0.05)

    assert exc_info.value.params["operation"] == "connect"
    assert client._session is None
    assert client._last_connect_error is exc_info.value


@pytest.mark.asyncio
async def test_transport_failure_returns_false_with_original_reason() -> None:
    client = PlaywrightClient(_config())
    with patch.dict(
        sys.modules,
        _fake_modules(_HangingSession, transport_raises=RuntimeError("startup failed")),
    ):
        connected = await client.connect(timeout=0.05)

    assert connected is False
    assert isinstance(client._last_connect_error, RuntimeError)
    assert str(client._last_connect_error) == "startup failed"


@pytest.mark.asyncio
async def test_caller_cancellation_is_not_converted_to_timeout() -> None:
    client = PlaywrightClient(_config())
    with patch.dict(sys.modules, _fake_modules(_HangingSession)):
        task = asyncio.create_task(client.connect(timeout=30))
        await asyncio.sleep(0.02)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    assert not isinstance(client._last_connect_error, McpTimeoutError)
