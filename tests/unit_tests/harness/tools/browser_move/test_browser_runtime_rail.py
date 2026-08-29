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
from openjiuwen.core.foundation.llm import ToolCall
from openjiuwen.core.foundation.llm.schema.message import ToolMessage, UserMessage
from openjiuwen.core.runner import Runner
from openjiuwen.core.single_agent.ability_manager import AbilityManager
from openjiuwen.core.single_agent.prompts.builder import SystemPromptBuilder
from openjiuwen.harness.tools.browser_move.playwright_runtime.browser_capabilities import (
    CORE_BROWSER_TOOL_NAMES,
    resolve_browser_capabilities,
)
from openjiuwen.harness.tools.browser_move.playwright_runtime.browser_working_context import (
    BrowserWorkingContextStore,
)
from openjiuwen.harness.tools.browser_move.playwright_runtime import runtime as runtime_module
from openjiuwen.harness.tools.browser_move.playwright_runtime.runtime import BrowserAgentRuntime, BrowserRuntimeRail
from openjiuwen.harness.tools.browser_move.playwright_runtime.service import MAX_ITERATION_MESSAGE
from openjiuwen.harness.tools.base_tool import ToolOutput
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext, AgentRail
from openjiuwen.core.single_agent.rail.base import InvokeInputs, ModelCallInputs, ToolCallInputs


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
        tuple(tool_name for tool_name in CORE_BROWSER_TOOL_NAMES if tool_name != "browser_take_screenshot"),
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
        "element": "search input",
    }


def test_before_tool_call_canonicalizes_playwright_official_server_separator() -> None:
    runtime = MagicMock(spec=BrowserAgentRuntime)
    rail = BrowserRuntimeRail(runtime)
    ctx = AgentCallbackContext(
        agent=MagicMock(),
        inputs=ToolCallInputs(
            tool_name="mcp_playwright-official-browser_evaluate",
            tool_args={"function": "() => document.title"},
        ),
    )

    _run(rail.before_tool_call(ctx))

    assert ctx.inputs.tool_name == "mcp_playwright-official_browser_evaluate"


def test_before_tool_call_canonicalizes_bare_mcp_tool_name() -> None:
    runtime = MagicMock(spec=BrowserAgentRuntime)
    runtime.service.allowed_tool_names = ("browser_navigate",)
    runtime.service.mcp_cfg.server_name = "playwright-official"
    runtime.semantic_progress = {}
    rail = BrowserRuntimeRail(runtime)
    ctx = AgentCallbackContext(
        agent=MagicMock(),
        inputs=ToolCallInputs(
            tool_call=ToolCall(id="navigate-1", type="function", name="browser_navigate", arguments="{}"),
            tool_name="browser_navigate",
            tool_args={"url": "https://example.com"},
        ),
    )

    _run(rail.before_tool_call(ctx))

    assert ctx.inputs.tool_name == "mcp_playwright-official_browser_navigate"


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


def test_before_tool_call_rewrites_direct_mcp_ref_aliases() -> None:
    runtime = MagicMock(spec=BrowserAgentRuntime)
    rail = BrowserRuntimeRail(runtime)
    ctx = AgentCallbackContext(
        agent=MagicMock(),
        inputs=ToolCallInputs(
            tool_name="mcp_playwright-official_browser_drag",
            tool_args={
                "startRef": "[ref=f1e2]",
                "endRef": "ref=f1e9",
                "element": "card",
            },
        ),
    )

    _run(rail.before_tool_call(ctx))

    assert ctx.inputs.tool_args == {
        "startTarget": "f1e2",
        "endTarget": "f1e9",
        "element": "card",
    }


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

    _run(rail.before_tool_call(ctx))

    assert ctx.inputs.tool_result["status"] == "denied"
    assert ctx.inputs.tool_result["error"]["code"] == "browser_image_input_unavailable"
    assert ctx.inputs.tool_msg is not None
    assert ctx.extra["_skip_tool_calls"]


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

    assert '"observation":"compact_page_state"' in tool_message.content
    assert '"generation_id":"g0"' in tool_message.content
    assert '"target_id":"t_g0_1"' in tool_message.content
    assert "[ref=f1e2]" not in tool_message.content
    assert '"ref":"f1e2"' not in tool_message.content


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

    assert runtime.resolve_primary_link({"selector": ".card:nth-of-type(1)"}) == "https://example.com/item/1"

    runtime._advance_page_generation()

    assert runtime.resolve_primary_link({"selector": ".card:nth-of-type(1)"}) == ""


def test_compact_rpc_wrapper_is_transparent_to_probe_parsing() -> None:
    runtime = _make_bare_runtime()
    raw = {
        "__browser_compact_rpc__": True,
        "payload": {"content": [{"type": "text", "text": '{"ok":true,"elements":[]}'}]},
        "rpc_metrics": {"transport_invoke_elapsed_ms": 4},
    }

    assert runtime._unwrap_mcp_text_result(raw) == ('{"ok":true,"elements":[]}')


