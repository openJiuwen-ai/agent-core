#!/usr/bin/env python
# coding: utf-8
"""Tests for BrowserRuntimeRail lifecycle hook."""
# pylint: disable=protected-access

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

from openjiuwen.core.single_agent.prompts.builder import SystemPromptBuilder
from openjiuwen.harness.tools.browser_move.playwright_runtime.runtime import BrowserAgentRuntime, BrowserRuntimeRail
from openjiuwen.harness.tools.browser_move.playwright_runtime.service import MAX_ITERATION_MESSAGE
from openjiuwen.harness.tools.base_tool import ToolOutput
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext, AgentRail
from openjiuwen.core.single_agent.rail.base import InvokeInputs, ToolCallInputs


def _run(coro):
    return asyncio.run(coro)


def _make_ctx() -> AgentCallbackContext:
    return AgentCallbackContext(agent=MagicMock())


class _FakeSession:
    def __init__(self, session_id: str = "browser-session") -> None:
        self._session_id = session_id
        self._state = {}

    def get_session_id(self) -> str:
        return self._session_id

    def get_state(self, key: str):
        return self._state.get(key)

    def update_state(self, payload):
        self._state.update(payload)


def test_rail_is_agent_rail_subclass() -> None:
    assert issubclass(BrowserRuntimeRail, AgentRail)


def test_rail_holds_runtime_reference() -> None:
    runtime = MagicMock(spec=BrowserAgentRuntime)
    rail = BrowserRuntimeRail(runtime)
    assert rail._runtime is runtime


def test_before_invoke_calls_ensure_runtime_ready() -> None:
    runtime = MagicMock(spec=BrowserAgentRuntime)
    runtime.ensure_runtime_ready = AsyncMock()
    runtime.service = MagicMock()
    runtime.service.mcp_cfg = MagicMock()
    rail = BrowserRuntimeRail(runtime)
    ctx = _make_ctx()
    ctx.agent.ability_manager = MagicMock()
    _run(rail.before_invoke(ctx))
    runtime.ensure_runtime_ready.assert_called_once_with()
    ctx.agent.ability_manager.add.assert_called_once_with(runtime.service.mcp_cfg)


def test_before_invoke_called_twice_delegates_twice() -> None:
    """Idempotency is BrowserAgentRuntime's responsibility; rail always delegates."""
    runtime = MagicMock(spec=BrowserAgentRuntime)
    runtime.ensure_runtime_ready = AsyncMock()
    runtime.service = MagicMock()
    runtime.service.mcp_cfg = MagicMock()
    rail = BrowserRuntimeRail(runtime)
    ctx1 = _make_ctx()
    ctx1.agent.ability_manager = MagicMock()
    ctx2 = _make_ctx()
    ctx2.agent.ability_manager = MagicMock()
    _run(rail.before_invoke(ctx1))
    _run(rail.before_invoke(ctx2))
    assert runtime.ensure_runtime_ready.call_count == 2


def test_rail_registered_for_before_invoke_event() -> None:
    """get_callbacks() must return before_invoke so the framework fires it."""
    from openjiuwen.core.single_agent.rail.base import AgentCallbackEvent

    runtime = MagicMock(spec=BrowserAgentRuntime)
    runtime.ensure_runtime_ready = AsyncMock()
    runtime.service = MagicMock()
    runtime.service.mcp_cfg = MagicMock()
    rail = BrowserRuntimeRail(runtime)
    callbacks = rail.get_callbacks()
    assert AgentCallbackEvent.BEFORE_INVOKE in callbacks


def test_before_invoke_persists_current_query_for_continuation() -> None:
    runtime = MagicMock(spec=BrowserAgentRuntime)
    runtime.ensure_runtime_ready = AsyncMock()
    runtime.service = MagicMock()
    runtime.service.mcp_cfg = MagicMock()
    runtime.service._progress_by_session = {}
    session = _FakeSession()
    agent = MagicMock()
    agent.ability_manager = MagicMock()
    ctx = AgentCallbackContext(
        agent=agent,
        session=session,
        inputs=InvokeInputs(query="open example.com", conversation_id=session.get_session_id()),
    )
    rail = BrowserRuntimeRail(runtime)

    _run(rail.before_invoke(ctx))

    assert session.get_state("__browser_subagent_last_task__") == "open example.com"


