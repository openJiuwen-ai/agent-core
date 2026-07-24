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
        {"target": "#dateOfBirthInput"},
    )

    limiter.record_result(
        calendar,
        [
            {
                "ok": False,
                "error": "date_not_found_after_18_month_clicks",
                "resolved_field_selector": "#dateOfBirthInput",
                "target_family": "semantic_calendar:https://example.test/:#dateOfBirthInput",
            }
        ],
    )

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

    limiter.record_result(
        dropdown,
        [
            {
                "ok": False,
                "error": "dropdown_option_not_found",
                "resolved_field_selector": "#states",
                "target_family": "semantic_dropdown:https://example.test/:#states",
            }
        ],
    )

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
        _Call("mcp_playwright-official_browser_type", {"selector": "#b", "text": "x"}),
        _Call("mcp_playwright-official_browser_fill_form", {"fields": [{"selector": "#c", "value": "y"}]}),
        _Call("mcp_playwright-official_browser_evaluate", {"function": "() => 1"}),
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


def test_snapshot_breaks_run_code_raw_primitive_streak() -> None:
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

    assert reason is None


def test_read_only_probe_is_not_semantic_no_progress_limited() -> None:
    limiter = _BrowserMcpUsageLimiter()
    probe = _Call("browser_probe_interactives", {"query": "airport", "max_items": 20})
    result = [{"ok": True, "url": "https://example.test/", "elements": [{"selector": "#airport"}]}]

    for _ in range(3):
        assert limiter.blocked_reason(probe) is None
        limiter.record_result(probe, result)

    assert limiter.semantic_state == {}


def test_snapshot_is_allowed_after_semantic_failure_and_context_is_preserved() -> None:
    limiter = _BrowserMcpUsageLimiter()
    semantic = _Call(
        "browser_select_dropdown_option",
        {"field_selector": "#states", "option_text": "Atlantis", "exact": True},
    )
    result = {
        "ok": False,
        "error": "dropdown_option_not_found",
        "resolved_field_selector": "#states",
        "target_family": "semantic_dropdown:https://example.test/:#states",
    }
    snapshot = _Call("mcp_playwright-official_browser_snapshot", {})
    raw_select = _Call(
        "mcp_playwright-official_browser_select_option",
        {"target": "#states", "values": ["AL"]},
    )

    limiter.record_result(semantic, [result])
    assert limiter.blocked_reason(snapshot) is None
    limiter.record_result(snapshot, [{"ok": True, "url": "https://example.test/"}])

    reason = limiter.blocked_reason(raw_select)

    assert reason is not None
    assert "raw_fallback_after_semantic_widget_failure" in reason


def test_raw_fallback_requires_same_target() -> None:
    limiter = _BrowserMcpUsageLimiter()
    semantic = _Call(
        "browser_select_dropdown_option",
        {"field_selector": "#states", "option_text": "Atlantis", "exact": True},
    )
    limiter.record_result(
        semantic,
        [
            {
                "ok": False,
                "error": "dropdown_option_not_found",
                "resolved_field_selector": "#states",
                "target_family": "semantic_dropdown:https://example.test/:#states",
            }
        ],
    )

    unrelated = _Call("mcp_playwright-official_browser_click", {"target": "#continue"})
    correlated = _Call("mcp_playwright-official_browser_click", {"target": "#states"})

    assert limiter.blocked_reason(unrelated) is None
    reason = limiter.blocked_reason(correlated)
    assert reason is not None
    assert "raw_fallback_after_semantic_widget_failure" in reason


def test_batch_failure_without_target_family_does_not_arm_raw_fallback() -> None:
    limiter = _BrowserMcpUsageLimiter()
    batch = _Call(
        "browser_batch_interact",
        {"steps": [{"op": "click", "selector": "#missing"}]},
    )
    limiter.record_result(
        batch,
        [
            {
                "ok": False,
                "error": "one_or_more_steps_failed",
                "steps_ok": 0,
                "steps_failed": 1,
            }
        ],
    )

    snapshot = _Call("mcp_playwright-official_browser_snapshot", {})
    raw_click = _Call("mcp_playwright-official_browser_click", {"target": "#missing"})

    assert limiter.last_failure_semantic_context is False
    assert limiter.blocked_reason(snapshot) is None
    reason = limiter.blocked_reason(raw_click)
    assert reason is None or "raw_fallback_after_semantic_widget_failure" not in reason