def test_phase_plan_uses_explicit_completion_conditions_and_large_budgets() -> None:
    state = BrowserRuntimeRail._build_phase_state("Compare products, apply filters, and complete the checkout form")

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
    assert all(phase["completion_condition"] for phase in state["phases"].values())


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
    state["phases"]["navigation"]["attempts"] = state["phases"]["navigation"]["budget"]
    session.update_state({"__browser_phase_budget_state__": state})

    with pytest.raises(ValueError, match="budget exhausted"):
        BrowserRuntimeRail._consume_phase_budget(
            session,
            "mcp_playwright_browser_navigate",
            {"url": "https://www.baidu.com/s?wd=OpenJiuwen"},
        )

    phase = session.get_state("__browser_phase_budget_state__")["phases"]["navigation"]
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


def test_price_field_extraction_is_not_misclassified_as_filtering() -> None:
    phase = BrowserRuntimeRail._classify_tool_phase(
        "browser_batch_interact",
        {
            "steps": [
                {"op": "extract_text", "field": "price"},
                {"op": "extract_text", "field": "title"},
            ]
        },
        {"current_phase": "extraction"},
    )

    assert phase == "extraction"


def test_semantic_action_signature_ignores_selector_generation_and_equivalent_tool() -> None:
    direct = BrowserRuntimeRail._phase_action_signature(
        "mcp_playwright-official_browser_click",
        {"target": "f1e2", "generation_id": "g1"},
    )
    batch = BrowserRuntimeRail._phase_action_signature(
        "browser_batch_interact",
        {"generation_id": "g8", "steps": [{"op": "click", "selector": "#different"}]},
    )

    assert direct == batch


def test_phase_attempts_do_not_reset_after_material_replan() -> None:
    session = _FakeSession()
    state = BrowserRuntimeRail._build_phase_state("Filter products by price")
    phase = state["phases"]["filtering"]
    phase["attempts"] = 3
    state["current_phase"] = "filtering"
    state["status"] = "replan_required"
    state["replan_required"] = True
    state["blocked_strategy"] = "script_exploration"
    state["failed_strategies"] = ["script_exploration"]
    session.update_state({"__browser_phase_budget_state__": state})

    action_class = BrowserRuntimeRail._consume_phase_budget(
        session,
        "browser_batch_interact",
        {"steps": [{"op": "click", "text": "100 to 200"}]},
    )

    updated = session.get_state("__browser_phase_budget_state__")["phases"]["filtering"]
    assert action_class == "filtering"
    assert updated["attempts"] == 4
    task_state = session.get_state("__browser_phase_budget_state__")
    assert task_state["replan_count"] == 1
    assert task_state["replan_trial_pending"] is True


def test_semantic_loop_marks_current_phase_replan_required() -> None:
    runtime = MagicMock(spec=BrowserAgentRuntime)
    runtime.semantic_progress = {
        "revision": 4,
        "progress": "state_revisit",
        "consecutive_no_progress": 3,
        "state_revisit_count": 3,
        "aba_loop": True,
        "repeated_filter_state": True,
        "replan_required": True,
        "replan_reason": ["three_semantic_state_revisits"],
        "semantic_state": {"field_coverage": ["title", "price"]},
    }
    rail = BrowserRuntimeRail(runtime)
    session = _FakeSession()
    state = BrowserRuntimeRail._build_phase_state("Filter products by price")
    state["current_phase"] = "filtering"
    state["phases"]["filtering"]["last_semantic_signature"] = '[{"op":"click"}]'
    session.update_state({"__browser_phase_budget_state__": state})

    rail._sync_semantic_progress(session)

    updated = session.get_state("__browser_phase_budget_state__")
    assert updated["semantic_revision"] == 4
    assert updated["replan_count"] == 0
    assert updated["field_coverage"] == ["price", "title"]
    assert updated["status"] == "replan_required"
    assert updated["replan_required"] is True
    assert updated["failed_strategies"] == []


def test_new_evidence_clears_pending_semantic_replan_trial() -> None:
    session = _FakeSession()
    state = BrowserRuntimeRail._build_phase_state("inspect alternating search results")
    state.update(
        {
            "status": "replan_trial",
            "replan_required": True,
            "replan_trial_pending": True,
            "trial_strategy": "structured_extraction",
        }
    )
    session.update_state({"__browser_phase_budget_state__": state})

    delta = BrowserRuntimeRail._record_phase_result(
        session,
        "browser_probe_cards",
        {},
        {
            "ok": True,
            "cards": [
                {
                    "region": "main_result",
                    "kind": "result",
                    "title": "Repeated result",
                    "primary_link": "https://example.test/result",
                }
            ],
        },
    )

    updated = session.get_state("__browser_phase_budget_state__")
    assert delta["evidence_added"] is True
    assert delta["recovered"] is True
    assert updated["replan_trial_pending"] is False
    assert updated["replan_required"] is False
    assert updated["status"] == "in_progress"


def test_terminal_state_is_sticky_across_semantic_progress_and_worker_report() -> None:
    session = _FakeSession()
    state = BrowserRuntimeRail._build_phase_state("extract title")
    state.update(
        {
            "status": "blocked",
            "blockers": ["semantic_replan_budget_exhausted"],
            "replan_required": True,
            "semantic_revision": 1,
        }
    )
    session.update_state({"__browser_phase_budget_state__": state})

    recovered = BrowserWorkingContextStore.sync_semantic_progress(
        session,
        {
            "revision": 2,
            "progress": "progress",
            "observable_progress": True,
            "semantic_state": {"url": "https://example.test/new"},
        },
    )
    BrowserRuntimeRail._apply_worker_progress_to_task_state(
        session,
        {"status": "completed", "blockers": []},
        "done",
    )

    updated = session.get_state("__browser_phase_budget_state__")
    assert recovered is False
    assert updated["status"] == "blocked"
    assert updated["blockers"] == ["semantic_replan_budget_exhausted"]
    with pytest.raises(ValueError, match="already blocked"):
        BrowserRuntimeRail._consume_phase_budget(
            session,
            "browser_navigate",
            {"url": "https://example.test/again"},
        )


