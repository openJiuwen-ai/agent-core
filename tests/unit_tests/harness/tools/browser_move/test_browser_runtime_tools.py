#!/usr/bin/env python
# coding: utf-8

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

from openjiuwen.core.foundation.tool import McpServerConfig, Tool, ToolCard
from openjiuwen.harness.tools.base_tool import ToolOutput
from openjiuwen.harness.tools.browser_move.playwright_runtime.config import BrowserRunGuardrails
from openjiuwen.harness.tools.browser_move.playwright_runtime.runtime import BrowserAgentRuntime
from openjiuwen.harness.tools.browser_move.playwright_runtime.runtime_tools import (
    BrowserBatchInteractTool,
    BrowserFillFormSemanticTool,
    BrowserCancelTool,
    BrowserClearCancelTool,
    BrowserCustomActionTool,
    BrowserListActionsTool,
    BrowserProbeCalendarTool,
    BrowserProbeCardsTool,
    BrowserProbeDropdownTool,
    BrowserProbeFormFieldsTool,
    BrowserProbeInteractivesTool,
    BrowserRuntimeHealthTool,
    BrowserSelectCalendarDateTool,
    BrowserSelectDropdownOptionTool,
    build_browser_runtime_tools,
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


def test_build_browser_runtime_tools_returns_helper_tools_by_default() -> None:
    tools = build_browser_runtime_tools(_make_runtime())
    assert len(tools) == 14


def test_each_tool_is_tool_subclass() -> None:
    for tool in build_browser_runtime_tools(_make_runtime()):
        assert isinstance(tool, Tool)


def test_each_tool_has_tool_card() -> None:
    for tool in build_browser_runtime_tools(_make_runtime()):
        assert isinstance(tool.card, ToolCard)


def test_default_helper_tool_names() -> None:
    names = [tool.card.name for tool in build_browser_runtime_tools(_make_runtime())]
    assert names == [
        "browser_cancel_run",
        "browser_clear_cancel",
        "browser_probe_interactives",
        "browser_probe_cards",
        "browser_probe_form_fields",
        "browser_fill_form_semantic",
        "browser_probe_dropdown",
        "browser_select_dropdown_option",
        "browser_probe_calendar",
        "browser_select_calendar_date",
        "browser_batch_interact",
        "browser_custom_action",
        "browser_list_custom_actions",
        "browser_runtime_health",
    ]


def test_helper_tool_classes() -> None:
    (
        cancel,
        clear_cancel,
        probe_interactives,
        probe_cards,
        probe_form_fields,
        fill_form_semantic,
        probe_dropdown,
        select_dropdown,
        probe_calendar,
        select_calendar,
        batch_interact,
        custom_action,
        list_actions,
        health,
    ) = build_browser_runtime_tools(_make_runtime())
    assert isinstance(cancel, BrowserCancelTool)
    assert isinstance(clear_cancel, BrowserClearCancelTool)
    assert isinstance(probe_interactives, BrowserProbeInteractivesTool)
    assert isinstance(probe_cards, BrowserProbeCardsTool)
    assert isinstance(probe_form_fields, BrowserProbeFormFieldsTool)
    assert isinstance(fill_form_semantic, BrowserFillFormSemanticTool)
    assert isinstance(probe_dropdown, BrowserProbeDropdownTool)
    assert isinstance(select_dropdown, BrowserSelectDropdownOptionTool)
    assert isinstance(probe_calendar, BrowserProbeCalendarTool)
    assert isinstance(select_calendar, BrowserSelectCalendarDateTool)
    assert isinstance(batch_interact, BrowserBatchInteractTool)
    assert isinstance(custom_action, BrowserCustomActionTool)
    assert isinstance(list_actions, BrowserListActionsTool)
    assert isinstance(health, BrowserRuntimeHealthTool)


def test_language_en_uses_non_empty_descriptions() -> None:
    tools = build_browser_runtime_tools(_make_runtime(), language="en")
    for tool in tools:
        assert tool.card.description
        assert any(ch.isascii() and ch.isalpha() for ch in tool.card.description)


def test_tool_ids_are_non_empty() -> None:
    for tool in build_browser_runtime_tools(_make_runtime()):
        assert tool.card.id



def test_browser_fill_form_semantic_wrapper_calls_runtime() -> None:
    runtime = _make_runtime()
    runtime.fill_form_semantic = AsyncMock(return_value={"ok": True, "filled": [{"field": "email"}]})
    tool = BrowserFillFormSemanticTool(runtime)

    result = _run(
        tool.invoke(
            {
                "fields": {"email": "test.passenger@example.com"},
                "max_fields": 999,
                "viewport_only": False,
                "clear_existing": True,
            }
        )
    )

    runtime.fill_form_semantic.assert_called_once_with(
        fields={"email": "test.passenger@example.com"},
        max_fields=200,
        viewport_only=False,
        clear_existing=True,
    )
    assert result.success is True


def test_browser_fill_form_semantic_requires_fields_object() -> None:
    runtime = _make_runtime()
    tool = BrowserFillFormSemanticTool(runtime)

    result = _run(tool.invoke({"fields": []}))

    assert result.success is False
    assert "fields must be a non-empty object" in result.error

def test_cancel_tool_calls_cancel_run() -> None:
    runtime = _make_runtime()
    runtime.ensure_runtime_ready = AsyncMock()
    runtime.cancel_run = AsyncMock(return_value={"ok": True, "session_id": "s1", "request_id": None, "error": None})
    tool = BrowserCancelTool(runtime)
    result = _run(tool.invoke({"session_id": "s1"}))
    runtime.ensure_runtime_ready.assert_called_once()
    runtime.cancel_run.assert_called_once_with(session_id="s1", request_id=None)
    assert result.success is True


def test_clear_cancel_tool_calls_runtime_clear_cancel() -> None:
    runtime = _make_runtime()
    runtime.ensure_runtime_ready = AsyncMock()
    runtime.clear_cancel = AsyncMock(return_value={"ok": True, "session_id": "s1", "request_id": "r1", "error": None})
    tool = BrowserClearCancelTool(runtime)
    result = _run(tool.invoke({"session_id": "s1", "request_id": "r1"}))
    runtime.ensure_runtime_ready.assert_called_once()
    runtime.clear_cancel.assert_called_once_with(session_id="s1", request_id="r1")
    assert result.success is True


def test_list_actions_tool_uses_runtime_api() -> None:
    runtime = _make_runtime()
    runtime.list_actions = AsyncMock(return_value={"ok": True, "actions": ["echo"], "details": {"echo": {}}})
    tool = BrowserListActionsTool(runtime)
    result = _run(tool.invoke({}))
    runtime.list_actions.assert_called_once()
    assert result.success is True
    assert result.data["actions"] == ["echo"]


def test_custom_action_tool_uses_runtime_api() -> None:
    runtime = _make_runtime()
    runtime.run_custom_action = AsyncMock(return_value={"ok": True, "session_id": "s1"})
    tool = BrowserCustomActionTool(runtime)
    result = _run(
        tool.invoke(
            {
                "action": "echo",
                "session_id": "s1",
                "request_id": "r1",
                "params": {"text": "hello"},
            }
        )
    )
    runtime.run_custom_action.assert_called_once_with(
        action="echo",
        session_id="s1",
        request_id="r1",
        params={"text": "hello"},
    )
    assert result.success is True


def test_probe_interactives_tool_uses_runtime_api() -> None:
    runtime = _make_runtime()
    runtime.probe_interactives = AsyncMock(
        return_value={
            "ok": True,
            "elements": [
                {
                    "id": "e1",
                    "role": "button",
                    "text": "Next",
                    "selector_hint": "button:nth-of-type(1)",
                }
            ],
            "error": None,
        }
    )

    tool = BrowserProbeInteractivesTool(runtime)

    result = _run(
        tool.invoke(
            {
                "max_items": 20,
                "viewport_only": True,
                "query": "next",
            }
        )
    )

    runtime.probe_interactives.assert_called_once_with(
        max_items=20,
        viewport_only=True,
        query="next",
    )
    assert result.success is True
    assert result.data["elements"][0]["text"] == "Next"


def test_probe_cards_tool_uses_runtime_api() -> None:
    runtime = _make_runtime()
    runtime.probe_cards = AsyncMock(
        return_value={
            "ok": True,
            "cards": [
                {
                    "id": "card_1",
                    "title": "Book",
                    "price": "£10.00",
                    "selector_hint": "article.product_pod",
                }
            ],
            "error": None,
        }
    )

    tool = BrowserProbeCardsTool(runtime)

    result = _run(
        tool.invoke(
            {
                "max_cards": 20,
                "viewport_only": True,
                "include_buttons": True,
                "query": "book",
            }
        )
    )

    runtime.probe_cards.assert_called_once_with(
        max_cards=20,
        viewport_only=True,
        include_buttons=True,
        query="book",
    )
    assert result.success is True
    assert result.data["cards"][0]["title"] == "Book"


def test_runtime_health_tool_uses_runtime_api() -> None:
    runtime = _make_runtime()
    runtime.runtime_health = AsyncMock(
        return_value={
            "ok": False,
            "started": False,
            "last_heartbeat_ok": None,
            "provider": "openai",
            "api_base": "https://example.invalid/v1",
            "model_name": "test-model",
        }
    )
    tool = BrowserRuntimeHealthTool(runtime)
    result = _run(tool.invoke({}))
    runtime.runtime_health.assert_called_once()
    assert result.success is True
    assert result.data["started"] is False

def test_batch_interact_tool_uses_runtime_api_for_realistic_form_flow() -> None:
    runtime = _make_runtime()
    runtime.batch_interact = AsyncMock(
        return_value={
            "ok": True,
            "steps": [
                {"index": 0, "op": "fill", "ok": True},
                {"index": 1, "op": "autocomplete", "ok": True},
                {"index": 2, "op": "select_option", "ok": True},
            ],
            "error": None,
        }
    )
    tool = BrowserBatchInteractTool(runtime)
    steps = [
        {"op": "fill", "label": "First name", "value": "John"},
        {
            "op": "autocomplete",
            "placeholder": "From",
            "value": "Singapore",
            "choose_text": "Singapore (SIN)",
        },
        {"op": "select_option", "label": "Nationality", "option_text": "Singapore"},
    ]

    result = _run(
        tool.invoke(
            {
                "steps": steps,
                "timeout_ms": 3000,
                "wait_after_each_ms": 50,
                "continue_on_error": False,
                "global_timeout_ms": 15000,
                "session_id": "sess-batch",
                "request_id": "req-batch",
            }
        )
    )

    runtime.batch_interact.assert_called_once_with(
        steps=steps,
        timeout_ms=3000,
        wait_after_each_ms=50,
        continue_on_error=False,
        global_timeout_ms=15000,
        session_id="sess-batch",
        request_id="req-batch",
    )
    assert result.success is True
    assert result.data["steps"][1]["op"] == "autocomplete"


def test_batch_interact_tool_reports_runtime_error() -> None:
    runtime = _make_runtime()
    runtime.batch_interact = AsyncMock(
        return_value={
            "ok": False,
            "error": "browser_code_executor_not_ready",
            "steps_requested": 1,
        }
    )
    tool = BrowserBatchInteractTool(runtime)

    result = _run(tool.invoke({"steps": [{"op": "click", "text": "Search"}]}))

    assert result.success is False
    assert result.error == "browser_code_executor_not_ready"
    assert result.data["steps_requested"] == 1



def test_probe_dropdown_tool_uses_runtime_api_with_clamped_inputs() -> None:
    runtime = _make_runtime()
    runtime.probe_dropdown = AsyncMock(
        return_value={
            "ok": True,
            "options": [
                {
                    "text": "Kuala Lumpur",
                    "role": "option",
                    "selector_hint": "[role=option]:nth-of-type(1)",
                }
            ],
            "error": None,
        }
    )
    tool = BrowserProbeDropdownTool(runtime)

    result = _run(
        tool.invoke(
            {
                "max_options": 999,
                "viewport_only": "false",
                "query": "Kuala Lumpur",
            }
        )
    )

    runtime.probe_dropdown.assert_called_once_with(
        max_options=80,
        viewport_only=False,
        query="Kuala Lumpur",
    )
    assert result.success is True
    assert result.data["options"][0]["text"] == "Kuala Lumpur"


def test_select_dropdown_option_tool_uses_runtime_api() -> None:
    runtime = _make_runtime()
    runtime.select_dropdown_option = AsyncMock(
        return_value={
            "ok": True,
            "selected": {"text": "Kuala Lumpur", "selector_hint": "li:nth-of-type(1)"},
            "error": None,
        }
    )
    tool = BrowserSelectDropdownOptionTool(runtime)

    result = _run(
        tool.invoke(
            {
                "field_selector": "#destination",
                "field_label": "Destination",
                "query": "Kuala",
                "option_text": "Kuala Lumpur",
                "exact": "true",
                "timeout_ms": 7000,
                "wait_after_type_ms": 100,
            }
        )
    )

    runtime.select_dropdown_option.assert_called_once_with(
        field_selector="#destination",
        field_label="Destination",
        query="Kuala",
        option_text="Kuala Lumpur",
        option_texts=None,
        exact=True,
        preserve_existing=True,
        selection_mode="add",
        timeout_ms=7000,
        wait_after_type_ms=100,
    )
    assert result.success is True
    assert result.data["selected"]["text"] == "Kuala Lumpur"


def test_select_dropdown_option_tool_reports_runtime_error() -> None:
    runtime = _make_runtime()
    runtime.select_dropdown_option = AsyncMock(
        return_value={"ok": False, "error": "dropdown_option_not_found", "options": []}
    )
    tool = BrowserSelectDropdownOptionTool(runtime)

    result = _run(tool.invoke({"query": "Missing City", "option_text": "Missing City"}))

    assert result.success is False
    assert result.error == "dropdown_option_not_found"


def test_probe_calendar_tool_uses_runtime_api_with_clamped_inputs() -> None:
    runtime = _make_runtime()
    runtime.probe_calendar = AsyncMock(
        return_value={
            "ok": True,
            "visible_months": [{"label": "July 2026", "month": 6, "year": 2026}],
            "days": [{"date": "2026-07-15", "day": 15, "disabled": False}],
            "error": None,
        }
    )
    tool = BrowserProbeCalendarTool(runtime)

    result = _run(
        tool.invoke(
            {
                "max_days": 999,
                "viewport_only": "0",
                "query": "2026-07-15",
            }
        )
    )

    runtime.probe_calendar.assert_called_once_with(
        max_days=240,
        viewport_only=False,
        query="2026-07-15",
    )
    assert result.success is True
    assert result.data["days"][0]["date"] == "2026-07-15"


def test_select_calendar_date_tool_requires_date() -> None:
    runtime = _make_runtime()
    runtime.select_calendar_date = AsyncMock()
    tool = BrowserSelectCalendarDateTool(runtime)

    result = _run(tool.invoke({}))

    runtime.select_calendar_date.assert_not_called()
    assert result.success is False
    assert result.error == "date is required"


def test_select_calendar_date_tool_uses_runtime_api() -> None:
    runtime = _make_runtime()
    runtime.select_calendar_date = AsyncMock(
        return_value={
            "ok": True,
            "selected_date": "2026-07-15",
            "method": "calendar_click",
            "error": None,
        }
    )
    tool = BrowserSelectCalendarDateTool(runtime)

    result = _run(
        tool.invoke(
            {
                "date": "2026-07-15",
                "field_selector": "[data-testid='search_date_depart0']",
                "next_selector": "button.next-month",
                "prev_selector": "button.prev-month",
                "max_month_clicks": 24,
                "timeout_ms": 8000,
                "try_direct_input": "false",
            }
        )
    )

    runtime.select_calendar_date.assert_called_once_with(
        date="2026-07-15",
        field_selector="[data-testid='search_date_depart0']",
        next_selector="button.next-month",
        prev_selector="button.prev-month",
        max_month_clicks=24,
        timeout_ms=8000,
        try_direct_input=False,
    )
    assert result.success is True
    assert result.data["selected_date"] == "2026-07-15"
