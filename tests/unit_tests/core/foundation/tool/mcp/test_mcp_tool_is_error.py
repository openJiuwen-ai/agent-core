# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""MCP ``isError`` must survive the client -> tool -> harness round trip.

Before this, ``call_tool`` flattened ``CallToolResult`` to plain text and the
flag was dropped, so a failing MCP tool looked like a success everywhere above.
"""
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from openjiuwen.core.foundation.tool import McpToolCard
from openjiuwen.core.foundation.tool.mcp.base import (
    MCPTool,
    extract_mcp_tool_result,
    extract_mcp_tool_result_content,
)
from openjiuwen.core.foundation.tool.mcp.client.streamable_http_client import (
    StreamableHttpClient,
)


def _result(text: str, *, is_error: bool = False) -> SimpleNamespace:
    return SimpleNamespace(content=[SimpleNamespace(text=text)], isError=is_error)


def test_extract_mcp_tool_result_keeps_is_error_flag() -> None:
    assert extract_mcp_tool_result(_result("ok")) == ("ok", False)
    assert extract_mcp_tool_result(_result("boom", is_error=True)) == ("boom", True)


def test_extract_mcp_tool_result_content_still_returns_plain_content() -> None:
    assert extract_mcp_tool_result_content(_result("boom", is_error=True)) == "boom"


def _tool_card(name: str = "apply_ops") -> McpToolCard:
    return McpToolCard(
        id=f"srv.srv.{name}",
        name=name,
        description=f"{name} tool",
        input_params={"type": "object", "properties": {}},
        server_name="srv",
        server_id="srv",
    )


class _MetaClient:
    """Client that only implements the new optional contract."""

    def __init__(self, payload: str, is_error: bool) -> None:
        self.payload = payload
        self.is_error = is_error
        self.calls: list[dict[str, Any]] = []

    async def call_tool_with_meta(self, tool_name: str, arguments: dict, **kwargs):
        self.calls.append({"tool_name": tool_name, "arguments": arguments})
        return self.payload, self.is_error


class _LegacyClient:
    """Client that only implements the historical ``call_tool`` contract."""

    def __init__(self, payload: str) -> None:
        self.payload = payload

    async def call_tool(self, tool_name: str, arguments: dict, **kwargs):
        return self.payload


@pytest.mark.asyncio
async def test_invoke_marks_tool_level_failure() -> None:
    client = _MetaClient('{"applied": false, "status": "rejected"}', is_error=True)
    tool = MCPTool(client, _tool_card())

    output = await tool.invoke({})

    assert output == {"result": '{"applied": false, "status": "rejected"}', "is_error": True}
    assert client.calls == [{"tool_name": "apply_ops", "arguments": {}}]


@pytest.mark.asyncio
async def test_invoke_does_not_add_is_error_on_success() -> None:
    tool = MCPTool(_MetaClient('{"applied": true}', is_error=False), _tool_card())

    assert await tool.invoke({}) == {"result": '{"applied": true}'}


@pytest.mark.asyncio
async def test_invoke_falls_back_for_clients_without_meta_api() -> None:
    tool = MCPTool(_LegacyClient("legacy payload"), _tool_card())

    assert await tool.invoke({}) == {"result": "legacy payload"}


@pytest.mark.asyncio
async def test_streamable_http_client_exposes_is_error() -> None:
    client = StreamableHttpClient.__new__(StreamableHttpClient)
    client._session = SimpleNamespace(
        call_tool=AsyncMock(return_value=_result("MCP error -32602: invalid params", is_error=True))
    )

    content, is_error = await client.call_tool_with_meta("apply_ops", {})

    assert content == "MCP error -32602: invalid params"
    assert is_error is True


@pytest.mark.asyncio
async def test_streamable_http_call_tool_stays_text_only() -> None:
    client = StreamableHttpClient.__new__(StreamableHttpClient)
    client._session = SimpleNamespace(call_tool=AsyncMock(return_value=_result("done")))

    assert await client.call_tool("apply_ops", {}) == "done"


@pytest.mark.asyncio
async def test_subclass_override_of_call_tool_is_not_bypassed() -> None:
    """Subclasses (e.g. browser-move) re-implement ``call_tool``; keep their code path."""

    class _OverridingClient(StreamableHttpClient):
        def __init__(self) -> None:  # no connection needed for this contract check
            self.calls: list[str] = []

        async def call_tool(self, tool_name: str, arguments: dict, **kwargs):
            self.calls.append(tool_name)
            return "from overridden call_tool"

    client = _OverridingClient()
    tool = MCPTool(client, _tool_card())

    assert await tool.invoke({}) == {"result": "from overridden call_tool"}
    assert client.calls == ["apply_ops"]