@pytest.mark.parametrize("status", ["blocked", "partial", "completed"])
def test_terminal_state_returns_structured_denial(status: str) -> None:
    runtime = MagicMock(spec=BrowserAgentRuntime)
    runtime.semantic_progress = {}
    rail = BrowserRuntimeRail(runtime)
    state = BrowserRuntimeRail._build_phase_state("inspect results")
    state.update({"status": status, "blockers": ["captcha"]})
    session = _FakeSession()
    session.update_state({"__browser_phase_budget_state__": state})
    ctx = AgentCallbackContext(
        agent=MagicMock(),
        session=session,
        inputs=ToolCallInputs(
            tool_call=ToolCall(id="probe-1", type="function", name="browser_probe_cards", arguments="{}"),
            tool_name="browser_probe_cards",
            tool_args={},
        ),
    )

    _run(rail.before_tool_call(ctx))

    assert ctx.inputs.tool_result["error"]["code"] == "browser_task_terminal"
    assert ctx.inputs.tool_result["error"]["terminal"] is True
    assert ctx.extra["_skip_tool_calls"] == {"probe-1": True}


def test_comparison_evidence_requires_distinct_bilibili_sort_slots() -> None:
    state = BrowserRuntimeRail._build_phase_state("对比B站 Python 搜索综合和最新结果的标题")
    comprehensive = {
        "generation_id": "g2",
        "page_state": {"url": "https://search.bilibili.com/all?keyword=Python"},
        "cards": [{"title": "Comprehensive result", "result_index": 1, "generation_id": "g2"}],
    }
    latest = {
        "generation_id": "g3",
        "page_state": {"url": "https://search.bilibili.com/all?keyword=Python&order=pubdate"},
        "cards": [{"title": "Latest result", "result_index": 1, "generation_id": "g3"}],
    }

    BrowserRuntimeRail._record_structured_evidence(
        state,
        comprehensive,
        tool_name="browser_probe_cards",
        tool_args={},
    )
    assert [slot["variant"] for slot in state["evidence_slots"]] == ["comprehensive"]
    assert any("latest" in item for item in BrowserRuntimeRail._missing_completion_requirements(state))

    BrowserRuntimeRail._record_structured_evidence(
        state,
        latest,
        tool_name="browser_probe_cards",
        tool_args={},
    )
    assert {slot["variant"] for slot in state["evidence_slots"]} == {"comprehensive", "latest"}
    latest_slot = next(slot for slot in state["evidence_slots"] if slot["variant"] == "latest")
    assert latest_slot == {
        "entity": "bilibili_search_result",
        "variant": "latest",
        "field": "title",
        "value": "Latest result",
        "source": "https://search.bilibili.com/all?keyword=Python&order=pubdate",
        "generation": "g3",
    }
    assert BrowserRuntimeRail._missing_completion_requirements(state) == []


def test_retryable_model_failure_retries_once_then_returns_structured_error() -> None:
    runtime = MagicMock(spec=BrowserAgentRuntime)
    rail = BrowserRuntimeRail(runtime)
    session = _FakeSession()
    session.update_state({"__browser_phase_budget_state__": BrowserRuntimeRail._build_phase_state("find a title")})
    ctx = AgentCallbackContext(
        agent=MagicMock(),
        session=session,
        extra={"__browser_task_deadline__": runtime_module.time.monotonic() + 60},
        exception=RuntimeError("model_provider_response_error: code=504"),
    )

    _run(rail.on_model_exception(ctx))
    assert ctx.consume_retry_request() is not None

    ctx.retry_attempt = 1
    _run(rail.on_model_exception(ctx))
    finish = ctx.consume_force_finish()
    assert finish is not None
    assert finish.result["result_type"] == "error"
    assert '"status":"failed"' in finish.result["output"]


def test_ability_manager_consumes_per_call_skip_without_executing_tool() -> None:
    manager = AbilityManager.__new__(AbilityManager)
    manager._execute_single_tool_call = AsyncMock()
    tool_call = ToolCall(
        id="denied-call",
        type="function",
        name="browser_click",
        arguments='{"target_id":"t_g1_2"}',
    )
    denied_result = {"ok": False, "status": "denied"}
    denied_message = ToolMessage(
        tool_call_id="denied-call",
        content='{"ok":false,"status":"denied"}',
    )
    ctx = AgentCallbackContext(
        agent=MagicMock(),
        inputs=ToolCallInputs(
            tool_call=tool_call,
            tool_name=tool_call.name,
            tool_args={"target_id": "t_g1_2"},
            tool_result=denied_result,
            tool_msg=denied_message,
        ),
        extra={"_skip_tool_calls": {"denied-call": True}},
    )

    raw_execute = AbilityManager._railed_execute_single_tool_call.__wrapped__
    result = _run(raw_execute(manager, ctx, tool_call, _FakeSession()))

    assert result == (denied_result, denied_message)
    manager._execute_single_tool_call.assert_not_awaited()


