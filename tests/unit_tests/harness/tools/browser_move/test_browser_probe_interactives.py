#!/usr/bin/env python
# coding: utf-8

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock

from openjiuwen.core.foundation.tool import McpServerConfig
from openjiuwen.core.runner import Runner
from openjiuwen.harness.tools.browser_move.playwright_runtime.config import BrowserRunGuardrails
from openjiuwen.harness.tools.browser_move.playwright_runtime.page_structure_index import (
    build_page_index_install_js,
)
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


def test_build_interactive_probe_js_uses_shared_page_index() -> None:
    install_js = build_page_index_install_js()
    query_js = build_interactive_probe_js(max_items=25, viewport_only=True)

    assert "STATE_KEY = '__openjiuwenPageStructureIndexV3'" in install_js
    assert "document.createTreeWalker" in install_js
    assert "index.interactiveIds" in install_js
    assert "cache_hit" in install_js
    assert "nodes_indexed" in install_js
    assert "page_index_runtime_missing" in query_js
    assert "document.createTreeWalker" not in query_js
    assert '"max_items":25' in query_js
    assert '"viewport_only":true' in query_js


def test_build_interactive_probe_js_queries_indexed_semantics_and_group_context() -> None:
    install_js = build_page_index_install_js()
    query_js = build_interactive_probe_js(max_items=25, viewport_only=True, query="search")

    assert "queryAliases" in install_js
    assert "queryInteractives" in install_js
    assert "action_likelihood" in install_js
    assert "findContainingGroup" in install_js
    assert "group_context" in install_js
    assert "INTERACTIVE_ROLES" in install_js
    assert "node.interactive" in install_js
    assert "搜索" in install_js
    assert "关键词" in install_js
    assert '"query":"search"' in query_js


def test_build_interactive_probe_js_clamps_max_items() -> None:
    js = build_interactive_probe_js(max_items=999, viewport_only=True)

    assert '"max_items":100' in js


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
        scope_group_id="",
        scope_item_index=None,
    )
    assert result.success is True
    assert result.data["elements"][0]["text"] == "Add to cart"


def test_browser_probe_interactives_tool_passes_group_scope() -> None:
    runtime = _make_runtime()
    runtime.probe_interactives = AsyncMock(
        return_value={"ok": True, "elements": [], "error": None}
    )
    tool = BrowserProbeInteractivesTool(runtime, language="en")

    result = _run(
        tool.invoke(
            {
                "query": "Soumission",
                "scope_group_id": "group_books",
                "scope_item_index": 2,
            }
        )
    )

    runtime.probe_interactives.assert_called_once_with(
        max_items=50,
        viewport_only=True,
        query="Soumission",
        scope_group_id="group_books",
        scope_item_index=2,
    )
    assert result.success is True


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
    runtime._page_index_runtime_installed = True
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


def test_runtime_probe_installs_page_index_once_before_first_query() -> None:
    runtime = _make_runtime()
    runtime.ensure_runtime_ready = AsyncMock()
    runtime._code_executor = AsyncMock(
        side_effect=[
            {
                "ok": True,
                "already_installed": False,
                "schema_version": 3,
            },
            {
                "ok": True,
                "elements": [],
                "page_index": {"cache_hit": False},
            },
        ]
    )

    result = _run(runtime.probe_interactives())

    assert result["ok"] is True
    assert runtime._page_index_runtime_installed is True
    assert runtime._code_executor.await_count == 2
    install_code = runtime._code_executor.await_args_list[0].args[0]
    query_code = runtime._code_executor.await_args_list[1].args[0]
    assert "window[RUNTIME_KEY]" in install_code
    assert "document.createTreeWalker" in install_code
    assert "page_index_runtime_missing" in query_code
    assert "document.createTreeWalker" not in query_code


def test_runtime_probe_reinstalls_page_index_after_navigation_reset() -> None:
    runtime = _make_runtime()
    runtime.ensure_runtime_ready = AsyncMock()
    runtime._page_index_runtime_installed = True
    runtime._code_executor = AsyncMock(
        side_effect=[
            {"ok": False, "error": "page_index_runtime_missing"},
            {
                "ok": True,
                "already_installed": False,
                "schema_version": 3,
            },
            {
                "ok": True,
                "elements": [{"id": "interactive_1", "role": "button"}],
            },
        ]
    )

    result = _run(runtime.probe_interactives())

    assert result["ok"] is True
    assert result["elements"][0]["role"] == "button"
    assert runtime._code_executor.await_count == 3
    assert runtime._page_index_runtime_installed is True
