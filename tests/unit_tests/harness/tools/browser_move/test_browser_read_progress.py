#!/usr/bin/env python
# coding: utf-8
"""Regression coverage across read classification, semantic tracking, and admission."""
# pylint: disable=protected-access

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openjiuwen.core.context_engine import ContextWindow
from openjiuwen.core.foundation.llm import AssistantMessage, ToolCall, ToolMessage, UserMessage
from openjiuwen.harness.tools.browser_move.playwright_runtime import runtime as runtime_module
from openjiuwen.harness.tools.browser_move.playwright_runtime.browser_state_context_processor import (
    BrowserStateContextProcessor,
    BrowserStateContextProcessorConfig,
)
from openjiuwen.harness.tools.browser_move.playwright_runtime.browser_working_context import (
    BROWSER_TASK_STATE_KEY,
)
from openjiuwen.harness.tools.browser_move.playwright_runtime.runtime import BrowserAgentRuntime, BrowserRuntimeRail


class _Session:
    def __init__(self) -> None:
        self.state = {BROWSER_TASK_STATE_KEY: BrowserRuntimeRail._build_phase_state("Inspect the search form")}

    def get_state(self, key: str):
        return self.state.get(key)

    def update_state(self, values: dict) -> None:
        self.state.update(values)


@pytest.fixture
def browser_flow(monkeypatch):
    """Run production capture and tracking with a fixed synthetic browser page."""
    runtime = BrowserAgentRuntime.__new__(BrowserAgentRuntime)
    runtime._page_generation = 0
    runtime._reference_generations = {}
    runtime._selector_primary_links = {}
    runtime._last_observed_url = ""
    runtime.ensure_runtime_ready = AsyncMock()
    runtime._call_playwright_tool = AsyncMock(return_value='- textbox "Search" [ref=e1]')
    runtime._call_playwright_run_code_unsafe = AsyncMock(
        return_value={
            "ok": True,
            "url": "https://example.test/search",
            "title": "Search",
            "semantic_state": {"result_count": 0, "selected_filters": []},
        }
    )
    runtime.capture_browser_state = AsyncMock(wraps=runtime.capture_browser_state)
    runtime.capture_compact_browser_state = AsyncMock(wraps=runtime.capture_compact_browser_state)
    monkeypatch.setattr(runtime_module, "write_browser_agent_audit_artifact", lambda *_args, **_kwargs: {})
    session = _Session()
    messages = [UserMessage(content="Inspect the search form")]
    context = SimpleNamespace(get_messages=lambda: messages, get_session_ref=lambda: session)
    processor = BrowserStateContextProcessor(BrowserStateContextProcessorConfig(provider=runtime))
    return runtime, session, context, processor


async def _render(context, processor) -> None:
    await processor.on_get_context_window(
        context, ContextWindow(context_messages=list(context.get_messages()))
    )


async def _complete_tool(browser_flow, tool_name: str, arguments: dict, *, result: dict | None = None) -> None:
    runtime, session, context, processor = browser_flow
    BrowserRuntimeRail._consume_phase_budget(
        session, tool_name, arguments, current_page_state=runtime.export_page_state()
    )
    messages = context.get_messages()
    call_id = f"call-{len(messages)}"
    messages.extend(
        [
            AssistantMessage(
                content="",
                tool_calls=[
                    ToolCall(id=call_id, name=tool_name, type="function", arguments=json.dumps(arguments))
                ],
            ),
            ToolMessage(content=json.dumps(result or {"ok": True}), name=tool_name, tool_call_id=call_id),
        ]
    )
    await _render(context, processor)


@pytest.mark.asyncio
@pytest.mark.parametrize("failed_interactions", [0, 2, 3])
async def test_reads_preserve_interaction_progress_and_replan_budget(browser_flow, failed_interactions: int) -> None:
    runtime, session, context, processor = browser_flow
    await _render(context, processor)
    assert runtime.semantic_progress["progress"] == "initial"

    for _ in range(failed_interactions):
        await _complete_tool(browser_flow, "browser_click", {"ref": "e1"})

    inspections = [
        ("mcp_playwright-official_browser_snapshot", {}),
        ("browser_probe_interactives", {"query": "search field"}),
        ("mcp_playwright-official_browser_find", {"text": "Search"}),
        ("browser_probe_cards", {}),
        ("playwright.browser_snapshot", {}),
        ("browser_find", {"text": "submit control"}),
    ]
    for index, (tool_name, arguments) in enumerate(inspections, start=1):
        await _complete_tool(browser_flow, tool_name, arguments)
        progress = runtime.semantic_progress
        state = session.get_state(BROWSER_TASK_STATE_KEY)
        assert progress["consecutive_no_progress"] == failed_interactions
        assert progress["replan_required"] is (failed_interactions == 3)
        assert state["replan_required"] is (failed_interactions == 3)
        assert state["replan_count"] == 0
        assert state["replan_trial_pending"] is False
        assert runtime.capture_compact_browser_state.await_count == index

        # Rebuilding the model context must not observe the same completed group twice.
        revision = progress["revision"]
        await _render(context, processor)
        assert runtime.semantic_progress["revision"] == revision
        assert runtime.capture_compact_browser_state.await_count == index

    assert runtime.capture_browser_state.await_count == 1 + failed_interactions
    assert sum(item["attempts"] for item in state["phases"].values()) == 6 + failed_interactions
    if failed_interactions < 3:
        await _complete_tool(browser_flow, "browser_click", {"ref": "e1"})
        assert runtime.semantic_progress["consecutive_no_progress"] == failed_interactions + 1
        assert session.get_state(BROWSER_TASK_STATE_KEY)["replan_required"] is (failed_interactions == 2)


@pytest.mark.asyncio
@pytest.mark.parametrize("compact_error", [True, False], ids=["exception", "non_object"])
async def test_read_fallback_capture_preserves_observation_category(browser_flow, compact_error: bool) -> None:
    runtime, session, context, processor = browser_flow
    await _render(context, processor)
    runtime.capture_compact_browser_state.return_value = None
    if compact_error:
        runtime.capture_compact_browser_state.side_effect = RuntimeError("compact capture unavailable")

    for _ in range(3):
        await _complete_tool(browser_flow, "browser_snapshot", {})

    assert runtime.capture_browser_state.await_count == 4
    assert runtime.semantic_progress["consecutive_no_progress"] == 0
    assert session.get_state(BROWSER_TASK_STATE_KEY)["replan_required"] is False


@pytest.mark.asyncio
async def test_read_after_ambiguous_action_does_not_repeat_reconciliation(browser_flow) -> None:
    runtime, session, context, processor = browser_flow
    await _render(context, processor)
    await _complete_tool(
        browser_flow, "browser_click", {"ref": "e1"}, result={"ok": False, "executed": True, "state_changed": True}
    )

    for _ in range(2):
        await _complete_tool(browser_flow, "browser_snapshot", {})

    assert runtime.capture_browser_state.await_count == 2
    assert runtime.capture_compact_browser_state.await_count == 2
    assert runtime.semantic_progress["consecutive_no_progress"] == 1
    assert session.get_state(BROWSER_TASK_STATE_KEY)["replan_required"] is False