def test_worker_cannot_claim_completion_without_runtime_field_evidence() -> None:
    session = _FakeSession()
    state = BrowserRuntimeRail._build_phase_state("Extract product title and price")
    state["phases"]["navigation"]["status"] = "completed"
    state["field_coverage"] = ["title"]
    state["structured_evidence"] = [{"kind": "structured_extraction", "fields": ["title"]}]
    session.update_state({"__browser_phase_budget_state__": state})

    BrowserRuntimeRail._apply_worker_progress_to_task_state(
        session,
        {"status": "completed", "missing_requirements": []},
        "Title only",
    )

    updated = session.get_state("__browser_phase_budget_state__")
    assert updated["status"] == "partial"
    assert updated["blockers"] == [
        "missing_required_field:price",
        "missing_required_field:evidence_slot:task_result:default:title",
    ]
    assert updated["worker_reported_status"] == "completed"


@pytest.mark.parametrize(
    ("tool_name", "tool_args"),
    [
        ("browser_snapshot", {}),
        ("browser_probe_interactives", {"query": "next"}),
        ("mcp_playwright-official_browser_find", {"text": "next"}),
    ],
)
def test_target_discovery_gets_one_bounded_replan_recovery(tool_name, tool_args) -> None:
    session = _FakeSession()
    state = BrowserRuntimeRail._build_phase_state("find the next control")
    state.update(
        {
            "status": "replan_required",
            "replan_required": True,
            "blocked_strategy": "target_discovery",
            "failed_strategies": ["target_discovery"],
        }
    )
    session.update_state({"__browser_phase_budget_state__": state})

    action_class = BrowserRuntimeRail._consume_phase_budget(session, tool_name, tool_args)
    assert action_class == "target_discovery"

    with pytest.raises(ValueError, match="already ran without verified semantic progress"):
        BrowserRuntimeRail._consume_phase_budget(session, tool_name, tool_args)

    assert session.get_state("__browser_phase_budget_state__")["replan_count"] == 1


def test_evaluate_result_becomes_compact_evidence_before_action_windowing() -> None:
    session = _FakeSession()
    state = BrowserRuntimeRail._build_phase_state("extract the title")
    session.update_state({"__browser_phase_budget_state__": state})
    function = "() => ({title: document.title, count: 3})"

    delta = BrowserRuntimeRail._record_phase_result(
        session,
        "browser_evaluate",
        {"function": function, "target": "document"},
        {"ok": True, "result": {"title": "Example", "count": 3}, "generation_id": "g4"},
    )
    BrowserRuntimeRail._record_recent_action(
        session,
        tool_name="browser_evaluate",
        tool_args={"function": function, "target": "document"},
        tool_result={"ok": True},
        action_class="script_exploration",
        elapsed_ms=23,
        progress_delta=delta,
    )

    task_state = session.get_state("__browser_phase_budget_state__")
    evidence = task_state["structured_evidence"][0]
    assert evidence["kind"] == "targeted_evaluate"
    assert evidence["values"] == {"title": "Example", "count": "3"}
    assert evidence["generation_id"] == "g4"
    action = task_state["recent_actions"][0]
    assert action["semantic_delta"] == "evidence_added"
    assert function not in action["target_summary"]
    assert "expression_sha256" in action["target_summary"]


def test_generation_or_selector_changes_do_not_create_new_semantic_evidence() -> None:
    session = _FakeSession()
    session.update_state({"__browser_phase_budget_state__": BrowserRuntimeRail._build_phase_state("extract title")})
    first = BrowserRuntimeRail._record_phase_result(
        session,
        "browser_evaluate",
        {"function": "() => document.title", "target": "ref=e1"},
        {"ok": True, "result": {"title": "Example"}, "generation_id": "g1"},
    )
    second = BrowserRuntimeRail._record_phase_result(
        session,
        "browser_evaluate",
        {"function": "() => document.title", "target": "ref=e9"},
        {"ok": True, "result": {"title": "Example"}, "generation_id": "g2"},
    )

    task_state = session.get_state("__browser_phase_budget_state__")
    assert first["evidence_added"] is True
    assert second["evidence_added"] is False
    assert len(task_state["structured_evidence"]) == 1


def test_recent_action_window_is_runtime_owned_and_bounded_to_six() -> None:
    session = _FakeSession()
    session.update_state({"__browser_phase_budget_state__": BrowserRuntimeRail._build_phase_state("inspect results")})
    for index in range(8):
        BrowserRuntimeRail._record_recent_action(
            session,
            tool_name="browser_click",
            tool_args={"target_id": f"e{index}"},
            tool_result={"ok": True},
            action_class="interaction",
            elapsed_ms=index,
            progress_delta={"phase": "navigation", "success": True},
        )

    actions = session.get_state("__browser_phase_budget_state__")["recent_actions"]
    assert [action["seq"] for action in actions] == [3, 4, 5, 6, 7, 8]