def test_before_model_call_injects_progress_sections() -> None:
    runtime = MagicMock(spec=BrowserAgentRuntime)
    runtime.service = MagicMock()
    runtime.service._progress_by_session = {}
    builder = SystemPromptBuilder(language="en")
    session = _FakeSession()
    session.update_state(
        {
            "__browser_subagent_progress_state__": {
                "status": "partial",
                "completed_steps": ["Opened home page"],
                "remaining_steps": ["Submit the form"],
                "next_step": "Fill the last required field",
                "completion_evidence": [],
                "missing_requirements": ["Need the user email"],
                "recent_tool_steps": ["browser_navigate: https://example.com"],
                "last_page": {"url": "https://example.com", "title": "Example"},
                "last_screenshot": None,
                "last_worker_final": "Waiting on the email field",
            }
        }
    )
    agent = MagicMock()
    agent.system_prompt_builder = builder
    ctx = AgentCallbackContext(agent=agent, session=session, inputs=InvokeInputs(query="continue"))
    rail = BrowserRuntimeRail(runtime)

    _run(rail.before_model_call(ctx))

    prompt = builder.build()
    assert "Known progress for continuation" in prompt
    assert "Opened home page" in prompt
    assert "<browser_progress>{...}</browser_progress>" in prompt


def test_after_tool_call_records_browser_tool_progress() -> None:
    runtime = MagicMock(spec=BrowserAgentRuntime)
    runtime.service = MagicMock()
    runtime.service._progress_by_session = {}
    runtime.service.record_tool_progress = MagicMock()
    runtime.service.export_progress_state = MagicMock(
        return_value={
            "status": "partial",
            "completed_steps": [],
            "remaining_steps": [],
            "next_step": None,
            "completion_evidence": [],
            "missing_requirements": [],
            "recent_tool_steps": ["browser_navigate: https://example.com"],
            "last_page": {"url": "https://example.com", "title": "Example"},
            "last_screenshot": None,
            "last_worker_final": None,
            "request_id": None,
        }
    )
    session = _FakeSession()
    ctx = AgentCallbackContext(
        agent=MagicMock(),
        session=session,
        inputs=ToolCallInputs(
            tool_name="browser_navigate",
            tool_result=ToolOutput(success=True, data={"page": {"url": "https://example.com", "title": "Example"}}),
        ),
    )
    rail = BrowserRuntimeRail(runtime)

    _run(rail.after_tool_call(ctx))

    runtime.service.record_tool_progress.assert_called_once()
    assert session.get_state("__browser_subagent_progress_state__")["status"] == "partial"


def test_after_invoke_rewrites_max_iteration_with_failure_summary() -> None:
    runtime = MagicMock(spec=BrowserAgentRuntime)
    runtime.service = MagicMock()
    runtime.service._progress_by_session = {
        "browser-session": MagicMock(
            last_page_url="https://example.com",
            last_page_title="Example",
            last_screenshot=None,
        )
    }
    runtime.service.export_progress_state = MagicMock(return_value={"status": "partial"})
    runtime.service.build_failure_summary = MagicMock(return_value="Failure summary for continuation:\n- step")
    session = _FakeSession()
    session.update_state(
        {
            "__browser_subagent_last_task__": "Finish the checkout flow",
            "__browser_subagent_progress_state__": {"status": "partial"},
        }
    )
    result = {"output": MAX_ITERATION_MESSAGE, "result_type": "error"}
    ctx = AgentCallbackContext(
        agent=MagicMock(),
        session=session,
        inputs=InvokeInputs(query="Finish checkout", result=result),
    )
    rail = BrowserRuntimeRail(runtime)

    _run(rail.after_invoke(ctx))

    assert result["output"].startswith("Failure summary for continuation:")
    assert result["failure_summary"].startswith("Failure summary for continuation:")
    assert result["progress_state"] == {"status": "partial"}


def test_after_invoke_promotes_completed_progress_block() -> None:
    runtime = MagicMock(spec=BrowserAgentRuntime)
    runtime.service = MagicMock()
    runtime.service.get_progress_state = MagicMock(
        return_value=MagicMock(is_empty=MagicMock(return_value=False))
    )
    runtime.service.record_worker_progress = MagicMock()
    runtime.service.export_progress_state = MagicMock(
        return_value={
            "status": "completed",
            "completion_evidence": ["Saved the settings page"],
        }
    )
    runtime.service.should_treat_as_completed = MagicMock(return_value=True)
    session = _FakeSession()
    result = {
        "output": (
            "Settings saved successfully.\n"
            '<browser_progress>{"status":"completed","completed_steps":["Opened settings"],'
            '"completion_evidence":["Saved the settings page"]}</browser_progress>'
        ),
        "result_type": "error",
    }
    ctx = AgentCallbackContext(
        agent=MagicMock(),
        session=session,
        inputs=InvokeInputs(query="Save settings", result=result),
    )
    rail = BrowserRuntimeRail(runtime)

    _run(rail.after_invoke(ctx))

    assert result["result_type"] == "answer"
    assert result["output"] == "Settings saved successfully."
    assert session.get_state("__browser_subagent_progress_state__") == {}
