# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Sanitized production regressions and guarded segmented handover contracts."""

import json
import time
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from openjiuwen.core.foundation.llm.schema.message import AssistantMessage, ToolMessage
from openjiuwen.core.foundation.llm.schema.tool_call import ToolCall
from openjiuwen.core.runner import Runner
from openjiuwen.core.single_agent.ability_manager import AbilityManager
from openjiuwen.harness.tools.browser_move.decision.action_space import (
    build_menu,
    goal_values,
)
from openjiuwen.harness.tools.browser_move.decision.guard import PAGE_STATE_JS
from openjiuwen.harness.tools.browser_move.decision.intent import normalize_goal
from openjiuwen.harness.tools.browser_move.decision.jev_client import (
    DecisionUnavailable,
    validate_choice,
)
from openjiuwen.harness.tools.browser_move.decision.policy_model import (
    BrowserPolicyModel,
)
from openjiuwen.harness.tools.browser_move.playwright_runtime.browser_state_context_processor import (
    BrowserStateContextProcessor,
)
from openjiuwen.harness.tools.browser_move.playwright_runtime.page_state import (
    BrowserPageState,
)
from openjiuwen.harness.tools.browser_move.playwright_runtime.policy_page_action import (
    BrowserPageActionTool,
)
from openjiuwen.harness.tools.browser_move.playwright_runtime.probes import (
    build_interactive_probe_js,
)
from openjiuwen.harness.tools.browser_move.playwright_runtime.runtime import (
    BrowserAgentRuntime,
    BrowserRuntimeRail,
)
from tests.unit_tests.harness.tools.browser_move.test_browser_jev_policy import (
    TOOLS,
    choose_operation,
    answer,
    messages_for,
    setup_policy,
)
from tests.unit_tests.harness.tools.browser_move.test_browser_runtime_rail import (
    _FakeSession,
)
from tests.unit_tests.harness.tools.browser_move.test_browser_runtime_tools import (
    _make_runtime,
)
from tests.unit_tests.harness.tools.browser_move.test_browser_september17_contracts import (
    dom_page as shared_dom_page,
)

dom_page = shared_dom_page
PHASE = "__browser_phase_budget_state__"


@pytest.mark.asyncio
async def test_tools_becoming_available_can_unlock_a_local_gate_without_wasting_a_request():
    policy, llm, client, runtime, context, captured = setup_policy()
    await policy.invoke(await messages_for(policy, context, captured), tools=[])
    client.evaluate.assert_not_awaited()
    result = await policy.invoke(await messages_for(policy, context, captured), tools=TOOLS)
    assert result.tool_calls
    client.evaluate.assert_awaited_once()


@pytest.mark.asyncio
async def test_long_task_url_is_never_compiled_from_a_truncated_state():
    policy, llm, client, runtime, context, captured = setup_policy(goal="打开 https://example.test/" + "a" * 4100)
    runtime._ensure_page_state().decision_snapshot["page_guard"] = {"document": "d"}
    result = await policy.invoke(await messages_for(policy, context, captured),
                                 tools=[{"name": "browser_page_action"}])
    assert result.metadata["browser_policy"]["reason"] == "task_intent_truncated"
    client.evaluate.assert_not_awaited()


def test_oversized_literals_are_skipped_without_extracting_partial_values():
    assert goal_values("search for research and development") == ["research and development"]
    assert goal_values('搜索 "' + 'a' * 210 + '" 然后输入 "valid"') == ["valid"]
    assert goal_values("搜索 " + "x" * 200 + " rest of query") == []


@pytest.mark.asyncio
async def test_malformed_host_goal_cannot_produce_an_unguided_jev_action():
    policy, llm, client, runtime, context, captured = setup_policy(goal='你收到一条消息：\n{"source":"web","content":')
    result = await policy.invoke(await messages_for(policy, context, captured), tools=TOOLS)
    assert result.metadata["browser_policy"]["reason"] == "missing_task_intent"
    client.evaluate.assert_not_awaited()


def wrapped(text):
    return "你收到一条消息：\n" + json.dumps(
        {
            "source": "web",
            "timestamp": "2026-09-23 12:00:00",
            "preferred_response_language": "zh",
            "content": text,
            "type": "user input",
            "files_updated_by_user": "{}",
            "origin_kind": "external_user_authored",
        },
        ensure_ascii=False,
    )


