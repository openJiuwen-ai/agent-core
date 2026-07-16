#!/usr/bin/env python
# coding: utf-8

from __future__ import annotations

import json

from openjiuwen.harness.tools.base_tool import ToolOutput
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


def test_limiter_preserves_failed_target_after_unrelated_semantic_tool() -> None:
    limiter = _BrowserMcpUsageLimiter()
    failed = _Call("mcp_playwright-official_browser_type", {"selector": "#passport", "text": "TESTPASS123"})
    semantic = _Call("browser_probe_form_fields", {"query": "passport"})
    follow_up = _Call("mcp_playwright-official_browser_type", {"selector": "#passport", "text": "TESTPASS123"})

    limiter.record_result(failed, [{"ok": False, "error": "locator.fill: Timeout 5000ms exceeded"}])
    limiter.record_result(failed, [{"ok": False, "error": "locator.fill: Timeout 5000ms exceeded"}])
    assert limiter.blocked_reason(follow_up) is not None

    limiter.record_result(semantic, [{"ok": True, "fields": [{"selector_hint": "#passport"}]}])

    reason = limiter.blocked_reason(follow_up)

    assert reason is not None
    assert "recently failed" in reason or "same failed" in reason


def test_limiter_reads_failure_from_tuple_tool_message() -> None:
    limiter = _BrowserMcpUsageLimiter()
    failed = _Call(
        "mcp_playwright-official_browser_click",
        {"target": 'div.react-datepicker__year-text:has-text("1996")'},
    )

    class _Message:
        content = '### Error Error: selector does not match any elements.'

    limiter.record_result(failed, [({"ignored": True}, _Message())])

    assert limiter.blocked_reason(failed) is not None


def test_limiter_blocks_same_calendar_year_target_family() -> None:
    limiter = _BrowserMcpUsageLimiter()
    failed = _Call(
        "mcp_playwright-official_browser_click",
        {"target": 'div.react-datepicker__year-text:has-text("1996")'},
    )
    alternate = _Call(
        "mcp_playwright-official_browser_click",
        {"target": 'div.react-datepicker__year-option:has-text("1996")'},
    )

    limiter.record_result(failed, ['### Error Error: selector does not match any elements.'])

    reason = limiter.blocked_reason(alternate)

    assert reason is not None
    assert "recently failed" in reason


def test_limiter_blocks_raw_click_after_semantic_calendar_failure() -> None:
    limiter = _BrowserMcpUsageLimiter()
    calendar = _Call(
        "browser_select_calendar_date",
        {"date": "1996-11-17", "field_selector": "#dateOfBirthInput"},
    )
    raw_click = _Call(
        "mcp_playwright-official_browser_click",
        {"target": ".react-datepicker__year-select"},
    )

    limiter.record_result(calendar, [{"ok": False, "error": "date_not_found_after_18_month_clicks"}])

    reason = limiter.blocked_reason(raw_click)

    assert reason is not None
    assert "last dropdown/calendar/form interaction failed" in reason


def test_limiter_reads_semantic_failure_from_tool_output() -> None:
    limiter = _BrowserMcpUsageLimiter()
    dropdown = _Call(
        "browser_select_dropdown_option",
        {
            "field_selector": "select.js-example-basic-multiple",
            "option_text": "Atlantis",
            "exact": True,
        },
    )
    raw_click = _Call(
        "mcp_playwright-official_browser_click",
        {"target": "select.js-example-basic-multiple"},
    )
    result = ToolOutput(
        success=False,
        data={
            "ok": False,
            "error": "dropdown_option_not_found",
            "resolved_field_selector": "select.js-example-basic-multiple",
            "target_family": "semantic_dropdown:https://select2.org/:select.js-example-basic-multiple",
        },
        error="dropdown_option_not_found",
    )

    limiter.record_result(dropdown, [(result, {"ignored": True})])

    reason = limiter.blocked_reason(raw_click)

    assert reason is not None
    assert "raw_fallback_after_semantic_widget_failure" in reason or "recently failed" in reason


def test_limiter_blocks_raw_select_option_after_semantic_dropdown_failure() -> None:
    limiter = _BrowserMcpUsageLimiter()
    dropdown = _Call(
        "browser_select_dropdown_option",
        {"field_selector": "#states", "option_text": "Atlantis", "exact": True},
    )
    raw_select = _Call(
        "mcp_playwright-official_browser_select_option",
        {"target": "#states", "values": ["AL", "HI"]},
    )

    limiter.record_result(dropdown, [{"ok": False, "error": "dropdown_option_not_found"}])

    reason = limiter.blocked_reason(raw_select)

    assert reason is not None
    assert "mcp_usage_limited" in reason


def test_limiter_blocks_third_identical_semantic_no_progress_attempt() -> None:
    limiter = _BrowserMcpUsageLimiter()
    dropdown = _Call(
        "browser_select_dropdown_option",
        {"field_selector": "#states", "option_text": "Alabama", "exact": True},
    )
    result = [{"ok": True, "selected_value": "AL", "display_value": "Alabama"}]

    assert limiter.blocked_reason(dropdown) is None
    limiter.record_result(dropdown, result)
    assert limiter.blocked_reason(dropdown) is None
    limiter.record_result(dropdown, result)

    reason = limiter.blocked_reason(dropdown)

    assert reason is not None
    assert "semantic_no_progress_limited" in reason


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


def test_limiter_blocks_repeated_evaluate_loop() -> None:
    limiter = _BrowserMcpUsageLimiter()
    calls = [
        _Call("mcp_playwright-official_browser_evaluate", {"function": f"() => {index}"})
        for index in range(3)
    ]
    for call in calls:
        assert limiter.blocked_reason(call) is None
        limiter.record_result(call, [{"ok": True}])

    blocked = _Call("mcp_playwright-official_browser_evaluate", {"function": "() => 4"})
    reason = limiter.blocked_reason(blocked)

    assert reason is not None
    assert "repeated browser_evaluate calls are limited to 3" in reason


def test_limiter_counts_run_code_in_raw_primitive_streak() -> None:
    limiter = _BrowserMcpUsageLimiter()
    calls = [
        _Call("mcp_playwright-official_browser_run_code_unsafe", {"code": f"async (page) => {index}"})
        for index in range(3)
    ]
    for call in calls:
        assert limiter.blocked_reason(call) is None
        limiter.record_result(call, [{"ok": True}])

    snapshot = _Call("mcp_playwright-official_browser_snapshot", {})
    assert limiter.blocked_reason(snapshot) is None
    limiter.record_result(snapshot, [{"ok": True}])

    click = _Call("mcp_playwright-official_browser_click", {"selector": "#next"})
    assert limiter.blocked_reason(click) is None
    limiter.record_result(click, [{"ok": True}])

    blocked = _Call("mcp_playwright-official_browser_evaluate", {"function": "() => 1"})
    reason = limiter.blocked_reason(blocked)

    assert reason is not None
    assert "raw browser primitive streak exceeded" in reason