def test_progress_guard_blocks_fifth_identical_probe_before_dispatch(monkeypatch) -> None:
    monkeypatch.setenv("BROWSER_TASK_PROGRESS_SOFT_LIMIT", "5")
    monkeypatch.setenv("BROWSER_TASK_PROGRESS_HARD_LIMIT", "8")
    limiter = _BrowserMcpUsageLimiter()
    navigation = _Call("browser_navigate", {"url": "https://sg.trip.com/flights"})
    limiter.record_result(navigation, [{"ok": True, "url": "https://sg.trip.com/flights"}])
    probe = _Call("browser_probe_interactives", {"query": "Search flights", "max_items": 10})
    result = [{"ok": True, "url": "https://sg.trip.com/flights", "elements": []}]

    for expected_turns in range(1, 5):
        assert limiter.blocked_reason(probe) is None
        limiter.record_result(probe, result)
        assert limiter.no_progress_turns == expected_turns

    reason = limiter.blocked_reason(probe)

    assert reason is not None
    assert "browser_task_strategy_change_required" in reason
    assert limiter.strategy_change_required is True
    assert limiter.no_progress_turns == 5
    payload = limiter.blocked_payload(probe.name, reason, probe.arguments)
    assert payload["reason"] == "task_progress_strategy_change_required"
    assert payload["no_progress_turns"] == 5
    assert "observation" in payload["blocked_families"]

    limiter.record_result(probe, [payload])

    assert limiter.no_progress_turns == 5


def test_progress_guard_does_not_soft_block_after_materially_different_calls(monkeypatch) -> None:
    monkeypatch.setenv("BROWSER_TASK_PROGRESS_SOFT_LIMIT", "5")
    monkeypatch.setenv("BROWSER_TASK_PROGRESS_HARD_LIMIT", "8")
    monkeypatch.setenv("BROWSER_TASK_OBSERVATION_PROGRESS_LIMIT", "0")
    limiter = _BrowserMcpUsageLimiter()
    completed = [
        (_Call("browser_press_key", {"key": "PageDown"}), [{"ok": True}]),
        (_Call("browser_press_key", {"key": "PageDown"}), [{"ok": True}]),
        (_Call("browser_press_key", {"key": "PageDown"}), [{"ok": True}]),
        (_Call("browser_press_key", {"key": "Escape"}), [{"ok": True}]),
    ]

    for call, result in completed:
        assert limiter.blocked_reason(call) is None
        limiter.record_result(call, result)

    second_escape = _Call("browser_press_key", {"key": "Escape"})

    assert limiter.no_progress_turns == 4
    assert limiter.strategy_change_required is False
    assert limiter.blocked_reason(second_escape) is None
    limiter.record_result(second_escape, [{"ok": True}])
    assert limiter.no_progress_turns == 5
    assert limiter.strategy_change_required is False


def test_progress_guard_exhausts_when_predictive_soft_block_is_ignored(monkeypatch) -> None:
    monkeypatch.setenv("BROWSER_TASK_PROGRESS_SOFT_LIMIT", "5")
    monkeypatch.setenv("BROWSER_TASK_PROGRESS_HARD_LIMIT", "8")
    limiter = _BrowserMcpUsageLimiter()
    navigation = _Call("browser_navigate", {"url": "https://sg.trip.com/flights"})
    limiter.record_result(navigation, [{"ok": True, "url": "https://sg.trip.com/flights"}])
    probe = _Call("browser_probe_interactives", {"query": "Search flights", "max_items": 10})
    result = [{"ok": True, "url": "https://sg.trip.com/flights", "elements": []}]

    for _ in range(4):
        assert limiter.blocked_reason(probe) is None
        limiter.record_result(probe, result)

    first_reason = limiter.blocked_reason(probe)
    second_reason = limiter.blocked_reason(probe)

    assert first_reason is not None
    assert "browser_task_strategy_change_required" in first_reason
    assert second_reason is not None
    assert "browser_task_progress_budget_exhausted" in second_reason
    assert limiter.no_progress_turns == 5
    assert limiter.progress_budget_exhausted is True