def test_host_envelope_and_balanced_literals_do_not_become_fill_candidates():
    text = "打开百度首页，搜索“人民币 新加坡元汇率”，返回结果页显示的数字"
    assert normalize_goal(wrapped(text)) == text
    assert goal_values(wrapped(text)) == ["人民币 新加坡元汇率"]
    assert goal_values("搜索「机械键盘」，然后点击搜索") == ["机械键盘"]
    assert goal_values("搜索“机械键盘") == []
    assert goal_values(wrapped(text)[:-12]) == []
    assert goal_values("search for keyboard, and return prices") == ["keyboard"]


@pytest.mark.asyncio
@pytest.mark.parametrize("reason", ["handoff", "uncertain", "jev_timeout", "jev_http_429", "jev_http_503"])
async def test_soft_fallback_reenters_only_after_meaningful_change_and_survives_recreation(reason):
    policy, llm, client, runtime, context, captured = setup_policy(goal=wrapped("点击“销量”排序"))
    phase = context.get_session_ref().get_state(PHASE)
    phase["query_id"] = "shared-query"
    if reason == "handoff":
        client.evaluate.return_value = answer("HANDOFF")
    elif reason == "uncertain":
        client.evaluate.return_value = answer(confidence=0.3)
    else:
        client.evaluate.side_effect = DecisionUnavailable(reason)
    first = await policy.invoke(await messages_for(policy, context, captured), tools=TOOLS)
    assert first.metadata["browser_policy"]["fallback_scope"] == "segment"
    assert client.evaluate.call_args.args[0]["state"]["current_intent"] == "点击“销量”排序"
    deadline = phase["deadline_at"]
    assert policy.should_observe(context)
    # A focused continuation can recreate the model and session wrapper, but not its budget/history.
    session = SimpleNamespace(get_session_id=lambda: "recreated-child", get_state=lambda _: phase)
    context = SimpleNamespace(get_session_ref=lambda: session)
    policy = BrowserPolicyModel(llm, policy.decision_config, runtime, client=client)
    page = runtime._ensure_page_state()
    page.decision_snapshot.update(capture_id="fresh-clock-only", observed_at_ms=99999)
    result = await policy.invoke(await messages_for(policy, context, captured), tools=TOOLS)
    assert result.metadata["browser_policy"]["cached_fallback"]
    assert client.evaluate.await_count == 1
    # A newly enabled control/name is real executable progress; a timestamp alone was not.
    target = page.get_target(page.export_decision_targets()[0]["target_id"])
    target.name = "价格"
    client.evaluate.side_effect = None
    client.evaluate.return_value = answer()
    result = await policy.invoke(await messages_for(policy, context, captured), tools=TOOLS)
    assert result.tool_calls
    assert client.evaluate.await_count == 2
    assert phase["decision_policy"]["decisions"] == 2
    assert phase["decision_policy"]["counters"]["reentries"] == 1
    assert phase["deadline_at"] == deadline


@pytest.mark.asyncio
@pytest.mark.parametrize("reason", ["jev_http_401", "jev_http_402", "jev_http_403", "invalid_jev_response"])
async def test_any_failure_hands_back_one_step_and_reenters_on_new_intent(reason):
    # One bad Jev answer or provider error must not switch Jev off for the rest of the task.
    policy, llm, client, runtime, context, captured = setup_policy()
    client.evaluate.side_effect = DecisionUnavailable(reason)
    await policy.invoke(await messages_for(policy, context, captured), tools=TOOLS)
    phase = context.get_session_ref().get_state(PHASE)
    phase["task"] = "现在翻到下一页"
    policy = BrowserPolicyModel(llm, policy.decision_config, runtime, client=client)
    result = await policy.invoke(await messages_for(policy, context, captured), tools=TOOLS)
    assert result.content == "original LLM answer"
    assert policy.should_observe(context)
    assert client.evaluate.await_count == 2


