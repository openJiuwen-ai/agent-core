#!/usr/bin/env python
# coding: utf-8

from __future__ import annotations

import asyncio

from openjiuwen.core.foundation.tool import McpServerConfig
from openjiuwen.harness.tools.browser_move.playwright_runtime.config import BrowserRunGuardrails
from openjiuwen.harness.tools.browser_move.playwright_runtime.runtime import BrowserAgentRuntime
from openjiuwen.harness.tools.browser_move.playwright_runtime.semantic_widgets import (
    build_calendar_probe_js,
    build_calendar_select_js,
    build_dropdown_probe_js,
    build_dropdown_select_js,
    build_form_fields_probe_js,
    build_semantic_form_fill_js,
)


def _run(coro):
    return asyncio.run(coro)


def _make_runtime() -> BrowserAgentRuntime:
    mcp_cfg = McpServerConfig(
        server_id="test",
        server_name="test",
        server_path="stdio://playwright",
        client_type="stdio",
        params={"cwd": "."},
    )
    return BrowserAgentRuntime(
        provider="openai",
        api_key="test-key",
        api_base="https://example.invalid/v1",
        model_name="test-model",
        mcp_cfg=mcp_cfg,
        guardrails=BrowserRunGuardrails(max_steps=3, max_failures=1, timeout_s=30, retry_once=False),
    )


def test_dropdown_probe_js_contains_compact_option_collection_and_clamped_params() -> None:
    js_code = build_dropdown_probe_js(max_options=999, viewport_only=False, query="Kuala Lumpur")

    assert "collectDropdownOptions" in js_code
    assert "selector_hint" in js_code
    assert '"max_options": 80' in js_code
    assert '"viewport_only": false' in js_code
    assert '"query": "Kuala Lumpur"' in js_code
    assert "document.body.innerText" not in js_code
    assert "process.platform" not in js_code


def test_dropdown_select_js_contains_atomic_typing_and_selection() -> None:
    js_code = build_dropdown_select_js(
        field_selector="#destination",
        query="Kuala",
        option_text="Kuala Lumpur",
        exact=True,
        timeout_ms=999999,
        wait_after_type_ms=999999,
    )

    assert "page.keyboard.type" in js_code
    assert "clickLikeUser(element)" in js_code
    assert '"field_selector": "#destination"' in js_code
    assert '"exact": true' in js_code
    assert '"timeout_ms": 30000' in js_code
    assert '"wait_after_type_ms": 5000' in js_code
    assert "process.platform" not in js_code


def test_form_fields_probe_js_collects_compact_field_metadata() -> None:
    js_code = build_form_fields_probe_js(
        max_fields=999,
        viewport_only=False,
        query="passport",
        include_options=False,
    )

    assert "fieldSelectors" in js_code
    assert "selector_hint" in js_code
    assert "text_context" in js_code
    assert "querySelectorAll(selector)" in js_code
    assert '"max_fields": 160' in js_code
    assert '"viewport_only": false' in js_code
    assert '"include_options": false' in js_code
    assert "document.body.innerText" not in js_code
    assert "process.platform" not in js_code



def test_semantic_form_fill_js_matches_fields_without_broad_dom_dump() -> None:
    js_code = build_semantic_form_fill_js(
        fields={"given name": "Alex", "surname": "Tan"},
        max_fields=999,
        viewport_only=False,
        clear_existing=True,
    )

    assert "scoreField" in js_code
    assert "setNativeValue" in js_code
    assert "selector_hint" in js_code
    assert '"max_fields": 200' in js_code
    assert '"viewport_only": false' in js_code
    assert "document.body.innerText" not in js_code
    assert "process.platform" not in js_code

def test_calendar_probe_js_normalizes_days_and_months() -> None:
    js_code = build_calendar_probe_js(max_days=999, viewport_only=True, query="2026-07-15")

    assert "collectCalendarDays" in js_code
    assert "visible_months" in js_code
    assert "outside_month" in js_code
    assert '"max_days": 240' in js_code
    assert '"query": "2026-07-15"' in js_code
    assert "document.body.outerHTML" not in js_code
    assert "process.platform" not in js_code


def test_calendar_select_js_contains_exact_iso_date_selection_and_navigation() -> None:
    js_code = build_calendar_select_js(
        date="2026-07-15",
        field_selector="[data-testid='search_date_depart0']",
        next_selector="button.next-month",
        prev_selector="button.prev-month",
        max_month_clicks=999,
        timeout_ms=999999,
        try_direct_input=True,
    )

    assert "targetIso" in js_code
    assert "day.date === payload.target_iso" in js_code
    assert "!day.disabled" in js_code
    assert "findMonthNavButton" in js_code
    assert '"date": "2026-07-15"' in js_code
    assert '"max_month_clicks": 60' in js_code
    assert '"timeout_ms": 30000' in js_code
    assert "process.platform" not in js_code



def test_runtime_probe_form_fields_parses_executor_json_and_captures_generated_code() -> None:
    runtime = _make_runtime()
    runtime.ensure_runtime_ready = _async_noop
    captured = {}

    async def fake_executor(js_code: str) -> str:
        captured["js_code"] = js_code
        return '{"ok": true, "fields": [{"label": "Passport number", "selector_hint": "#passport"}]}'

    runtime._code_executor = fake_executor

    result = _run(
        runtime.probe_form_fields(
            max_fields=500,
            viewport_only=False,
            query="passport",
            include_options=False,
        )
    )

    assert result["ok"] is True
    assert result["fields"][0]["selector_hint"] == "#passport"
    assert '"max_fields": 160' in captured["js_code"]
    assert '"include_options": false' in captured["js_code"]