def test_same_price_interval_cannot_be_visited_twice() -> None:
    session = _FakeSession()
    state = BrowserRuntimeRail._build_phase_state("Filter products from 100 to 200 yuan")
    state["current_phase"] = "filtering"
    state["phases"]["filtering"]["visited_price_intervals"] = ["100:200"]
    session.update_state({"__browser_phase_budget_state__": state})

    with pytest.raises(ValueError, match="already visited"):
        BrowserRuntimeRail._consume_phase_budget(
            session,
            "browser_batch_interact",
            {
                "steps": [
                    {"op": "fill", "name": "minimum price", "value": "100"},
                    {"op": "fill", "name": "maximum price", "value": "200"},
                ]
            },
        )


def test_structured_extraction_records_task_scoped_evidence() -> None:
    session = _FakeSession()
    state = BrowserRuntimeRail._build_phase_state("Extract product title and price")
    state["current_phase"] = "extraction"
    session.update_state({"__browser_phase_budget_state__": state})

    BrowserRuntimeRail._record_phase_result(
        session,
        "browser_batch_interact",
        {"steps": [{"op": "extract_text", "field": "title"}]},
        {
            "ok": True,
            "generation_id": "g5",
            "extracted": {"title": "Headphones", "price": "$99"},
            "field_provenance": {
                "title": {
                    "selector": "h2.product-title",
                    "raw_text": "Headphones",
                    "generation_id": "g5",
                }
            },
        },
    )

    updated = session.get_state("__browser_phase_budget_state__")
    assert updated["field_coverage"] == ["price", "title"]
    assert updated["structured_evidence"][0]["kind"] == "structured_extraction"
    assert updated["structured_evidence"][0]["generation_id"] == "g5"
    assert updated["structured_evidence"][0]["provenance"]["title"]["selector"] == "h2.product-title"

    prompt_evidence = BrowserWorkingContextStore.compact_evidence(updated["structured_evidence"])
    assert prompt_evidence[0]["values"] == {"title": "Headphones", "price": "$99"}
    assert prompt_evidence[0]["provenance"]["title"]["selector"] == "h2.product-title"


def test_extraction_phase_waits_for_all_inferred_required_fields() -> None:
    session = _FakeSession()
    state = BrowserRuntimeRail._build_phase_state("Extract product title and price")
    state["current_phase"] = "extraction"
    state["phases"]["navigation"]["status"] = "completed"
    session.update_state({"__browser_phase_budget_state__": state})

    BrowserRuntimeRail._record_phase_result(
        session,
        "browser_batch_interact",
        {"steps": [{"op": "extract_text", "field": "title"}]},
        {"ok": True, "extracted": {"title": "Headphones"}},
    )
    partial = session.get_state("__browser_phase_budget_state__")
    assert partial["phases"]["extraction"]["status"] == "in_progress"
    assert partial["phases"]["extraction"]["missing_fields"] == ["price"]

    BrowserRuntimeRail._record_phase_result(
        session,
        "browser_batch_interact",
        {"steps": [{"op": "extract_text", "field": "price"}]},
        {"ok": True, "extracted": {"price": "$99"}},
    )
    completed = session.get_state("__browser_phase_budget_state__")
    assert completed["phases"]["extraction"]["status"] == "completed"
    assert completed["status"] == "completed"


def test_known_url_gate_allows_extraction_when_browser_is_already_on_target() -> None:
    session = _FakeSession()
    state = BrowserRuntimeRail._build_phase_state("Open https://example.test/article?id=7 and extract the title")
    session.update_state({"__browser_phase_budget_state__": state})

    action_class = BrowserRuntimeRail._consume_phase_budget(
        session,
        "browser_probe_cards",
        {},
        current_page_state={"url": "https://example.test/article?id=7", "title": "Article"},
    )

    assert action_class == "structured_extraction"
    assert session.get_state("__browser_phase_budget_state__")["phases"]["extraction"]["attempts"] == 1


def test_known_url_gate_guides_once_then_leaves_recovery_to_model() -> None:
    session = _FakeSession()
    state = BrowserRuntimeRail._build_phase_state("Open https://example.test and extract the title")
    session.update_state({"__browser_phase_budget_state__": state})

    with pytest.raises(ValueError, match="https://example.test"):
        BrowserRuntimeRail._consume_phase_budget(
            session,
            "browser_probe_cards",
            {},
            current_page_state={"url": "about:blank"},
        )

    action_class = BrowserRuntimeRail._consume_phase_budget(
        session,
        "browser_probe_cards",
        {},
        current_page_state={"url": "about:blank"},
    )
    updated = session.get_state("__browser_phase_budget_state__")
    assert action_class == "structured_extraction"
    assert updated["direct_navigation_guidance_count"] == 1
    assert updated["phases"]["extraction"]["attempts"] == 1


def test_known_root_url_gate_accepts_deeper_same_site_page() -> None:
    session = _FakeSession()
    state = BrowserRuntimeRail._build_phase_state("Open https://example.test and return the title")
    session.update_state({"__browser_phase_budget_state__": state})

    action_class = BrowserRuntimeRail._consume_phase_budget(
        session,
        "browser_probe_cards",
        {},
        current_page_state={"url": "https://example.test/search?q=title"},
    )

    assert action_class == "structured_extraction"