@pytest.mark.asyncio
async def test_shared_budget_and_old_state_are_not_reset_by_new_intent():
    policy, llm, client, runtime, context, captured = setup_policy()
    policy.decision_config = replace(policy.decision_config, max_decisions=1)
    client.evaluate.return_value = answer("HANDOFF")
    await policy.invoke(await messages_for(policy, context, captured), tools=TOOLS)
    context.get_session_ref().get_state(PHASE)["task"] = "改为下一页"
    policy = BrowserPolicyModel(llm, policy.decision_config, runtime, client=client)
    result = await policy.invoke(await messages_for(policy, context, captured), tools=TOOLS)
    assert result.metadata["browser_policy"]["reason"] == "decision_budget_exhausted"
    assert client.evaluate.await_count == 1


@pytest.mark.asyncio
async def test_rejected_response_and_cached_route_are_distinct_and_sanitized(monkeypatch):
    records = []
    monkeypatch.setattr(
        "openjiuwen.harness.tools.browser_move.decision.policy_model.browser_agent_log_info",
        lambda marker, data: records.append((marker, json.loads(data))),
    )
    policy, llm, client, runtime, context, captured = setup_policy(goal="查询“private-user-value”")
    client.evaluate.return_value = answer(confidence=0.2)
    await policy.invoke(await messages_for(policy, context, captured), tools=TOOLS)
    await policy.invoke(await messages_for(policy, context, captured), tools=TOOLS)
    response = next(data for marker, data in records if "POLICY_RESPONSE" in marker)
    routes = [data for marker, data in records if marker == "[BROWSER_POLICY] %s"]
    assert response["confidence"] == 0.2 and response["top1"] == 0.98
    assert response["probability_margin"] == pytest.approx(0.97)
    assert response["operation"] == "click"
    assert routes[0]["evaluated"] is True and routes[1]["evaluated"] is False
    assert routes[1]["cached_fallback"] is True
    assert "private-user-value" not in json.dumps(records)


def test_probability_rounding_boundary_is_inclusive_but_bad_distributions_still_fail():
    criteria = {"a": "A", "b": "B", "c": "C"}
    response = {"type": "choice", "choice": "a", "confidence": 0.8, "probabilities": {"a": 0.8, "b": 0.1, "c": 0.09}}
    assert validate_choice(response, criteria, 0.65) == "a"
    response["probabilities"]["c"] = 0.08
    with pytest.raises(DecisionUnavailable, match="distribution"):
        validate_choice(response, criteria, 0.65)


@pytest.mark.asyncio
async def test_execution_receipts_postconditions_and_no_automatic_replay():
    policy, llm, client, runtime, context, captured = setup_policy()
    result = await policy.invoke(await messages_for(policy, context, captured), tools=TOOLS)
    call = result.tool_calls[0]
    inputs = SimpleNamespace(tool_call=call, tool_name=call.name, tool_args=call.arguments)
    policy.record_execution(inputs, context.get_session_ref(), {"success": True, "executed": True})
    policy.record_execution(inputs, context.get_session_ref(), {"success": True, "executed": True})
    runtime._ensure_page_state().decision_snapshot["capture_id"] = "post-action-capture"
    result = await policy.invoke(await messages_for(policy, context, captured), tools=TOOLS)
    state = context.get_session_ref().get_state(PHASE)["decision_policy"]
    assert state["counters"]["executions_ok"] == 1
    assert state["receipts"][-1]["postcondition"] == "no_observable_progress"
    assert result.metadata["browser_policy"]["reason"] == "compiled_action"
    assert client.evaluate.await_count == 2
    second = result.tool_calls[0]
    policy.record_execution(SimpleNamespace(tool_call=second), context.get_session_ref(), {"success": True})
    runtime._ensure_page_state().decision_snapshot["capture_id"] = "second-noop"
    result = await policy.invoke(await messages_for(policy, context, captured), tools=TOOLS)
    assert result.metadata["browser_policy"]["reason"] == "no_supported_actions"
    assert client.evaluate.await_count == 2  # This button cannot consume further decisions.


def test_unsupported_controls_are_removed_before_candidate_budget():
    controls = [
        {
            "target_id": str(i),
            "name": "widget",
            "role": "combobox",
            "enabled": True,
            "actionable": True,
            "decision_state": {"tag": "div", "node_guard": {"node": i}},
        }
        for i in range(30)
    ]
    controls.append(
        {
            "target_id": "search",
            "name": "搜索",
            "role": "button",
            "enabled": True,
            "actionable": True,
            "clickable": True,
            "decision_state": {"tag": "button", "node_guard": {"node": 31}},
        }
    )
    menu = build_menu(controls, "搜索商品", limit=1)
    assert menu.steps["a1"]["target_id"] == "search"
    assert menu.excluded["unsupported_control"] == 30


