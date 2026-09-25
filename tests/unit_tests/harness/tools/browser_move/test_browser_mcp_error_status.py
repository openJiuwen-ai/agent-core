#!/usr/bin/env python
# coding: utf-8
"""Preserve server failures across browser transport and helper boundaries.

The MCP session is replaced and global client registration is skipped because
the intended client is constructed explicitly. Connection setup is outside these
tests; extraction, MCPTool, batch adaptation, and the runtime rail run unchanged.
"""
# pylint: disable=protected-access

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from mcp.types import CallToolResult, TextContent

from openjiuwen.core.foundation.llm import ToolMessage
from openjiuwen.core.foundation.tool import McpServerConfig, McpToolCard
from openjiuwen.core.foundation.tool.mcp.base import MCPTool, McpToolResult
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext, ToolCallInputs
from openjiuwen.harness.tools.browser_move.clients.stdio_client import BrowserMoveStdioClient
from openjiuwen.harness.tools.browser_move.clients.streamable_http_client import BrowserMoveStreamableHttpClient
from openjiuwen.harness.tools.browser_move.playwright_runtime.config import BrowserRunGuardrails
from openjiuwen.harness.tools.browser_move.playwright_runtime.runtime import BrowserAgentRuntime, BrowserRuntimeRail
from openjiuwen.harness.tools.browser_move.playwright_runtime.runtime_tools import BrowserBatchInteractTool


@pytest.fixture(params=["stdio", "streamable-http"])
def browser_transport(request, monkeypatch):
    monkeypatch.setattr(
        "openjiuwen.harness.tools.browser_move.playwright_runtime.runtime.ensure_browser_runtime_client_patch",
        lambda: None,
    )
    config = McpServerConfig(
        server_id=f"browser-error-status-{request.param}",
        server_name="playwright-error-status",
        server_path="stdio://playwright" if request.param == "stdio" else "http://127.0.0.1:1/mcp",
        client_type=request.param,
    )
    client_type = BrowserMoveStdioClient if request.param == "stdio" else BrowserMoveStreamableHttpClient
    client = client_type(config)
    runtime = BrowserAgentRuntime(
        provider="openai",
        api_key="unused",
        api_base="https://example.invalid/v1",
        model_name="unused",
        mcp_cfg=config,
        guardrails=BrowserRunGuardrails(max_steps=3, max_failures=1, timeout_s=30, retry_once=False),
    )
    runtime.ensure_runtime_ready = AsyncMock()
    return client, runtime


def _native_wait_tool(client, *, is_error: bool, text: str | None) -> MCPTool:
    content = [] if text is None else [TextContent(type="text", text=text)]
    client._session = SimpleNamespace(
        call_tool=AsyncMock(return_value=CallToolResult(isError=is_error, content=content))
    )
    return MCPTool(
        client,
        McpToolCard(
            name="browser_wait_for",
            server_name="playwright-error-status",
            description="Wait for visible text",
            input_params={"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]},
        ),
    )


async def _after_tool_call(runtime, *, tool_name: str, result, rendered: str):
    rail = BrowserRuntimeRail(runtime)
    rail._status_logger = None
    inputs = ToolCallInputs(
        tool_name=tool_name,
        tool_result=result,
        tool_msg=ToolMessage(tool_call_id="browser-error-status", content=rendered),
    )
    await rail.after_tool_call(AgentCallbackContext(agent=None, inputs=inputs))
    return inputs


@pytest.mark.parametrize(
    ("is_error", "response_text"),
    [
        pytest.param(True, "### Error\nTimeoutError: Timeout 750ms exceeded.", id="playwright-timeout"),
        pytest.param(True, "Search could not be completed. Please try again.", id="unprefixed-error"),
        pytest.param(True, None, id="empty-error-content"),
        pytest.param(False, "### Result\nWaited for New results", id="successful-response"),
    ],
)
def test_mcp_status_survives_browser_client_wrapper_and_rail(
    browser_transport,
    is_error: bool,
    response_text: str | None,
) -> None:
    client, runtime = browser_transport
    native_tool = _native_wait_tool(client, is_error=is_error, text=response_text)

    async def exercise():
        result = await native_tool.invoke({"text": "New results"})
        inputs = await _after_tool_call(
            runtime,
            tool_name="mcp_playwright-error-status_browser_wait_for",
            result=result,
            rendered=native_tool.render_for_llm(result),
        )
        return result, inputs

    result, inputs = asyncio.run(exercise())
    outcome = runtime.classify_tool_result(result)
    assert outcome["success"] is not is_error
    assert inputs.tool_msg.metadata["success"] is not is_error
    assert inputs.tool_msg.metadata["executed"] is True
    if is_error:
        expected_error = response_text or "MCP tool reported an error."
        assert isinstance(result, McpToolResult)
        assert result.success is False
        assert result.data == {"result": response_text}
        assert result.error == expected_error
        assert outcome["error"] == expected_error
        assert inputs.tool_result["ok"] is False
        assert inputs.tool_result["error"] == expected_error
    else:
        assert result == {"result": response_text}
        assert native_tool.render_for_llm(result) == response_text
    client._session.call_tool.assert_awaited_once_with("browser_wait_for", arguments={"text": "New results"})


@pytest.mark.parametrize("is_error", [True, False], ids=["timeout", "successful-wait"])
def test_single_batch_wait_preserves_native_outcome(browser_transport, is_error: bool) -> None:
    client, runtime = browser_transport
    response_text = (
        "### Error\nTimeoutError: Timeout 750ms exceeded." if is_error else "### Result\nWaited for New results"
    )
    native_tool = _native_wait_tool(client, is_error=is_error, text=response_text)
    runtime._get_playwright_mcp_tool = AsyncMock(return_value=native_tool)
    helper = BrowserBatchInteractTool(runtime)

    async def exercise():
        result = await helper.invoke({"generation_id": "g0", "steps": [{"op": "wait_for_text", "text": "New results"}]})
        inputs = await _after_tool_call(
            runtime,
            tool_name="browser_batch_interact",
            result=result,
            rendered=helper.render_for_llm(result),
        )
        return result, inputs

    result, inputs = asyncio.run(exercise())
    assert result.success is not is_error
    assert result.data["ok"] is not is_error
    assert result.data["status"] == ("failed" if is_error else "completed")
    assert result.data["conditions"][0]["ok"] is not is_error
    assert inputs.tool_msg.metadata["success"] is not is_error
    assert result.error == (response_text if is_error else None)
    if is_error:
        assert inputs.tool_result["error"] == response_text
    client._session.call_tool.assert_awaited_once_with("browser_wait_for", arguments={"text": "New results"})