def test_replan_fingerprint_allows_a_different_requested_field() -> None:
    session = _FakeSession()
    state = BrowserRuntimeRail._build_phase_state("Extract the title and price")
    state["last_page"] = {"url": "https://example.test/item/7"}
    title_args = {
        "function": "() => ({title: document.title})",
        "target": "ref=e1",
        "field": "title",
    }
    title_strategy = BrowserRuntimeRail._strategy_fingerprint(
        state,
        "browser_evaluate",
        title_args,
        "script_exploration",
    )
    state.update(
        {
            "status": "replan_required",
            "replan_required": True,
            "blocked_strategy": title_strategy,
            "failed_strategies": [title_strategy],
        }
    )
    session.update_state({"__browser_phase_budget_state__": state})

    action_class = BrowserRuntimeRail._consume_phase_budget(
        session,
        "browser_evaluate",
        {
            "function": "() => ({price: document.querySelector('.price')?.textContent})",
            "target": "ref=e9",
            "field": "price",
        },
    )

    assert action_class == "script_exploration"
    updated = session.get_state("__browser_phase_budget_state__")
    assert updated["status"] == "replan_trial"
    assert updated["replan_count"] == 1


def test_strategy_fingerprint_tracks_semantic_target_without_generation_noise() -> None:
    state = BrowserRuntimeRail._build_phase_state("Compare the titles")
    first_page_state = {
        "url": "https://example.test/results",
        "interactives": [
            {"target_id": "t_g2_1", "role": "link", "text": "First result"},
            {"target_id": "t_g2_2", "role": "link", "text": "Second result"},
        ],
    }
    refreshed_page_state = {
        "url": "https://example.test/results",
        "interactives": [
            {"target_id": "t_g3_9", "role": "link", "text": "First result"},
        ],
    }

    first = BrowserRuntimeRail._strategy_fingerprint(
        state,
        "browser_batch_interact",
        {"steps": [{"op": "extract_text", "target_id": "t_g2_1", "field": "title"}]},
        "structured_extraction",
        first_page_state,
    )
    second = BrowserRuntimeRail._strategy_fingerprint(
        state,
        "browser_batch_interact",
        {"steps": [{"op": "extract_text", "target_id": "t_g2_2", "field": "title"}]},
        "structured_extraction",
        first_page_state,
    )
    refreshed_first = BrowserRuntimeRail._strategy_fingerprint(
        state,
        "browser_batch_interact",
        {"steps": [{"op": "extract_text", "target_id": "t_g3_9", "field": "title"}]},
        "structured_extraction",
        refreshed_page_state,
    )

    assert first != second
    assert first == refreshed_first


def test_repeated_replan_denials_become_terminal_after_finite_budget() -> None:
    session = _FakeSession()
    state = BrowserRuntimeRail._build_phase_state("Extract the title")
    args = {"function": "() => document.querySelector('button').click()", "target": "ref=e1"}
    strategy = BrowserRuntimeRail._strategy_fingerprint(
        state,
        "browser_evaluate",
        args,
        "script_exploration",
    )
    state.update(
        {
            "status": "replan_required",
            "replan_required": True,
            "blocked_strategy": strategy,
            "failed_strategies": [strategy],
        }
    )
    session.update_state({"__browser_phase_budget_state__": state})

    for _ in range(3):
        with pytest.raises(ValueError, match="Semantic loop detected"):
            BrowserRuntimeRail._consume_phase_budget(session, "browser_evaluate", args)

    updated = session.get_state("__browser_phase_budget_state__")
    assert updated["status"] == "blocked"
    assert updated["replan_denial_count"] == 3
    assert "semantic_replan_denial_budget_exhausted" in updated["blockers"]


def test_replan_allows_one_bounded_read_only_evidence_recovery() -> None:
    session = _FakeSession()
    state = BrowserRuntimeRail._build_phase_state("Extract the title")
    args = {"function": "() => ({title: document.title})", "field": "title"}
    strategy = BrowserRuntimeRail._strategy_fingerprint(
        state,
        "browser_evaluate",
        args,
        "script_exploration",
    )
    state.update(
        {
            "status": "replan_required",
            "replan_required": True,
            "blocked_strategy": strategy,
            "failed_strategies": [strategy],
        }
    )
    session.update_state({"__browser_phase_budget_state__": state})

    action_class = BrowserRuntimeRail._consume_phase_budget(session, "browser_evaluate", args)

    updated = session.get_state("__browser_phase_budget_state__")
    assert action_class == "script_exploration"
    assert updated["status"] == "replan_trial"
    assert updated["read_only_recovery_counts"][strategy] == 1


def test_offload_recall_does_not_consume_browser_phase_or_replan_budget() -> None:
    session = _FakeSession()
    state = BrowserRuntimeRail._build_phase_state("Extract the title")
    state.update(
        {
            "status": "replan_required",
            "replan_required": True,
            "replan_count": 2,
        }
    )
    session.update_state({"__browser_phase_budget_state__": state})

    action_class = BrowserRuntimeRail._consume_phase_budget(
        session,
        "browser_recall_offload",
        {"handle": "audit-1", "query": "title"},
    )

    updated = session.get_state("__browser_phase_budget_state__")
    assert action_class == "other"
    assert all(details["attempts"] == 0 for details in updated["phases"].values())
    assert updated["replan_count"] == 2