@pytest.mark.asyncio
@pytest.mark.parametrize("successful_fill", [False, True])
async def test_llm_supplied_search_value_is_bound_to_its_observed_node_only(successful_fill):
    policy, llm, client, runtime, context, captured = setup_policy(goal="查汇率")
    page = runtime._ensure_page_state()
    target = page.get_target(page.export_decision_targets()[0]["target_id"])
    target.role, target.name = "combobox", "搜索"
    target.decision_state.update(tag="input", input_type="search", search_like=True, current_value="")
    call = ToolCall(
        id="llm-fill",
        type="function",
        name="browser_batch_interact",
        arguments=json.dumps(
            {
                "steps": [{"op": "fill", "target_id": target.target_id, "value": "人民币 SGD 汇率"}],
            }
        ),
    )
    policy.record_execution(
        SimpleNamespace(tool_call=call, tool_name=call.name, tool_args=call.arguments),
        context.get_session_ref(),
        {"success": successful_fill},
    )
    target.decision_state["current_value"] = "人民币 SGD 汇率"
    choose_operation(client, "PRESS_ENTER")
    result = await policy.invoke(await messages_for(policy, context, captured), tools=TOOLS)
    if successful_fill:
        assert json.loads(result.tool_calls[0].arguments)["steps"][0]["op"] == "press"
    else:
        assert result.content == "original LLM answer"
        client.evaluate.assert_not_awaited()


def test_search_binding_cannot_cross_dom_nodes_or_create_fill_values():
    control = {
        "target_id": "field",
        "role": "combobox",
        "name": "搜索",
        "enabled": True,
        "actionable": True,
        "decision_state": {
            "tag": "input",
            "search_like": True,
            "current_value": "derived query",
            "node_guard": {"document": "doc", "node": 2},
        },
    }
    binding = {"document": "doc", "node": 1, "value": "derived query"}
    assert not build_menu([control], "查汇率", limit=30, search_bindings=[binding]).steps


def test_file_inputs_and_duplicate_actions_cannot_consume_action_slots():
    item = {
        "target_id": "x",
        "role": "textbox",
        "name": "upload",
        "enabled": True,
        "actionable": True,
        "decision_state": {"tag": "input", "input_type": "file", "node_guard": {"node": 1}},
    }
    assert not build_menu([item], "输入“anything”", limit=30).steps
    item.update(role="button", clickable=True)
    item["decision_state"] = {"tag": "button", "node_guard": {"node": 1}}
    assert len(build_menu([item, item], "点击", limit=30).steps) == 1


def dom_probe(page, limit=30, intent=""):
    return page.evaluate(
        "async code => await eval('(' + code + ')')({evaluate: (fn, arg) => fn(arg)})",
        build_interactive_probe_js(max_items=limit, decision_mode=True, intent=intent),
    )


def test_real_dom_prefilters_disabled_and_readonly_and_ranks_goal_before_limit(dom_page):
    dom_page.set_content(
        "".join(f'<input aria-label="readonly{i}" readonly>' for i in range(32))
        + "<button>首页</button><button>销量排序</button>"
    )
    observed = dom_probe(dom_page, limit=1, intent="按销量排序")
    assert observed["elements"][0]["text"] == "销量排序"
    assert observed["decision_excluded"]["readonly"] > 0
    assert dom_page.evaluate("window.__openjiuwenDecisionNodes.next") == 1


def test_real_dom_search_fill_suggestion_and_enter_use_fresh_observations(dom_page):
    dom_page.set_content("""<form onsubmit="event.preventDefault(); document.querySelector('h1').textContent='结果';">
        <h1>搜索</h1><input aria-label="搜索" role="combobox" aria-autocomplete="list">
        <div role="option" tabindex="0" onclick="document.querySelector('input').value='机械键盘'">机械键盘</div>
        </form>""")
    state = BrowserPageState()
    state.register_interactives(dom_probe(dom_page))
    menu = build_menu(state.export_decision_targets(), "搜索“机械键盘”", limit=30)
    assert any(step["op"] == "fill" and step["value"] == "机械键盘" for step in menu.steps.values())
    assert any(step["op"] == "click" for step in menu.steps.values())
    dom_page.locator("input").fill("机械键盘")
    state.register_interactives(dom_probe(dom_page))
    menu = build_menu(state.export_decision_targets(), "搜索“机械键盘”", limit=30)
    assert not any(step["op"] == "fill" for step in menu.steps.values())
    assert any(step["op"] == "press" and step["key"] == "Enter" for step in menu.steps.values())
    dom_page.locator("input").press("Enter")
    assert dom_page.locator("h1").inner_text() == "结果"


