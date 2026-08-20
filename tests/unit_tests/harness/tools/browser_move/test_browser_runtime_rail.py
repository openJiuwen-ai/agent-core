#!/usr/bin/env python
# coding: utf-8
"""Tests for BrowserRuntimeRail lifecycle hook."""
# pylint: disable=protected-access

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openjiuwen.core.foundation.tool import McpServerConfig, ToolInfo
from openjiuwen.core.foundation.llm.schema.message import ToolMessage
from openjiuwen.core.runner import Runner
from openjiuwen.core.single_agent.ability_manager import AbilityManager
from openjiuwen.core.single_agent.prompts.builder import SystemPromptBuilder
from openjiuwen.harness.tools.browser_move.playwright_runtime.browser_capabilities import (
    CORE_BROWSER_TOOL_NAMES,
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
    agent = MagicMock()
    agent.deep_config = SimpleNamespace(enable_read_image_multimodal=True)
    return AgentCallbackContext(agent=agent)


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


def _make_bare_runtime() -> BrowserAgentRuntime:
    runtime = BrowserAgentRuntime.__new__(BrowserAgentRuntime)
    runtime._page_generation = 0
    runtime._reference_generations = {}
    runtime._selector_primary_links = {}
    runtime._last_observed_url = ""
    return runtime


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


def test_before_invoke_with_none_allowlist_defaults_to_core() -> None:
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
    ctx.agent.ability_manager.set_mcp_tool_allowlist.assert_called_once_with(
        runtime.service.mcp_cfg,
        CORE_BROWSER_TOOL_NAMES,
    )


def test_before_invoke_removes_screenshot_for_non_multimodal_model() -> None:
    runtime = MagicMock(spec=BrowserAgentRuntime)
    runtime.ensure_runtime_ready = AsyncMock()
    runtime.service = MagicMock()
    runtime.service.mcp_cfg = _playwright_mcp_config()
    runtime.service.allowed_tool_names = (
        "browser_click",
        "browser_take_screenshot",
        "browser_snapshot",
    )
    rail = BrowserRuntimeRail(runtime)
    ctx = _make_ctx()
    ctx.agent.deep_config.enable_read_image_multimodal = False
    ctx.agent.ability_manager = MagicMock()

    _run(rail.before_invoke(ctx))

    ctx.agent.ability_manager.set_mcp_tool_allowlist.assert_called_once_with(
        runtime.service.mcp_cfg,
        ("browser_click", "browser_snapshot"),
    )


def test_before_invoke_builds_non_multimodal_allowlist_from_core() -> None:
    runtime = MagicMock(spec=BrowserAgentRuntime)
    runtime.ensure_runtime_ready = AsyncMock()
    runtime.service = MagicMock()
    runtime.service.mcp_cfg = _playwright_mcp_config()
    runtime.service.allowed_tool_names = None
    rail = BrowserRuntimeRail(runtime)
    ctx = _make_ctx()
    ctx.agent.deep_config.enable_read_image_multimodal = False
    ctx.agent.ability_manager = MagicMock()
    _run(rail.before_invoke(ctx))

    ctx.agent.ability_manager.set_mcp_tool_allowlist.assert_called_once_with(
        runtime.service.mcp_cfg,
        tuple(
            tool_name
            for tool_name in CORE_BROWSER_TOOL_NAMES
            if tool_name != "browser_take_screenshot"
        ),
    )


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
        visible_names = {tool.name for tool in _run(agent.ability_manager.list_tool_info())}

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


def test_before_tool_call_normalizes_explicit_snapshot_ref_target() -> None:
    runtime = MagicMock(spec=BrowserAgentRuntime)
    rail = BrowserRuntimeRail(runtime)
    ctx = AgentCallbackContext(
        agent=MagicMock(),
        inputs=ToolCallInputs(
            tool_name="mcp_playwright-official_browser_click",
            tool_args={
                "target": "ref=f2e36",
                "ref": "[ref=f2e36]",
                "element": "search input",
            },
        ),
    )

    _run(rail.before_tool_call(ctx))

    assert ctx.inputs.tool_args == {
        "target": "f2e36",
        "ref": "f2e36",
        "element": "search input",
    }


def test_before_tool_call_normalizes_bracketed_refs_in_json_arguments() -> None:
    runtime = MagicMock(spec=BrowserAgentRuntime)
    rail = BrowserRuntimeRail(runtime)
    ctx = AgentCallbackContext(
        agent=MagicMock(),
        inputs=ToolCallInputs(
            tool_name="mcp_playwright-official_browser_drag",
            tool_args=('{"startTarget": "[ref=f1e2]", "endTarget": "ref=f1e9", "element": "card"}'),
        ),
    )

    _run(rail.before_tool_call(ctx))

    assert '"startTarget": "f1e2"' in ctx.inputs.tool_args
    assert '"endTarget": "f1e9"' in ctx.inputs.tool_args


def test_before_tool_call_preserves_css_selector_targets() -> None:
    runtime = MagicMock(spec=BrowserAgentRuntime)
    rail = BrowserRuntimeRail(runtime)
    args = {"target": "#search-input", "element": "search input"}
    ctx = AgentCallbackContext(
        agent=MagicMock(),
        inputs=ToolCallInputs(
            tool_name="mcp_playwright-official_browser_click",
            tool_args=args,
        ),
    )

    _run(rail.before_tool_call(ctx))

    assert ctx.inputs.tool_args is args


def test_before_tool_call_rewrites_card_primary_link_click_to_navigation() -> None:
    runtime = MagicMock(spec=BrowserAgentRuntime)
    runtime.resolve_primary_link.return_value = "https://example.com/item/1"
    rail = BrowserRuntimeRail(runtime)
    ctx = AgentCallbackContext(
        agent=MagicMock(),
        inputs=ToolCallInputs(
            tool_name="mcp_playwright-official_browser_click",
            tool_args={"selector": ".result-card:nth-of-type(1)"},
        ),
    )

    _run(rail.before_tool_call(ctx))

    assert ctx.inputs.tool_name == "mcp_playwright-official_browser_navigate"
    assert ctx.inputs.tool_args == {"url": "https://example.com/item/1"}


def test_before_tool_call_rejects_batch_screenshot_without_image_support() -> None:
    runtime = MagicMock(spec=BrowserAgentRuntime)
    rail = BrowserRuntimeRail(runtime)
    agent = MagicMock()
    agent.deep_config = SimpleNamespace(enable_read_image_multimodal=False)
    ctx = AgentCallbackContext(
        agent=agent,
        inputs=ToolCallInputs(
            tool_name="browser_batch_interact",
            tool_args={
                "steps": [
                    {"op": "click", "selector": "#search"},
                    {"op": "screenshot", "path": "result.png"},
                ]
            },
        ),
    )

    with pytest.raises(ValueError, match="Screenshot input is unavailable"):
        _run(rail.before_tool_call(ctx))


def test_navigation_invalidates_snapshot_refs_from_older_generation() -> None:
    runtime = _make_bare_runtime()
    runtime.record_tool_reference_state(
        tool_name="mcp_playwright_browser_snapshot",
        tool_args={},
        tool_result='textbox "Search" [ref=f1e2]',
    )

    runtime.validate_reference_values(("f1e2",))
    assert runtime.generation_id == "g0"

    runtime.record_tool_reference_state(
        tool_name="mcp_playwright_browser_navigate",
        tool_args={"url": "https://example.com/results"},
        tool_result={"ok": True, "url": "https://example.com/results"},
    )

    assert runtime.generation_id == "g1"
    with pytest.raises(ValueError, match="older page generation"):
        runtime.validate_reference_values(("f1e2",))


def test_after_snapshot_attaches_compact_page_state_to_tool_message() -> None:
    runtime = _make_bare_runtime()
    rail = BrowserRuntimeRail(runtime)
    tool_message = ToolMessage(
        tool_call_id="snapshot-call",
        content='textbox "Search" [ref=f1e2]',
    )
    ctx = AgentCallbackContext(
        agent=MagicMock(),
        inputs=ToolCallInputs(
            tool_name="mcp_playwright_browser_snapshot",
            tool_args={},
            tool_result='textbox "Search" [ref=f1e2]',
            tool_msg=tool_message,
        ),
    )

    _run(rail.after_tool_call(ctx))

    assert "<browser_page_state>" in tool_message.content
    assert '"generation_id":"g0"' in tool_message.content
    assert '"ref":"f1e2"' in tool_message.content


def test_click_result_url_change_invalidates_snapshot_refs() -> None:
    runtime = _make_bare_runtime()
    runtime._last_observed_url = "https://example.com/"
    runtime.record_tool_reference_state(
        tool_name="mcp_playwright_browser_snapshot",
        tool_args={},
        tool_result='link "Result" [ref=e7]',
    )

    runtime.record_tool_reference_state(
        tool_name="mcp_playwright_browser_click",
        tool_args={"ref": "e7"},
        tool_result="Page URL: https://example.com/result/7",
    )

    assert runtime.generation_id == "g1"
    with pytest.raises(ValueError, match="e7"):
        runtime.validate_reference_values(("e7",))


def test_first_observed_url_after_snapshot_invalidates_unknown_page_refs() -> None:
    runtime = _make_bare_runtime()
    runtime.record_tool_reference_state(
        tool_name="mcp_playwright_browser_snapshot",
        tool_args={},
        tool_result='button "Open" [ref=e3]',
    )

    runtime.record_tool_reference_state(
        tool_name="mcp_playwright_browser_click",
        tool_args={"ref": "e3"},
        tool_result="Page URL: https://example.com/opened",
    )

    assert runtime.generation_id == "g1"
    with pytest.raises(ValueError, match="e3"):
        runtime.validate_reference_values(("e3",))


def test_card_primary_link_mapping_is_generation_scoped() -> None:
    runtime = _make_bare_runtime()
    runtime.register_card_primary_links(
        {
            "cards": [
                {
                    "selector_hint": ".card:nth-of-type(1)",
                    "primary_link": "https://example.com/item/1",
                }
            ]
        }
    )

    assert runtime.resolve_primary_link(
        {"selector": ".card:nth-of-type(1)"}
    ) == "https://example.com/item/1"

    runtime._advance_page_generation()

    assert runtime.resolve_primary_link(
        {"selector": ".card:nth-of-type(1)"}
    ) == ""


def test_compact_rpc_wrapper_is_transparent_to_probe_parsing() -> None:
    runtime = _make_bare_runtime()
    raw = {
        "__browser_compact_rpc__": True,
        "payload": {
            "content": [
                {"type": "text", "text": '{"ok":true,"elements":[]}'}
            ]
        },
        "rpc_metrics": {"transport_invoke_elapsed_ms": 4},
    }

    assert runtime._unwrap_mcp_text_result(raw) == (
        '{"ok":true,"elements":[]}'
    )


def test_phase_plan_uses_explicit_completion_conditions_and_large_budgets() -> None:
    state = BrowserRuntimeRail._build_phase_state(
        "Compare products, apply filters, and complete the checkout form"
    )

    assert state["task_type"] == "complex"
    assert list(state["phases"]) == [
        "navigation",
        "form",
        "filtering",
        "extraction",
    ]
    assert state["phases"]["navigation"]["budget"] == 12
    assert state["phases"]["form"]["budget"] == 24
    assert state["phases"]["filtering"]["budget"] == 20
    assert state["phases"]["extraction"]["budget"] == 20
    assert all(
        phase["completion_condition"]
        for phase in state["phases"].values()
    )


def test_known_url_must_be_navigated_before_selector_exploration() -> None:
    session = _FakeSession()
    session.update_state(
        {
            "__browser_phase_budget_state__": BrowserRuntimeRail._build_phase_state(
                "Inspect https://example.com/products"
            )
        }
    )

    with pytest.raises(ValueError, match="Navigate to it directly"):
        BrowserRuntimeRail._consume_phase_budget(
            session,
            "mcp_playwright_browser_snapshot",
            {},
        )


def test_exhausted_phase_budget_requires_replan() -> None:
    session = _FakeSession()
    state = BrowserRuntimeRail._build_phase_state("Search for OpenJiuwen")
    state["phases"]["navigation"]["attempts"] = (
        state["phases"]["navigation"]["budget"]
    )
    session.update_state({"__browser_phase_budget_state__": state})

    with pytest.raises(ValueError, match="budget exhausted"):
        BrowserRuntimeRail._consume_phase_budget(
            session,
            "mcp_playwright_browser_navigate",
            {"url": "https://www.baidu.com/s?wd=OpenJiuwen"},
        )

    phase = session.get_state("__browser_phase_budget_state__")["phases"][
        "navigation"
    ]
    assert phase["status"] == "replan_required"


def test_batch_phase_signature_changes_when_steps_change() -> None:
    first = BrowserRuntimeRail._phase_action_signature(
        "browser_batch_interact",
        {
            "steps": [
                {"op": "fill", "selector": "#q", "value": "first"},
                {"op": "press", "key": "Enter"},
            ]
        },
    )
    second = BrowserRuntimeRail._phase_action_signature(
        "browser_batch_interact",
        {
            "steps": [
                {"op": "fill", "selector": "#search", "value": "second"},
                {"op": "wait_for_url", "url_contains": "/results"},
            ]
        },
    )

    assert first != second


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


def test_before_model_call_explains_screenshot_is_disabled_without_image_support() -> None:
    runtime = MagicMock(spec=BrowserAgentRuntime)
    builder = SystemPromptBuilder(language="en")
    agent = MagicMock()
    agent.deep_config = SimpleNamespace(enable_read_image_multimodal=False)
    agent.system_prompt_builder = builder
    rail = BrowserRuntimeRail(runtime)

    _run(rail.before_model_call(AgentCallbackContext(agent=agent)))

    prompt = builder.build()
    assert "Image input is unavailable or unverified" in prompt
    assert "browser_take_screenshot" in prompt
    assert "op=screenshot" in prompt


def test_before_model_call_limits_screenshot_use_for_multimodal_model() -> None:
    runtime = MagicMock(spec=BrowserAgentRuntime)
    builder = SystemPromptBuilder(language="en")
    agent = MagicMock()
    agent.deep_config = SimpleNamespace(enable_read_image_multimodal=True)
    agent.system_prompt_builder = builder
    rail = BrowserRuntimeRail(runtime)

    _run(rail.before_model_call(AgentCallbackContext(agent=agent)))

    prompt = builder.build()
    assert "The current model can inspect image input" in prompt
    assert "pixel-level visual evidence" in prompt


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
    runtime.service.get_progress_state = MagicMock(return_value=MagicMock(is_empty=MagicMock(return_value=False)))
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
