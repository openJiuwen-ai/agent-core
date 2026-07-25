#!/usr/bin/env python
# coding: utf-8
"""Tests for BrowserRuntimeRail lifecycle hook."""
# pylint: disable=protected-access

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from openjiuwen.core.foundation.tool import McpServerConfig, ToolInfo
from openjiuwen.core.runner import Runner
from openjiuwen.core.single_agent.ability_manager import AbilityManager
from openjiuwen.core.single_agent.prompts.builder import SystemPromptBuilder
from openjiuwen.harness.tools.browser_move.playwright_runtime.browser_capabilities import (
    resolve_browser_capabilities,
)
from openjiuwen.harness.tools.browser_move.playwright_runtime import runtime as runtime_module
from openjiuwen.harness.tools.browser_move.playwright_runtime.runtime import BrowserAgentRuntime, BrowserRuntimeRail
from openjiuwen.harness.tools.browser_move.playwright_runtime.service import MAX_ITERATION_MESSAGE
from openjiuwen.harness.tools.base_tool import ToolOutput
from openjiuwen.harness.prompts.prompt_attachment_manager import PromptAttachmentManager
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext, AgentRail
from openjiuwen.core.single_agent.rail.base import InvokeInputs, ToolCallInputs


def _run(coro):
    return asyncio.run(coro)


def _make_ctx() -> AgentCallbackContext:
    return AgentCallbackContext(agent=MagicMock())


def _playwright_mcp_config() -> McpServerConfig:
    return McpServerConfig(
        server_id="playwright_official_stdio",
        server_name="playwright-official",
        server_path="stdio://playwright",
        client_type="stdio",
    )


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


def test_reset_active_browser_runtimes_resets_all_live_instances() -> None:
    class _Runtime:
        def __init__(self) -> None:
            self.reset = AsyncMock()

    first = _Runtime()
    second = _Runtime()
    runtime_module._ACTIVE_BROWSER_RUNTIMES.clear()
    runtime_module._ACTIVE_BROWSER_RUNTIMES.add(first)
    runtime_module._ACTIVE_BROWSER_RUNTIMES.add(second)

    assert _run(runtime_module.reset_active_browser_runtimes()) == 2
    first.reset.assert_awaited_once()
    second.reset.assert_awaited_once()
    runtime_module._ACTIVE_BROWSER_RUNTIMES.clear()


def test_before_invoke_calls_ensure_runtime_ready() -> None:
    runtime = MagicMock(spec=BrowserAgentRuntime)
    runtime.ensure_runtime_ready = AsyncMock()
    runtime.service = MagicMock()
    runtime.service.mcp_cfg = MagicMock()
    runtime.service.allowed_tool_names = ("browser_click", "browser_pdf_save")
    rail = BrowserRuntimeRail(runtime)
    ctx = _make_ctx()
    ctx.agent.ability_manager = MagicMock()
    _run(rail.before_invoke(ctx))
    runtime.ensure_runtime_ready.assert_called_once_with()
    ctx.agent.ability_manager.add.assert_called_once_with(runtime.service.mcp_cfg)
    ctx.agent.ability_manager.set_mcp_tool_allowlist.assert_called_once_with(
        runtime.service.mcp_cfg,
        runtime.service.allowed_tool_names,
    )


def test_before_invoke_with_none_allowlist_preserves_unrestricted_mode() -> None:
    runtime = MagicMock(spec=BrowserAgentRuntime)
    runtime.ensure_runtime_ready = AsyncMock()
    runtime.service = MagicMock()
    runtime.service.mcp_cfg = MagicMock()
    runtime.service.allowed_tool_names = None
    rail = BrowserRuntimeRail(runtime)
    ctx = _make_ctx()
    ctx.agent.ability_manager = MagicMock()

    _run(rail.before_invoke(ctx))

    ctx.agent.ability_manager.add.assert_called_once_with(runtime.service.mcp_cfg)
    ctx.agent.ability_manager.set_mcp_tool_allowlist.assert_not_called()