def test_real_dom_page_guard_changes_on_scroll_or_document_replacement(dom_page):
    dom_page.set_content('<main style="height:4000px">Long content</main>')
    first = dom_probe(dom_page)["decision_snapshot"]["page_guard"]
    dom_page.evaluate("window.scrollBy(0,500)")
    assert dom_page.evaluate(PAGE_STATE_JS) != first
    dom_page.goto("about:blank?other")
    assert dom_page.evaluate(PAGE_STATE_JS)["document"] != first["document"]


def test_page_actions_require_observed_capabilities_and_explicit_urls():
    page = {
        "url": "https://old.test",
        "page_guard": {"history_length": 2, "can_go_back": True},
        "page_position": {"pixels_above": 20, "pixels_below": 100},
    }
    goal = "打开 https://example.test/path?q=1 然后返回"
    assert not build_menu([], goal, limit=30, page=page).steps
    menu = build_menu([], goal, limit=30, page=page, allow_page_actions=True)
    assert {step["op"] for step in menu.steps.values()} == {"navigate", "navigate_back", "scroll"}
    assert len(menu.steps) == 4
    assert not any(
        step["op"] == "navigate"
        for step in build_menu(
            [], "打开百度或 javascript:alert(1)", limit=30, page=page, allow_page_actions=True
        ).steps.values()
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("mutation", [None, "arguments", "node", "capability", "timeout"])
async def test_page_dispatch_uses_ability_manager_late_guard_and_never_retries(monkeypatch, mutation):
    policy, llm, client, runtime, context, captured = setup_policy(goal="打开 https://destination.test/")
    page = runtime._ensure_page_state()
    page.decision_snapshot["page_guard"] = {"document": "doc-a", "history_length": 1}
    choose_operation(client, "NAVIGATE")
    # Only the authorized page helper is supplied.
    result = await policy.invoke(await messages_for(policy, context, captured), tools=[{"name": "browser_page_action"}])
    call = result.tool_calls[0]
    inputs = SimpleNamespace(tool_name=call.name, tool_args=json.loads(call.arguments), tool_call=call)
    callback = SimpleNamespace(inputs=inputs)
    session = context.get_session_ref()
    runtime.decision_policy = policy
    runtime.service = SimpleNamespace(allowed_tool_names=("browser_navigate",))
    runtime.classify_tool_result = BrowserAgentRuntime.classify_tool_result
    runtime.record_tool_reference_state = MagicMock()
    runtime._call_playwright_tool = AsyncMock(return_value={"ok": True})
    policy.check_tool_call_binding(inputs, session)
    if mutation == "node":
        runtime._call_playwright_run_code_unsafe.return_value = {"result": {"ok": False}}
    elif mutation == "capability":
        runtime.service.allowed_tool_names = ()
    elif mutation == "arguments":
        inputs.tool_args["url"] = "https://changed.test/"
        call.arguments = json.dumps(inputs.tool_args)
    elif mutation == "timeout":
        runtime._call_playwright_tool.side_effect = TimeoutError()
    tool = BrowserPageActionTool(runtime)
    manager = AbilityManager(owner_id="jev-page-dispatch")
    manager.add(tool.card)
    monkeypatch.setattr(Runner.resource_mgr, "get_tool", lambda **kwargs: tool)
    output, _ = await manager._execute_single_tool_call(call, session, callback_context=callback)
    assert output.success is (mutation is None)
    assert runtime._call_playwright_tool.await_count == (1 if mutation in {None, "timeout"} else 0)
    if mutation == "timeout":
        assert output.data["executed"] is None and output.data["error"] == "page_action_uncertain"
        assert output.data["execution_state"] == "dispatched_unknown"


def test_old_failed_action_group_does_not_poison_later_successful_group():
    def group(call_id, ok):
        return [
            AssistantMessage(
                content="", tool_calls=[ToolCall(id=call_id, type="function", name="browser_click", arguments="{}")]
            ),
            ToolMessage(
                content=json.dumps({"ok": ok}),
                tool_call_id=call_id,
                metadata={"success": ok, "executed": True, "state_changed": True},
            ),
        ]

    messages = group("old-failure", False) + group("new-success", True)
    _, ids, _ = BrowserStateContextProcessor._completed_state_action_group(messages)
    assert ids == {"new-success"}
    assert not BrowserStateContextProcessor._requires_reconciliation(messages, ids)


def test_resuming_execution_error_clears_only_recoverable_blocker_and_preserves_policy_budget():
    state = BrowserRuntimeRail._build_phase_state("Read the page title")
    state.update(
        status="blocked",
        terminal_reason="browser_execution_error",
        blockers=["browser_execution_error"],
        deadline_at=time.time() + 120,
        deadline_started_at=time.time() - 10,
        decision_policy={"decisions": 4},
    )
    original_deadline = state["deadline_at"]
    session = _FakeSession()
    session.update_state({PHASE: state})
    runtime = MagicMock(spec=BrowserAgentRuntime)
    runtime.service = MagicMock()
    result = BrowserRuntimeRail(runtime)._resume_task_state(session, state, "Read the visible title again")
    assert "browser_execution_error" not in result["blockers"]
    assert result["deadline_at"] == original_deadline
    assert result["decision_policy"] == {"decisions": 4}


@pytest.mark.asyncio
async def test_recovery_metadata_includes_fresh_decision_targets_without_extra_ax_snapshot():
    policy, llm, client, fake, context, captured = setup_policy()
    runtime = _make_runtime()
    runtime.decision_policy = policy
    runtime.ensure_runtime_ready = AsyncMock()
    runtime._call_playwright_tool = AsyncMock()
    page = fake._ensure_page_state()
    runtime._capture_browser_metadata = AsyncMock(
        return_value=(
            {
                "url": page.url,
                "title": page.title,
                "decision_probe": {
                    "ok": True,
                    "url": page.url,
                    "decision_snapshot": page.decision_snapshot,
                    "elements": [
                        {
                            "selector_hint": "#sales",
                            "selector_hint_validated": True,
                            "match_count": 1,
                            "role": "button",
                            "accessible_name": "销量",
                            "visible": True,
                            "enabled": True,
                            "actionable": True,
                            "clickable": True,
                            "decision_state": page.export_decision_targets()[0]["decision_state"],
                        }
                    ],
                },
            },
            None,
        )
    )
    observed = await runtime.capture_reconciliation_browser_state(
        action_group_id="failed-action", include_decision=True
    )
    assert observed["decision_observation"]["controls"]
    assert observed["reconciliation_only"] and observed["dom"] == ""
    runtime._capture_browser_metadata.assert_awaited_once_with(include_decision=True)
    runtime._call_playwright_tool.assert_not_awaited()


@pytest.mark.asyncio
async def test_failed_dispatch_leaves_other_fresh_local_choices_available():
    policy, llm, client, runtime, context, captured = setup_policy()
    result = await policy.invoke(await messages_for(policy, context, captured), tools=TOOLS)
    call = result.tool_calls[0]
    policy.record_execution(
        SimpleNamespace(tool_call=call), context.get_session_ref(), {"success": False, "executed": None}
    )
    assert not policy._guards
    page = runtime._ensure_page_state()
    page.get_target(page.export_decision_targets()[0]["target_id"]).name = "new control"
    failed = ToolMessage(tool_call_id=call.id, content='{"ok":false}', metadata={"success": False})
    result = await policy.invoke([failed, *await messages_for(policy, context, captured)], tools=TOOLS)
    assert result.tool_calls and client.evaluate.await_count == 2
    observed = ToolMessage(tool_call_id="llm-snapshot", content='{"ok":true}', metadata={"success": True})
    result = await policy.invoke([observed, *await messages_for(policy, context, captured)], tools=TOOLS)
    assert result.metadata["browser_policy"]["cached_fallback"]
    assert client.evaluate.await_count == 2
