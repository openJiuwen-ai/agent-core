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


def test_runtime_rail_allows_snapshot_after_semantic_failure_then_blocks_same_target() -> None:
    rail = _runtime_rail()
    semantic_ctx = _tool_context(
        "browser_select_dropdown_option",
        {"field_selector": "#states", "option_text": "Atlantis", "exact": True},
        tool_result={
            "ok": False,
            "error": "dropdown_option_not_found",
            "resolved_field_selector": "#states",
            "target_family": "semantic_dropdown:https://example.test/:#states",
        },
    )
    _run(rail.after_tool_call(semantic_ctx))

    snapshot_ctx = _tool_context("mcp_playwright-official_browser_snapshot", {})
    _run(rail.before_tool_call(snapshot_ctx))
    assert "_skip_tool" not in snapshot_ctx.extra
    snapshot_ctx.inputs.tool_result = {"ok": True, "url": "https://example.test/"}
    _run(rail.after_tool_call(snapshot_ctx))

    raw_ctx = _tool_context(
        "mcp_playwright-official_browser_select_option",
        {"target": "#states", "values": ["AL"]},
    )
    _run(rail.before_tool_call(raw_ctx))

    assert raw_ctx.extra["_skip_tool"] is True
    assert raw_ctx.inputs.tool_result["reason"] == "raw_fallback_after_semantic_widget_failure"


def test_runtime_rail_does_not_arm_fallback_for_generic_batch_failure() -> None:
    rail = _runtime_rail()
    batch_ctx = _tool_context(
        "browser_batch_interact",
        {"steps": [{"op": "click", "selector": "#missing"}]},
        tool_result={
            "ok": False,
            "error": "one_or_more_steps_failed",
            "steps_ok": 0,
            "steps_failed": 1,
        },
    )
    _run(rail.after_tool_call(batch_ctx))

    snapshot_ctx = _tool_context("mcp_playwright-official_browser_snapshot", {})
    _run(rail.before_tool_call(snapshot_ctx))

    assert "_skip_tool" not in snapshot_ctx.extra
    assert rail.mcp_usage_limiter.last_failure_semantic_context is False


def test_runtime_rail_blocks_fifth_probe_then_escalates_identical_retry(monkeypatch) -> None:
    monkeypatch.setenv("BROWSER_TASK_PROGRESS_SOFT_LIMIT", "5")
    monkeypatch.setenv("BROWSER_TASK_PROGRESS_HARD_LIMIT", "8")
    rail = _runtime_rail()
    navigation = _tool_context(
        "browser_navigate",
        {"url": "https://sg.trip.com/flights"},
        tool_result={"ok": True, "url": "https://sg.trip.com/flights"},
    )
    _run(rail.after_tool_call(navigation))
    args = {"query": "Search flights", "max_items": 10}

    for expected_turns in range(1, 5):
        probe = _tool_context(
            "browser_probe_interactives",
            args,
            tool_result={"ok": True, "url": "https://sg.trip.com/flights", "elements": []},
        )
        _run(rail.before_tool_call(probe))
        assert "_skip_tool" not in probe.extra
        _run(rail.after_tool_call(probe))
        assert rail.mcp_usage_limiter.no_progress_turns == expected_turns

    fifth_probe = _tool_context("browser_probe_interactives", args)
    _run(rail.before_tool_call(fifth_probe))

    assert fifth_probe.extra["_skip_tool"] is True
    assert fifth_probe.inputs.tool_result["reason"] == "task_progress_strategy_change_required"
    assert fifth_probe.inputs.tool_result["no_progress_turns"] == 5
    assert "observation" in fifth_probe.inputs.tool_result["blocked_families"]

    _run(rail.after_tool_call(fifth_probe))
    assert rail.mcp_usage_limiter.no_progress_turns == 5

    sixth_probe = _tool_context("browser_probe_interactives", args)
    _run(rail.before_tool_call(sixth_probe))

    assert sixth_probe.extra["_skip_tool"] is True
    assert sixth_probe.inputs.tool_result["reason"] == "task_progress_budget_exhausted"
    assert sixth_probe.inputs.tool_result["no_progress_turns"] == 5


def test_runtime_rail_blocks_all_browser_calls_after_progress_budget_exhaustion(monkeypatch) -> None:
    monkeypatch.setenv("BROWSER_TASK_PROGRESS_SOFT_LIMIT", "2")
    monkeypatch.setenv("BROWSER_TASK_PROGRESS_HARD_LIMIT", "4")
    monkeypatch.setenv("BROWSER_TASK_OBSERVATION_PROGRESS_LIMIT", "0")
    rail = _runtime_rail()
    completed = [
        _tool_context("browser_wait_for", {"time": 100}, tool_result={"ok": True}),
        _tool_context("browser_click", {"target": "#a"}, tool_result={"ok": True}),
        _tool_context(
            "browser_navigate",
            {"url": "https://example.test/seen"},
            tool_result={"ok": False, "error": "navigation_failed"},
        ),
        _tool_context(
            "browser_batch_interact",
            {"steps": []},
            tool_result={"ok": False, "error": "one_or_more_steps_failed"},
        ),
    ]
    for ctx in completed:
        _run(rail.after_tool_call(ctx))

    blocked = _tool_context("browser_probe_form_fields", {})
    _run(rail.before_tool_call(blocked))

    assert blocked.extra["_skip_tool"] is True
    assert blocked.inputs.tool_result["reason"] == "task_progress_budget_exhausted"
    assert blocked.inputs.tool_result["no_progress_turns"] == 4