def test_new_structured_evidence_recovers_pending_replan_trial() -> None:
    session = _FakeSession()
    state = BrowserRuntimeRail._build_phase_state("Extract the title")
    state.update(
        {
            "status": "replan_trial",
            "replan_required": True,
            "replan_trial_pending": True,
            "trial_strategy": "title-extraction",
        }
    )
    session.update_state({"__browser_phase_budget_state__": state})

    result = BrowserRuntimeRail._record_phase_result(
        session,
        "browser_batch_interact",
        {"steps": [{"op": "extract_text", "field": "title"}]},
        {"ok": True, "extracted": {"title": "Verified title"}},
    )

    updated = session.get_state("__browser_phase_budget_state__")
    assert result["recovered"] is True
    assert updated["status"] == "in_progress"
    assert updated["replan_required"] is False
    assert updated["replan_trial_pending"] is False


def test_price_interval_ignores_evaluate_script_numbers() -> None:
    assert BrowserRuntimeRail._price_interval_signature(
        "browser_evaluate",
        {"function": "() => [...document.querySelectorAll('.price')].slice(1, 2)"},
    ) == ""
    assert BrowserRuntimeRail._price_interval_signature(
        "browser_batch_interact",
        {
            "steps": [
                {"op": "fill", "field": "minimum price", "value": "100"},
                {"op": "fill", "field": "maximum price", "value": "200"},
            ]
        },
    ) == "100:200"


def test_required_field_ontology_and_generic_evidence_slots_keep_provenance() -> None:
    state = BrowserRuntimeRail._build_phase_state(
        "提取作者、点赞数、收藏数、评论数、店铺、时长、最高温、最低温和排序状态"
    )
    expected_fields = {
        "author",
        "likes",
        "favorites",
        "comments",
        "shop",
        "duration",
        "high_temperature",
        "low_temperature",
        "sort_state",
    }
    assert set(state["required_fields"]) == expected_fields
    assert {slot["field"] for slot in state["required_evidence_slots"]} == expected_fields

    BrowserRuntimeRail._record_structured_evidence(
        state,
        {
            "generation_id": "g8",
            "extracted": {"作者": "Alice", "点赞数": "123"},
            "field_provenance": {
                "作者": {
                    "selector": ".author",
                    "raw_text": "作者 Alice",
                    "generation_id": "g8",
                    "source": "browser_batch_interact",
                },
                "点赞数": {
                    "selector": ".likes",
                    "raw_text": "点赞 123",
                    "generation_id": "g8",
                    "source": "browser_batch_interact",
                },
            },
        },
        tool_name="browser_batch_interact",
        tool_args={},
    )

    slots = {slot["field"]: slot for slot in state["evidence_slots"]}
    assert slots["author"]["selector"] == ".author"
    assert slots["author"]["raw_text"] == "作者 Alice"
    assert slots["author"]["generation"] == "g8"
    assert slots["likes"]["source"] == "browser_batch_interact"
    assert BrowserRuntimeRail._infer_required_fields("提取商品评分和店铺评分") == [
        "product_rating",
        "shop_rating",
    ]


def test_required_fields_use_requested_output_clause_not_operation_preconditions() -> None:
    task = "打开淘宝搜索机械键盘，可以按价格或销量排序，返回第一条商品标题"
    assert BrowserRuntimeRail._infer_required_fields(task) == ["title"]
    assert BrowserRuntimeRail._infer_required_fields(
        "打开百度查看天气，返回最高温和最低温"
    ) == ["high_temperature", "low_temperature"]
    assert BrowserRuntimeRail._infer_required_fields("查询美元兑人民币并返回汇率") == ["exchange_rate"]
    assert BrowserRuntimeRail._infer_required_fields("搜索 Python 视频，返回播放量") == ["views"]


def test_comprehensive_and_latest_create_distinct_title_slots_without_compare_word() -> None:
    slots = BrowserRuntimeRail._infer_required_evidence_slots(
        "在B站分别切换综合排序和最新发布，返回两个标题"
    )
    assert slots == [
        {"entity": "bilibili_search_result", "variant": "comprehensive", "field": "title"},
        {"entity": "bilibili_search_result", "variant": "latest", "field": "title"},
    ]


def test_terminal_state_allows_one_tool_disabled_synthesis_then_force_finishes() -> None:
    runtime = MagicMock(spec=BrowserAgentRuntime)
    runtime.semantic_progress = {}
    runtime.service = MagicMock()
    state = BrowserRuntimeRail._build_phase_state("Extract the title")
    state.update({"status": "partial", "blockers": ["missing_required_field:title"]})
    session = _FakeSession()
    session.update_state({"__browser_phase_budget_state__": state})
    agent = MagicMock()
    agent.system_prompt_builder = SystemPromptBuilder(language="en")
    first_ctx = AgentCallbackContext(
        agent=agent,
        session=session,
        inputs=ModelCallInputs(tools=[MagicMock()]),
    )
    rail = BrowserRuntimeRail(runtime)

    _run(rail.before_model_call(first_ctx))

    assert first_ctx.inputs.tools == []
    assert first_ctx.consume_force_finish() is None
    assert "Do not call tools" in agent.system_prompt_builder.build()

    second_ctx = AgentCallbackContext(agent=agent, session=session, inputs=ModelCallInputs(tools=[]))
    _run(rail.before_model_call(second_ctx))
    finish = second_ctx.consume_force_finish()
    assert finish is not None
    assert finish.result["authoritative_browser_result"]["status"] == "partial"


