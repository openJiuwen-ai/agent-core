#!/usr/bin/env python
# coding: utf-8
# pylint: disable=protected-access

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock

from openjiuwen.core.foundation.tool import McpServerConfig
from openjiuwen.core.runner import Runner
from openjiuwen.harness.tools.browser_move.playwright_runtime.config import BrowserRunGuardrails
from openjiuwen.harness.tools.browser_move.playwright_runtime.probes import (
    build_interactive_probe_js,
)
from openjiuwen.harness.tools.browser_move.playwright_runtime.runtime import (
    BrowserAgentRuntime,
)
from openjiuwen.harness.tools.browser_move.playwright_runtime.runtime_tools import (
    BrowserProbeInteractivesTool,
)


def _run(coro):
    return asyncio.run(coro)


def _make_runtime() -> BrowserAgentRuntime:
    mcp_cfg = McpServerConfig(
        server_id="test-playwright-runtime",
        server_name="test-playwright-runtime",
        server_path="stdio://playwright",
        client_type="stdio",
        params={"cwd": str(Path.cwd())},
    )

    return BrowserAgentRuntime(
        provider="openai",
        api_key="test-key",
        api_base="https://example.invalid/v1",
        model_name="test-model",
        mcp_cfg=mcp_cfg,
        guardrails=BrowserRunGuardrails(
            max_steps=3,
            max_failures=1,
            timeout_s=30,
            retry_once=False,
        ),
    )


def test_build_interactive_probe_js_contains_high_value_selectors() -> None:
    js = build_interactive_probe_js(max_items=25, viewport_only=True)

    assert "button" in js
    assert "a[href]" in js
    assert "input" in js
    assert "[aria-label]" in js
    assert "[data-testid]" in js
    assert "max_items" in js
    assert "viewport_only" in js


def test_build_interactive_probe_js_clamps_max_items() -> None:
    js = build_interactive_probe_js(max_items=999, viewport_only=True)

    assert '"max_items": 100' in js


def test_browser_probe_interactives_tool_invokes_runtime_api() -> None:
    runtime = _make_runtime()
    runtime.probe_interactives = AsyncMock(
        return_value={
            "ok": True,
            "elements": [
                {
                    "id": "e1",
                    "role": "button",
                    "text": "Add to cart",
                    "selector_hint": "button:nth-of-type(1)",
                }
            ],
            "error": None,
        }
    )

    tool = BrowserProbeInteractivesTool(runtime, language="en")

    result = _run(
        tool.invoke(
            {
                "max_items": 200,
                "viewport_only": "false",
                "query": "cart",
            }
        )
    )

    runtime.probe_interactives.assert_called_once_with(
        max_items=100,
        viewport_only=False,
        query="cart",
    )
    assert result.success is True
    assert result.data["elements"][0]["text"] == "Add to cart"


def test_browser_probe_interactives_tool_reports_runtime_error() -> None:
    runtime = _make_runtime()
    runtime.probe_interactives = AsyncMock(
        return_value={
            "ok": False,
            "error": "browser_code_executor_not_ready",
            "elements": [],
        }
    )

    tool = BrowserProbeInteractivesTool(runtime, language="en")

    result = _run(tool.invoke({}))

    assert result.success is False
    assert result.error == "browser_code_executor_not_ready"
    assert result.data["elements"] == []


def test_runtime_probe_interactives_uses_code_executor_and_parses_json() -> None:
    runtime = _make_runtime()
    runtime.ensure_runtime_ready = AsyncMock()
    runtime._code_executor = AsyncMock(
        return_value={
            "ok": True,
            "url": "https://example.com",
            "title": "Example",
            "elements": [
                {
                    "id": "e1",
                    "role": "button",
                    "text": "Search",
                    "selector_hint": "button:nth-of-type(1)",
                }
            ],
        }
    )

    result = _run(
        runtime.probe_interactives(
            max_items=10,
            viewport_only=True,
            query="search",
        )
    )

    runtime.ensure_runtime_ready.assert_called_once()
    runtime._code_executor.assert_called_once()
    assert result["ok"] is True
    assert result["url"] == "https://example.com"
    assert result["elements"][0]["text"] == "Search"


def test_runtime_probe_interactives_handles_missing_code_executor() -> None:
    runtime = _make_runtime()
    runtime.ensure_runtime_ready = AsyncMock()
    runtime._code_executor = None

    result = _run(runtime.probe_interactives())

    assert result["ok"] is False
    assert result["error"] == "browser_code_executor_not_ready"
    assert result["elements"] == []


def test_runtime_playwright_client_lookup_keys_include_server_name_variants() -> None:
    runtime = _make_runtime()
    runtime._service.mcp_cfg.server_id = "playwright_official_stdio"
    runtime._service.mcp_cfg.server_name = "playwright-official"

    keys = runtime._playwright_client_lookup_keys()

    assert "playwright_official_stdio" in keys
    assert "playwright-official" in keys
    assert "playwright_official" in keys
    assert "playwright" in keys


def test_runtime_unwrap_mcp_text_result() -> None:
    runtime = _make_runtime()

    raw = {
        "content": [
            {
                "type": "text",
                "text": '{"ok": true, "elements": []}',
            }
        ]
    }

    assert runtime._unwrap_mcp_text_result(raw) == '{"ok": true, "elements": []}'

def test_runtime_call_playwright_run_code_unsafe_uses_runner_mcp_tool(monkeypatch) -> None:
    runtime = _make_runtime()

    class FakeToolResult:
        success = True
        error = None
        data = {
            "content": [
                {
                    "type": "text",
                    "text": '{"ok": true, "elements": []}',
                }
            ]
        }

    class FakeTool:
        def __init__(self):
            self.inputs = None

        async def invoke(self, inputs):
            self.inputs = inputs
            return FakeToolResult()

    fake_tool = FakeTool()

    async def fake_get_mcp_tool(**kwargs):
        if kwargs.get("name") == "browser_run_code_unsafe":
            return [fake_tool]
        return []

    monkeypatch.setattr(
        Runner.resource_mgr,
        "get_mcp_tool",
        fake_get_mcp_tool,
    )

    result = _run(
        runtime._call_playwright_run_code_unsafe(
            "async (page) => ({ok: true})"
        )
    )

    assert fake_tool.inputs == {
        "code": "async (page) => ({ok: true})"
    }
    assert result["content"][0]["text"] == '{"ok": true, "elements": []}'