def test_runtime_fill_form_semantic_parses_executor_json_and_captures_generated_code() -> None:
    runtime = _make_runtime()
    runtime.ensure_runtime_ready = _async_noop
    captured = {}

    async def fake_executor(js_code: str) -> str:
        captured["js_code"] = js_code
        return '{"ok": true, "filled": [{"field": "given name", "selector_hint": "#given"}], "failed": []}'

    runtime._code_executor = fake_executor

    result = _run(
        runtime.fill_form_semantic(
            fields={"given name": "Alex"},
            max_fields=500,
            viewport_only=False,
            clear_existing=True,
        )
    )

    assert result["ok"] is True
    assert result["filled"][0]["selector_hint"] == "#given"
    assert '"max_fields": 200' in captured["js_code"]
    assert '"given name": "Alex"' in captured["js_code"]

def test_runtime_probe_dropdown_parses_executor_json_and_captures_generated_code() -> None:
    runtime = _make_runtime()
    runtime.ensure_runtime_ready = _async_noop
    captured = {}

    async def fake_executor(js_code: str) -> str:
        captured["js_code"] = js_code
        return '{"ok": true, "options": [{"text": "Kuala Lumpur", "selector_hint": "li:nth-of-type(1)"}]}'

    runtime._code_executor = fake_executor

    result = _run(runtime.probe_dropdown(max_options=500, viewport_only=False, query="Kuala Lumpur"))

    assert result["ok"] is True
    assert result["options"][0]["text"] == "Kuala Lumpur"
    assert '"max_options": 80' in captured["js_code"]
    assert '"viewport_only": false' in captured["js_code"]


async def _async_noop() -> None:
    return None


def test_runtime_select_dropdown_option_parses_executor_json() -> None:
    runtime = _make_runtime()
    runtime.ensure_runtime_ready = _async_noop
    captured = {}

    async def fake_executor(js_code: str) -> str:
        captured["js_code"] = js_code
        return '{"ok": true, "selected": {"text": "Singapore", "selector_hint": "li:nth-of-type(1)"}}'

    runtime._code_executor = fake_executor

    result = _run(
        runtime.select_dropdown_option(
            field_selector="#origin",
            query="Singapore",
            option_text="Singapore",
            exact=True,
            timeout_ms=5000,
            wait_after_type_ms=100,
        )
    )

    assert result["ok"] is True
    assert result["selected"]["text"] == "Singapore"
    assert '"field_selector": "#origin"' in captured["js_code"]
    assert '"exact": true' in captured["js_code"]


def test_runtime_probe_calendar_parses_executor_json() -> None:
    runtime = _make_runtime()
    runtime.ensure_runtime_ready = _async_noop
    captured = {}

    async def fake_executor(js_code: str) -> str:
        captured["js_code"] = js_code
        return '{"ok": true, "visible_months": [{"label": "July 2026"}], "days": [{"date": "2026-07-15", "day": 15}]}'

    runtime._code_executor = fake_executor

    result = _run(runtime.probe_calendar(max_days=500, viewport_only=True, query="2026-07-15"))

    assert result["ok"] is True
    assert result["days"][0]["date"] == "2026-07-15"
    assert '"max_days": 240' in captured["js_code"]
    assert '"query": "2026-07-15"' in captured["js_code"]


def test_runtime_select_calendar_date_parses_executor_json() -> None:
    runtime = _make_runtime()
    runtime.ensure_runtime_ready = _async_noop
    captured = {}

    async def fake_executor(js_code: str) -> str:
        captured["js_code"] = js_code
        return '{"ok": true, "selected_date": "2026-07-15", "method": "calendar_click"}'

    runtime._code_executor = fake_executor

    result = _run(
        runtime.select_calendar_date(
            date="2026-07-15",
            field_selector="[data-testid='search_date_depart0']",
            next_selector="button.next-month",
            prev_selector="button.prev-month",
            max_month_clicks=1000,
            timeout_ms=100000,
            try_direct_input=False,
        )
    )

    assert result["ok"] is True
    assert result["selected_date"] == "2026-07-15"
    assert '"date": "2026-07-15"' in captured["js_code"]
    assert '"max_month_clicks": 60' in captured["js_code"]
    assert '"try_direct_input": false' in captured["js_code"]


def test_runtime_semantic_tools_report_missing_code_executor() -> None:
    runtime = _make_runtime()
    runtime.ensure_runtime_ready = _async_noop
    runtime._code_executor = None

    fields = _run(runtime.probe_form_fields())
    fill_form = _run(runtime.fill_form_semantic(fields={"email": "test@example.com"}))
    dropdown = _run(runtime.probe_dropdown())
    calendar = _run(runtime.probe_calendar())
    select_dropdown = _run(runtime.select_dropdown_option(query="Singapore"))
    select_calendar = _run(runtime.select_calendar_date(date="2026-07-15"))

    assert fields == {"ok": False, "error": "browser_code_executor_not_ready", "fields": []}
    assert fill_form == {"ok": False, "error": "browser_code_executor_not_ready", "filled": [], "failed": []}
    assert dropdown == {"ok": False, "error": "browser_code_executor_not_ready", "options": []}
    assert calendar == {"ok": False, "error": "browser_code_executor_not_ready", "days": []}
    assert select_dropdown == {"ok": False, "error": "browser_code_executor_not_ready"}
    assert select_calendar == {"ok": False, "error": "browser_code_executor_not_ready"}