def test_unfinished_text_tool_intent_retries_once_in_same_task() -> None:
    runtime = MagicMock(spec=BrowserAgentRuntime)
    state = BrowserRuntimeRail._build_phase_state("Extract the title")
    session = _FakeSession()
    session.update_state({"__browser_phase_budget_state__": state})
    response = SimpleNamespace(
        content="让我继续调用 browser_evaluate 获取标题",
        tool_calls=[],
    )
    ctx = AgentCallbackContext(
        agent=MagicMock(),
        session=session,
        inputs=ModelCallInputs(response=response),
    )
    ctx.extra["__browser_task_deadline__"] = runtime_module.time.monotonic() + 60
    rail = BrowserRuntimeRail(runtime)

    _run(rail.after_model_call(ctx))

    assert ctx.has_pending_steering() is True
    assert session.get_state("__browser_phase_budget_state__")["model_protocol_retry_count"] == 1
    assert ctx.consume_force_finish() is None

    _run(rail.after_model_call(ctx))
    finish = ctx.consume_force_finish()
    assert finish is not None
    assert finish.result["authoritative_browser_result"]["terminal_reason"] == "model_tool_protocol_error"


def test_structured_resume_preserves_original_slots_and_evidence() -> None:
    runtime = MagicMock(spec=BrowserAgentRuntime)
    runtime.service = MagicMock()
    rail = BrowserRuntimeRail(runtime)
    state = BrowserRuntimeRail._build_phase_state("返回商品标题和商品评分")
    state.update(
        {
            "status": "partial",
            "terminal_reason": "runtime_completion_requirements_missing",
            "field_coverage": ["title"],
            "evidence_slots": [
                {
                    "entity": "product",
                    "variant": "default",
                    "field": "title",
                    "value": "Keyboard",
                    "source": "browser_probe_cards",
                    "generation": "g2",
                }
            ],
        }
    )
    original_required_slots = list(state["required_evidence_slots"])
    session = _FakeSession()
    session.update_state({"__browser_phase_budget_state__": state})

    resumed = rail._ensure_task_state(
        session,
        "只补充缺失的商品评分，不要重新搜索",
        resume=True,
    )

    assert resumed["status"] == "in_progress"
    assert resumed["resume_count"] == 1
    assert resumed["required_evidence_slots"] == original_required_slots
    assert resumed["evidence_slots"][0]["value"] == "Keyboard"
    assert resumed["resume_instruction"] == "只补充缺失的商品评分，不要重新搜索"
    runtime.reset_semantic_task.assert_called_once()


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


def test_before_model_call_initializes_runtime_task_state_without_progress_attachment() -> None:
    runtime = MagicMock(spec=BrowserAgentRuntime)
    runtime.service = MagicMock()
    runtime.service._progress_by_session = {}
    builder = SystemPromptBuilder(language="en")
    session = _FakeSession()
    agent = MagicMock()
    agent.system_prompt_builder = builder
    ctx = AgentCallbackContext(
        agent=agent,
        session=session,
        inputs=ModelCallInputs(messages=[UserMessage(content="continue checkout")]),
    )
    rail = BrowserRuntimeRail(runtime)

    _run(rail.before_model_call(ctx))

    prompt = builder.build()
    assert "<browser_progress>{...}</browser_progress>" in prompt
    task_state = session.get_state("__browser_phase_budget_state__")
    assert task_state["goal"] == "continue checkout"
    assert task_state["status"] == "in_progress"
    runtime.reset_semantic_task.assert_called_once()


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
    session.update_state({"__browser_phase_budget_state__": BrowserRuntimeRail._build_phase_state("open example.com")})
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

    runtime.service.record_tool_progress.assert_not_called()
    task_state = session.get_state("__browser_phase_budget_state__")
    assert len(task_state["recent_actions"]) == 1
    assert task_state["recent_actions"][0]["action_class"] == "navigation"
    assert session.get_state("__browser_subagent_progress_state__")["recent_tool_steps"]


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
    assert result["progress_state"]["status"] == "partial"


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


def test_after_invoke_cannot_override_runtime_terminal_state_without_progress_block() -> None:
    runtime = MagicMock(spec=BrowserAgentRuntime)
    runtime.service.should_treat_as_completed.return_value = False
    runtime.service.build_failure_summary.return_value = "runtime blocked: captcha"
    state = BrowserRuntimeRail._build_phase_state("submit form")
    state.update(
        {
            "status": "blocked",
            "blockers": ["captcha"],
            "terminal_reason": "runtime_blocked",
        }
    )
    session = _FakeSession()
    session.update_state(
        {
            "__browser_phase_budget_state__": state,
            "__browser_subagent_last_task__": "submit form",
        }
    )
    result = {"output": "Done", "result_type": "answer"}
    ctx = AgentCallbackContext(
        agent=MagicMock(),
        session=session,
        inputs=InvokeInputs(query="submit form", result=result),
    )

    _run(BrowserRuntimeRail(runtime).after_invoke(ctx))

    assert result["result_type"] == "error"
    assert result["failure_summary"] == "runtime blocked: captcha"
    assert session.get_state("__browser_phase_budget_state__")["blockers"] == ["captcha"]