def test_meaningful_navigation_resets_predictive_soft_budget(monkeypatch) -> None:
    monkeypatch.setenv("BROWSER_TASK_PROGRESS_SOFT_LIMIT", "3")
    monkeypatch.setenv("BROWSER_TASK_PROGRESS_HARD_LIMIT", "6")
    monkeypatch.setenv("BROWSER_TASK_OBSERVATION_PROGRESS_LIMIT", "0")
    limiter = _BrowserMcpUsageLimiter()
    probe = _Call("browser_probe_interactives", {"query": "airport"})
    result = [{"ok": True, "elements": []}]

    for _ in range(2):
        assert limiter.blocked_reason(probe) is None
        limiter.record_result(probe, result)

    navigation = _Call("browser_navigate", {"url": "https://example.test/new-stage"})
    assert limiter.blocked_reason(navigation) is None
    limiter.record_result(
        navigation,
        [{"ok": True, "url": "https://example.test/new-stage"}],
    )

    assert limiter.no_progress_turns == 0
    assert limiter.strategy_change_required is False
    assert limiter.blocked_reason(probe) is None


def test_progress_guard_exhausts_after_hard_limit(monkeypatch) -> None:
    monkeypatch.setenv("BROWSER_TASK_PROGRESS_SOFT_LIMIT", "2")
    monkeypatch.setenv("BROWSER_TASK_PROGRESS_HARD_LIMIT", "4")
    monkeypatch.setenv("BROWSER_TASK_OBSERVATION_PROGRESS_LIMIT", "0")
    limiter = _BrowserMcpUsageLimiter()
    calls = [
        _Call("browser_wait_for", {"time": 100}),
        _Call("browser_click", {"target": "#a"}),
        _Call("browser_navigate", {"url": "https://example.test/seen"}),
        _Call("browser_batch_interact", {"steps": []}),
    ]
    results = [
        [{"ok": True}],
        [{"ok": True}],
        [{"ok": False, "error": "navigation_failed"}],
        [{"ok": False, "error": "one_or_more_steps_failed"}],
    ]

    for call, result in zip(calls, results):
        limiter.record_result(call, result)

    assert limiter.progress_budget_exhausted is True
    blocked = _Call("browser_probe_form_fields", {})
    reason = limiter.blocked_reason(blocked)
    assert reason is not None
    assert "browser_task_progress_budget_exhausted" in reason
    payload = limiter.blocked_payload(blocked.name, reason, blocked.arguments)
    assert payload["reason"] == "task_progress_budget_exhausted"


def test_progress_guard_treats_same_url_stage_with_new_query_values_as_no_progress(monkeypatch) -> None:
    monkeypatch.setenv("BROWSER_TASK_PROGRESS_SOFT_LIMIT", "2")
    monkeypatch.setenv("BROWSER_TASK_PROGRESS_HARD_LIMIT", "5")
    limiter = _BrowserMcpUsageLimiter()
    first = _Call("browser_navigate", {"url": "https://example.test/flights/list?from=SIN&ts=1"})
    second = _Call("browser_navigate", {"url": "https://example.test/flights/list?from=KUL&ts=2"})

    limiter.record_result(first, [{"ok": True, "url": "https://example.test/flights/list?from=SIN&ts=1"}])
    assert limiter.no_progress_turns == 0

    limiter.record_result(second, [{"ok": True, "url": "https://example.test/flights/list?from=KUL&ts=2"}])
    assert limiter.no_progress_turns == 1
