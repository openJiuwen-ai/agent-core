#!/usr/bin/env python
# coding: utf-8

from __future__ import annotations

import json

from openjiuwen.harness.tools.browser_move.playwright_runtime.agents import (
    _BrowserMcpUsageLimiter,
    _normalized_browser_tool_name,
)


class _Call:
    def __init__(self, name: str, arguments: dict):
        self.name = name
        self.arguments = json.dumps(arguments)
        self.id = "tool-call-1"


def test_normalizes_prefixed_official_playwright_tool_names() -> None:
    assert _normalized_browser_tool_name("mcp_playwright-official_browser_click") == "browser_click"
    assert _normalized_browser_tool_name("mcp_playwright_browser_snapshot") == "browser_snapshot"
    assert _normalized_browser_tool_name("vendor.namespace.browser_type") == "browser_type"


def test_limiter_blocks_repeating_same_failed_stale_selector() -> None:
    limiter = _BrowserMcpUsageLimiter()
    call = _Call("mcp_playwright-official_browser_click", {"selector": "[ref=f96e393]"})

    assert limiter.blocked_reason(call) is None
    limiter.record_result(call, [{"ok": False, "error": "[ref=f96e393] does not match any elements"}])

    reason = limiter.blocked_reason(call)

    assert reason is not None
    assert "same failed browser_click target" in reason
    assert "stale refs" in reason


def test_limiter_blocks_after_two_raw_failures_and_suggests_form_probe() -> None:
    limiter = _BrowserMcpUsageLimiter()
    first = _Call("mcp_playwright-official_browser_type", {"selector": "#first", "text": "Alex"})
    second = _Call("mcp_playwright-official_browser_type", {"selector": "#last", "text": "Tan"})
    third = _Call("mcp_playwright-official_browser_type", {"selector": "#passport", "text": "TESTPASS123"})

    limiter.record_result(first, [{"ok": False, "error": "locator.fill: Timeout 5000ms exceeded"}])
    limiter.record_result(second, [{"ok": False, "error": "locator.fill: Timeout 5000ms exceeded"}])

    reason = limiter.blocked_reason(third)

    assert reason is not None
    assert "repeated raw Playwright interactions recently failed" in reason
    assert "browser_probe_form_fields" in reason


def test_limiter_resets_failure_state_after_semantic_tool() -> None:
    limiter = _BrowserMcpUsageLimiter()
    failed = _Call("mcp_playwright-official_browser_type", {"selector": "#passport", "text": "TESTPASS123"})
    semantic = _Call("browser_probe_form_fields", {"query": "passport"})
    follow_up = _Call("mcp_playwright-official_browser_type", {"selector": "#passport", "text": "TESTPASS123"})

    limiter.record_result(failed, [{"ok": False, "error": "locator.fill: Timeout 5000ms exceeded"}])
    limiter.record_result(failed, [{"ok": False, "error": "locator.fill: Timeout 5000ms exceeded"}])
    assert limiter.blocked_reason(follow_up) is not None

    limiter.record_result(semantic, [{"ok": True, "fields": [{"selector_hint": "#passport"}]}])

    assert limiter.blocked_reason(follow_up) is None


def test_limiter_blocks_alternating_raw_primitive_streak() -> None:
    limiter = _BrowserMcpUsageLimiter()
    calls = [
        _Call("mcp_playwright-official_browser_click", {"selector": "#a"}),
        _Call("mcp_playwright-official_browser_snapshot", {}),
        _Call("mcp_playwright-official_browser_click", {"selector": "#b"}),
        _Call("mcp_playwright-official_browser_snapshot", {}),
        _Call("mcp_playwright-official_browser_type", {"selector": "#c", "text": "x"}),
    ]
    for call in calls:
        assert limiter.blocked_reason(call) is None
        limiter.record_result(call, [{"ok": True}])

    blocked = _Call("mcp_playwright-official_browser_click", {"selector": "#d"})
    reason = limiter.blocked_reason(blocked)

    assert reason is not None
    assert "raw browser primitive streak exceeded" in reason


def test_limiter_keeps_failed_stale_ref_after_snapshot_success() -> None:
    limiter = _BrowserMcpUsageLimiter()
    failed_click = _Call("mcp_playwright-official_browser_click", {"target": "[ref=f22e208]"})
    snapshot = _Call("mcp_playwright-official_browser_snapshot", {"depth": 4})

    limiter.record_result(failed_click, ['### Error Error: "[ref=f22e208]" does not match any elements.'])
    limiter.record_result(snapshot, [{"ok": True}])

    reason = limiter.blocked_reason(failed_click)

    assert reason is not None
    assert "failed" in reason
    assert "stale refs" in reason


def test_limiter_blocks_recent_failed_target_even_with_different_tool_shape() -> None:
    limiter = _BrowserMcpUsageLimiter()
    failed_click = _Call("mcp_playwright-official_browser_click", {"target": "[ref=f22e220]"})
    fill_form = _Call("mcp_playwright-official_browser_fill_form", {"fields": [{"ref": "[ref=f22e220]"}]})

    limiter.record_result(failed_click, ['### Error Error: "[ref=f22e220]" does not match any elements.'])

    reason = limiter.blocked_reason(fill_form)

    assert reason is not None
    assert "recently failed" in reason
    assert "browser_probe_form_fields" in reason