def test_pdf_allowlist_filters_active_browser_agent_schemas() -> None:
    runtime = MagicMock(spec=BrowserAgentRuntime)
    runtime.ensure_runtime_ready = AsyncMock()
    runtime.service = MagicMock()
    runtime.service.mcp_cfg = _playwright_mcp_config()
    runtime.service.allowed_tool_names = resolve_browser_capabilities(["pdf"]).allowed_tool_names
    rail = BrowserRuntimeRail(runtime)
    agent = MagicMock()
    agent.ability_manager = AbilityManager()
    ctx = AgentCallbackContext(agent=agent)

    _run(rail.before_invoke(ctx))

    registered_tools = [
        ToolInfo(name="browser_click", description="core", parameters={}),
        ToolInfo(name="browser_pdf_save", description="pdf", parameters={}),
        ToolInfo(name="browser_get_config", description="config", parameters={}),
        ToolInfo(name="browser_cookie_list", description="storage", parameters={}),
        ToolInfo(name="browser_mouse_click_xy", description="vision", parameters={}),
    ]
    with patch.object(
        Runner.resource_mgr,
        "get_mcp_tool_infos",
        new=AsyncMock(return_value=registered_tools),
    ):
        visible_names = {
            tool.name for tool in _run(agent.ability_manager.list_tool_info())
        }

    assert "mcp_playwright-official_browser_click" in visible_names
    assert "mcp_playwright-official_browser_pdf_save" in visible_names
    assert "mcp_playwright-official_browser_get_config" not in visible_names
    assert "mcp_playwright-official_browser_cookie_list" not in visible_names
    assert "mcp_playwright-official_browser_mouse_click_xy" not in visible_names


def test_before_invoke_called_twice_delegates_twice() -> None:
    """Idempotency is BrowserAgentRuntime's responsibility; rail always delegates."""
    runtime = MagicMock(spec=BrowserAgentRuntime)
    runtime.ensure_runtime_ready = AsyncMock()
    runtime.service = MagicMock()
    runtime.service.mcp_cfg = MagicMock()
    runtime.service.allowed_tool_names = ("browser_click", "browser_pdf_save")
    rail = BrowserRuntimeRail(runtime)
    ctx1 = _make_ctx()
    ctx1.agent.ability_manager = MagicMock()
    ctx2 = _make_ctx()
    ctx2.agent.ability_manager = MagicMock()
    _run(rail.before_invoke(ctx1))
    _run(rail.before_invoke(ctx2))
    assert runtime.ensure_runtime_ready.call_count == 2
    ctx1.agent.ability_manager.set_mcp_tool_allowlist.assert_called_once_with(
        runtime.service.mcp_cfg,
        runtime.service.allowed_tool_names,
    )
    ctx2.agent.ability_manager.set_mcp_tool_allowlist.assert_called_once_with(
        runtime.service.mcp_cfg,
        runtime.service.allowed_tool_names,
    )


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


def test_before_model_call_skips_dynamic_progress_without_attachment_manager() -> None:
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
    assert "<browser_progress>{...}</browser_progress>" in prompt
    assert "Opened home page" not in prompt
    assert not builder.has_section("browser_progress_continuation")


def test_before_model_call_injects_progress_attachment() -> None:
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
    agent.prompt_attachment_manager = PromptAttachmentManager()
    ctx = AgentCallbackContext(agent=agent, session=session, inputs=InvokeInputs(query="continue"))
    rail = BrowserRuntimeRail(runtime)

    _run(rail.before_model_call(ctx))

    prompt = builder.build()
    assert "<browser_progress>{...}</browser_progress>" in prompt
    assert "Opened home page" not in prompt
    assert not builder.has_section("browser_progress_continuation")
    items = _run(agent.prompt_attachment_manager.collect_for_session("browser-session"))
    assert len(items) == 1
    assert items[0].section == "browser_progress_continuation"
    assert "Opened home page" in (items[0].content or "")


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
