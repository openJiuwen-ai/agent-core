#!/usr/bin/env python
# coding: utf-8
"""Integration tests for MCP enforcement on the live BrowserRuntimeRail path."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from unittest.mock import MagicMock

from openjiuwen.core.single_agent.rail.base import AgentCallbackContext, ToolCallInputs
from openjiuwen.harness.tools.browser_move.playwright_runtime.mcp_usage_limiter import (
    BrowserMcpUsageLimiter,
)
from openjiuwen.harness.tools.browser_move.playwright_runtime.runtime import (
    BrowserAgentRuntime,
    BrowserRuntimeRail,
)


@dataclass
class _ToolCall:
    name: str
    arguments: object
    id: str = "browser-tool-call"


def _run(coro):
    return asyncio.run(coro)


def _runtime_rail() -> BrowserRuntimeRail:
    runtime = MagicMock(spec=BrowserAgentRuntime)
    runtime.service = MagicMock()
    return BrowserRuntimeRail(runtime)


def _tool_context(
    tool_name: str,
    tool_args: dict,
    *,
    tool_result=None,
) -> AgentCallbackContext:
    call = _ToolCall(name=tool_name, arguments=tool_args)
    return AgentCallbackContext(
        agent=MagicMock(),
        inputs=ToolCallInputs(
            tool_call=call,
            tool_name=tool_name,
            tool_args=tool_args,
            tool_result=tool_result,
        ),
    )


def test_runtime_rail_exposes_shared_limiter() -> None:
    rail = _runtime_rail()

    assert isinstance(rail.mcp_usage_limiter, BrowserMcpUsageLimiter)


def test_runtime_rail_blocks_raw_fallback_after_semantic_failure() -> None:
    rail = _runtime_rail()
    semantic_ctx = _tool_context(
        "browser_select_dropdown_option",
        {
            "field_selector": "select.js-example-basic-multiple",
            "option_text": "Atlantis",
            "exact": True,
        },
        tool_result={
            "ok": False,
            "error": "dropdown_option_not_found",
            "field_kind": "native_select_multiple",
            "resolved_field_selector": "select.js-example-basic-multiple",
            "target_family": "semantic_dropdown:https://select2.org/:select.js-example-basic-multiple",
        },
    )
    _run(rail.after_tool_call(semantic_ctx))

    raw_ctx = _tool_context(
        "mcp_playwright-official_browser_select_option",
        {
            "target": "select.js-example-basic-multiple",
            "values": ["AL"],
        },
    )
    _run(rail.before_tool_call(raw_ctx))

    assert raw_ctx.extra["_skip_tool"] is True
    assert raw_ctx.inputs.tool_result["ok"] is False
    assert raw_ctx.inputs.tool_result["reason"] == "raw_fallback_after_semantic_widget_failure"
    assert "mcp_usage_limited" in raw_ctx.inputs.tool_result["error"]
    assert raw_ctx.inputs.tool_result["target_family"].startswith("semantic_dropdown:")
    assert raw_ctx.inputs.tool_msg.tool_call_id == "browser-tool-call"


def test_runtime_rail_blocks_third_identical_semantic_no_progress_call() -> None:
    rail = _runtime_rail()
    args = {
        "field_selector": "select.js-example-basic-single",
        "option_text": "Wyoming",
        "exact": True,
    }
    first = _tool_context(
        "browser_select_dropdown_option",
        args,
        tool_result={
            "ok": True,
            "verified": True,
            "selected_value": "WY",
            "selected_text": "Wyoming",
            "added_values": ["WY"],
        },
    )
    _run(rail.after_tool_call(first))

    second = _tool_context(
        "browser_select_dropdown_option",
        args,
        tool_result={
            "ok": True,
            "verified": True,
            "selected_value": "WY",
            "selected_text": "Wyoming",
            "added_values": ["WY"],
        },
    )
    _run(rail.before_tool_call(second))
    assert "_skip_tool" not in second.extra
    _run(rail.after_tool_call(second))

    third = _tool_context("browser_select_dropdown_option", args)
    _run(rail.before_tool_call(third))

    assert third.extra["_skip_tool"] is True
    assert third.inputs.tool_result["reason"] == "semantic_no_progress_limited"
    assert "semantic_no_progress_limited" in third.inputs.tool_result["error"]


def test_runtime_rail_limits_repeated_navigation_keys_by_key_value() -> None:
    rail = _runtime_rail()

    for _ in range(3):
        page_down = _tool_context(
            "mcp_playwright-official_browser_press_key",
            {"key": "PageDown"},
        )
        _run(rail.before_tool_call(page_down))
        assert "_skip_tool" not in page_down.extra
        page_down.inputs.tool_result = "PageDown executed"
        _run(rail.after_tool_call(page_down))

    blocked_page_down = _tool_context(
        "mcp_playwright-official_browser_press_key",
        {"key": "PageDown"},
    )
    _run(rail.before_tool_call(blocked_page_down))

    assert blocked_page_down.extra["_skip_tool"] is True
    assert blocked_page_down.inputs.tool_result["reason"] == "repeated_key_limited"
    assert blocked_page_down.inputs.tool_result["key"] == "PageDown"

    for _ in range(2):
        escape = _tool_context(
            "mcp_playwright-official_browser_press_key",
            {"key": "Escape"},
        )
        _run(rail.before_tool_call(escape))
        assert "_skip_tool" not in escape.extra
        escape.inputs.tool_result = "Escape executed"
        _run(rail.after_tool_call(escape))

    blocked_escape = _tool_context(
        "mcp_playwright-official_browser_press_key",
        {"key": "Escape"},
    )
    _run(rail.before_tool_call(blocked_escape))

    assert blocked_escape.extra["_skip_tool"] is True
    assert blocked_escape.inputs.tool_result["reason"] == "repeated_key_limited"
    assert blocked_escape.inputs.tool_result["key"] == "Escape"
