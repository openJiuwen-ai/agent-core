# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""September 24 trajectories: light intent, partial execution and domain proof."""

import asyncio
import copy
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from jsonschema import ValidationError, validate
from openjiuwen.core.foundation.llm import ToolCall
from openjiuwen.core.foundation.llm.schema.message import ToolMessage
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext, ToolCallInputs
from openjiuwen.harness.tools.browser_move.playwright_runtime import cart_verification as cart
from openjiuwen.harness.tools.browser_move.playwright_runtime import execution_journal as journal
from openjiuwen.harness.tools.browser_move.playwright_runtime.browser_state_context_processor import (
    BrowserStateContextProcessor as Processor,
)
from openjiuwen.harness.tools.browser_move.playwright_runtime.browser_state_context_processor import (
    BrowserStateContextProcessorConfig,
)
from openjiuwen.harness.tools.browser_move.playwright_runtime.browser_working_context import BrowserWorkingContextStore
from openjiuwen.harness.tools.browser_move.playwright_runtime.phase_contract import (
    PHASE_KEY,
    BrowserPhaseTool,
    missing_conditions,
    observe_conditions,
    observe_runtime,
    read_cart,
    set_phase,
    unresolved_writes,
)
from openjiuwen.harness.tools.browser_move.playwright_runtime.runtime import BrowserAgentRuntime
from openjiuwen.harness.tools.browser_move.playwright_runtime.runtime import BrowserRuntimeRail as Rail
from openjiuwen.harness.tools.browser_move.playwright_runtime.runtime_tools import build_browser_runtime_tools
from openjiuwen.harness.tools.subagent.task_tool import TaskTool
from tests.unit_tests.harness.tools.browser_move.test_browser_jev_phase_contract import (
    call,
    cart_runtime,
    cart_state,
    control,
    new_state,
    session_for,
)
from tests.unit_tests.harness.tools.browser_move.test_browser_jev_policy import TOOLS, messages_for, setup_policy
from tests.unit_tests.harness.tools.browser_move.test_browser_september17_contracts import dom_page  # noqa: F401


def ui_runtime(*controls):
    page = SimpleNamespace(
        export_decision_targets=lambda: list(controls), get_target=lambda _: SimpleNamespace(selector=None, ref=None)
    )
    return SimpleNamespace(_ensure_page_state=lambda: page)


def record(session, inputs, result, runtime=None, *, success=False):
    journal.prepare(session, inputs, runtime, effect_adapter=cart.prepare_effects)
    receipt = journal.record_result(session, inputs, {"success": success}, result)
    cart.record_effects(journal.task_state(session), inputs.tool_call.id)
    return receipt


def test_local_intent_needs_only_objective_and_does_not_reset_budgets():
    state = Rail._build_phase_state("搜索“鼠标”，然后读取标题")
    session = session_for(state)
    deadline = state.get("deadline_at")
    budgets = copy.deepcopy(state["phases"])
    set_phase(state, {"objective": "搜索鼠标"}, [])
    assert state["phase_requirements"] == []
    set_phase(state, {"objective": "搜索鼠标"}, [])
    assert state["active_phase_contract"]["version"] == 1
    for _ in range(14):  # Ctrip: bad management calls previously exhausted extraction.
        assert Rail._consume_phase_budget(session, "browser_phase", {"op": "set"}) == "management"
        with pytest.raises(ValueError):
            set_phase(state, {"objective": "continue", "conditions": [{"type": "selected"}]}, [])
    assert state["phases"] == budgets and state.get("deadline_at") == deadline
    with pytest.raises(ValidationError):
        validate({"op": "set", "conditions": [{"type": "selected"}]}, BrowserPhaseTool(None).card.input_params)


def test_intent_replaces_old_nodes_without_erasing_task_or_business_requirements():
    state, condition = cart_state()
    state["required_fields"] = ["title"]
    for node in range(1, 5):
        field = control(f"search-{node}", "Search", node, tag="input")
        field["decision_state"]["node_guard"]["document"] = f"doc-{node}"
        set_phase(
            state,
            {
                "objective": "搜索",
                "bound_values": [{"target_id": field["target_id"], "value": "mouse"}],
                "conditions": [{"kind": "control_value", "target_id": field["target_id"], "value": "mouse"}],
            },
            [field],
        )
    assert len(state["phase_requirements"]) == 2
    assert state["phase_requirements"][0]["id"] == condition["id"]
    assert state["required_fields"] == ["title"]
    field["decision_state"]["current_value"] = "mouse"
    observe_conditions(state, {"capture_id": "new", "controls": [field], "url": "https://search.test/"})
    assert missing_conditions(state) == [condition["id"]]


@pytest.mark.parametrize("reading", [False, True])
def test_phase_metadata_never_mutates_page_and_only_actual_reads_update_observation(reading):
    tool_call = ToolCall(id="phase", type="function", name="browser_phase", arguments='{"op":"set"}')
    message = ToolMessage(
        tool_call_id="phase",
        content=json.dumps({"ok": True, "executed": True, "state_changed": False, "observation_updated": reading}),
    )
    assert not Rail._result_may_have_changed_browser_state("browser_phase", {"ok": True}, {"success": True})
    _, mutation, observation = Processor._classify_completed_action_group(
        [tool_call], completed_call_ids={"phase"}, executed_call_ids={"phase"}, tool_messages={"phase": message}
    )
    assert not mutation and bool(observation) is reading


@pytest.mark.parametrize("name", ["mcp_playwright-official_browser_type", "mcp_playwright-official_browser_click"])
def test_native_missing_ref_is_not_a_dispatched_write(name):
    state = new_state()
    session = session_for(state)
    inputs = call("missing-ref", name)
    inputs.tool_args = {"target": "f1e16", "text": "Python"}
    receipt = record(
        session,
        inputs,
        {"result": "### Error\nError: Ref f1e16 not found in the current page snapshot. Try capturing new snapshot."},
    )
    assert receipt["executed"] is False and not unresolved_writes(state)
    assert not state.get("cart_baseline_closed")
    record(session, call("next-step"), {"executed": False})


def test_page_text_cannot_impersonate_native_preflight_error():
    state = new_state()
    receipt = record(
        session_for(state),
        call("script", "browser_evaluate"),
        {"result": "### Error\nError: Ref f1e16 not found in the current page snapshot. Try capturing new snapshot."},
    )
    assert receipt["execution_state"] == "dispatched_unknown" and unresolved_writes(state)


def test_ctrip_partial_batch_preserves_acknowledged_rejected_and_pending_steps():
    state = new_state()
    session = session_for(state)
    inputs = call("ctrip")
    inputs.tool_args = {
        "steps": [{"op": "click", "target_id": target} for target in ("four-star", "five-star", "breakfast")]
    }
    receipt = record(
        session,
        inputs,
        {
            "status": "partial",
            "steps": [
                {"index": 0, "op": "click", "ok": True, "executed": True},
                {"index": 1, "op": "click", "ok": False, "executed": False, "error": "stale target"},
            ],
        },
    )
    assert receipt["execution_state"] == "partial"
    assert [s["execution_state"] for s in receipt["execution"]["steps"]] == [
        "acknowledged",
        "rejected_before_dispatch",
        "not_started",
    ]
    assert not unresolved_writes(state)
    record(session, call("continue-missing-filter"), {"executed": False})


def test_unknown_business_step_is_not_erased_by_partial_batch_or_page_progress():
    state = new_state()
    session = session_for(state)
    inputs = call("business")
    inputs.tool_args["steps"].append({"op": "click", "target_id": "confirm"})
    record(session, inputs, {"steps": [{"index": 0, "ok": True}, {"index": 1, "ok": False, "executed": None}]})
    state["recent_actions"] = [{"call_id": "business", "outcome_status": "ambiguous"}]
    assert not BrowserWorkingContextStore._reconcile_observed_action(state, {"observable_progress": True})
    for name in ("browser_batch_interact", "browser_evaluate"):
        with pytest.raises(ValueError, match="requires_reconciliation"):
            journal.prepare(session, call("retry-" + name, name))


@pytest.mark.asyncio
async def test_search_fill_does_not_close_cart_baseline_and_exact_observation_verifies_field():
    state, condition = cart_state()
    session = session_for(state)
    field = control("search", "Search", 1, tag="input", search_like=True, current_value="")
    inputs = call("llm-fill")
    inputs.tool_args = {"steps": [{"op": "fill", "target_id": "search", "value": "mouse"}]}
    record(session, inputs, {"ok": True}, ui_runtime(field), success=True)
    assert not state.get("cart_baseline_closed")
    field["decision_state"]["current_value"] = "mouse"
    await observe_runtime(
        ui_runtime(field),
        session,
        {
            "ok": True,
            "decision_observation": {"capture_id": "after-fill", "url": "https://search.test/", "controls": [field]},
        },
    )
    assert state["execution_journal"][0]["execution_state"] == "verified"
    await read_cart(cart_runtime({"old-item": 1}), condition, state, baseline=True)
    assert condition["baseline"]["items"] == {"old-item": 1}


@pytest.mark.parametrize("mode", ["llm", "hybrid"])
@pytest.mark.asyncio
async def test_cart_precondition_rejected_before_budget_in_both_modes(mode):
    state = Rail._build_phase_state("搜索鼠标并加购")
    state["decision_policy"] = {"mode": mode}
    session = session_for(state)
    observed = control("add", "Add to cart", 1)
    runtime = MagicMock(spec=BrowserAgentRuntime)
    runtime.decision_policy = None
    runtime._ensure_page_state.return_value = ui_runtime(observed)._ensure_page_state()
    runtime.normalize_model_batch_steps.side_effect = lambda s: s
    runtime.export_page_state.return_value = {}
    runtime.semantic_progress = {}
    inputs = ToolCallInputs(
        tool_call=ToolCall(id="add-no-baseline", type="function", name="browser_batch_interact", arguments="{}"),
        tool_name="browser_batch_interact",
        tool_args={"steps": [{"op": "click", "target_id": "add"}]},
    )
    ctx = AgentCallbackContext(agent=MagicMock(), session=session, inputs=inputs)
    before = copy.deepcopy(state["phases"])
    await Rail(runtime).before_tool_call(ctx)
    assert "cart_baseline_required" in str(ctx.inputs.tool_result)
    assert session.get_state(PHASE_KEY)["phases"] == before
    assert not session.get_state(PHASE_KEY).get("execution_journal")


@pytest.mark.asyncio
async def test_cart_unknown_effect_auto_reconciles_without_phase_verify_or_policy():
    state, condition = cart_state()
    state.pop("decision_policy")
    session = session_for(state)
    runtime = cart_runtime({"old-item": 2, "mouse-black": 1})
    await read_cart(runtime, condition, state, baseline=True)
    record(session, call(), {"executed": None}, runtime)
    assert unresolved_writes(state)
    state["phase_requirements"].append({**copy.deepcopy(condition), "id": "unrelated-old-cart-reader"})
    runtime._call_playwright_run_code_unsafe.return_value["items"]["mouse-black"] = 2
    captured = {"ok": True, "decision_observation": {"capture_id": "after-cart", "url": "https://shop.test/cart"}}
    await observe_runtime(runtime, session, captured)
    assert not unresolved_writes(state)
    assert state["execution_journal"][0]["execution_state"] == "verified"
    assert condition["status"] == "unsatisfied"  # Keyboard still required.
    assert runtime._call_playwright_run_code_unsafe.await_count == 2  # Baseline + one relevant effect read.
    count = runtime._call_playwright_run_code_unsafe.await_count
    await observe_runtime(runtime, session, captured)
    assert runtime._call_playwright_run_code_unsafe.await_count == count


@pytest.mark.parametrize("mode", ["llm", "hybrid"])
def test_phase_is_available_for_business_verification_in_both_modes(mode):
    runtime = SimpleNamespace(decision_policy=None if mode == "llm" else object())
    assert "browser_phase" in {tool.card.name for tool in build_browser_runtime_tools(runtime)}


def test_positive_empty_cart_proof_uses_real_dom(dom_page):  # noqa: F811
    state, condition = cart_state()
    dom_page.set_content('<span id="cart-line-count">0</span>')
    data = dom_page.evaluate(
        "async code => await eval('(' + code + ')')({evaluate:(fn,arg)=>fn(arg)})",
        cart.CART_READER.replace("__SPEC__", json.dumps(condition["spec"])),
    )
    assert data["coverage_count"] == 0 and data["complete"]
    runtime = cart_runtime({})
    runtime._call_playwright_run_code_unsafe.return_value = data
    # Playwright's synchronous fixture owns a running loop in this thread.
    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=1) as pool:
        pool.submit(lambda: asyncio.run(read_cart(runtime, condition, state, baseline=True))).result()
    assert condition["baseline"]["items"] == {}
    dom_page.set_content('<div class="cart-row" data-sku="unrelated"><input class="qty" value="1"></div>')
    missing = dom_page.evaluate(
        "async code => await eval('(' + code + ')')({evaluate:(fn,arg)=>fn(arg)})",
        cart.CART_READER.replace("__SPEC__", json.dumps(condition["spec"])),
    )
    assert not missing["ok"]


@pytest.mark.asyncio
async def test_target_only_scope_does_not_waive_explicit_whole_cart_requirement():
    state, condition = cart_state()
    spec = {**condition["spec"], "preserve_existing": False}
    with pytest.raises(ValueError, match="preserve_existing"):
        set_phase(state, {"objective": "add", "conditions": [spec]}, [])
    state = {**new_state(), "goal": "Add the selected mouse and keyboard"}
    set_phase(state, {"objective": "add", "conditions": [spec]}, [])
    condition = state["phase_requirements"][0]
    runtime = cart_runtime({"old-item": 2})
    await read_cart(runtime, condition, state, baseline=True)
    runtime._call_playwright_run_code_unsafe.return_value["items"] = {"old-item": 3, "mouse-black": 1, "keyboard-us": 1}
    await read_cart(runtime, condition, state, baseline=False)
    assert condition["status"] == "satisfied"


@pytest.mark.asyncio
async def test_simple_goal_with_then_does_not_require_phase_but_ambiguous_values_do():
    policy, _, client, _, context, captured = setup_policy(goal="搜索“mouse”，然后读取结果")
    await policy.invoke(await messages_for(policy, context, captured), tools=[*TOOLS, {"name": "browser_phase"}])
    assert client.evaluate.await_count == 1
    policy, llm, client, _, context, captured = setup_policy(goal="分别搜索“mouse”和“keyboard”")
    response = await policy.invoke(await messages_for(policy, context, captured), tools=TOOLS)
    assert response.metadata["browser_policy"]["reason"] == "no_supported_actions"
    client.evaluate.assert_not_awaited()
    llm.invoke.assert_awaited_once()


def test_pending_business_record_survives_journal_compaction_and_reaches_parent():
    state = Rail._build_phase_state("搜索并保存")
    session = session_for(state)
    record(session, call("unknown-save"), {"executed": None})
    field = control("search", "Search", 1, tag="input", search_like=True)
    for index in range(70):
        inputs = call(f"local-{index}")
        inputs.tool_args = {"steps": [{"op": "fill", "target_id": "search", "value": "mouse"}]}
        record(session, inputs, {"ok": True}, ui_runtime(field), success=True)
    assert len(state["execution_journal"]) == 64
    assert unresolved_writes(state)[0]["call_id"] == "unknown-save"
    payload = Rail._authoritative_terminal_payload(state)
    assert payload["execution"]["unresolved"][0]["call_id"] == "unknown-save"
    result = TaskTool._build_result_data(
        {"authoritative_browser_result": payload},
        "partial",
        agent_id="browser",
        subagent_type="browser_agent",
        sub_session_id="child",
    )
    assert result["resume_context"]["execution"] == payload["execution"]


def test_verification_budget_is_bounded_and_phase_updates_cannot_reset_it():
    state = Rail._build_phase_state("查询酒店")
    state.update(replan_required=True, replan_trial_pending=True)
    state["phases"]["extraction"]["attempts"] = 20
    session = session_for(state)
    for index in range(3):
        assert Rail._consume_phase_budget(session, "browser_snapshot", {}) == "verification"
        set_phase(state, {"objective": f"继续 {index}"}, [])
    with pytest.raises(ValueError, match="budget exhausted"):
        Rail._consume_phase_budget(session, "browser_snapshot", {})
    assert state["phases"]["extraction"]["attempts"] == 20


@pytest.mark.asyncio
async def test_normal_observation_lifecycle_verifies_without_policy_or_extra_capture():
    from openjiuwen.core.context_engine import ContextWindow

    state = new_state()
    field = control("q", "Search", 1, tag="input", current_value="mouse")
    set_phase(
        state,
        {"objective": "填入查询", "conditions": [{"kind": "control_value", "target_id": "q", "value": "mouse"}]},
        [field],
    )
    session = session_for(state)
    provider = SimpleNamespace(
        capture_browser_state=AsyncMock(
            return_value={
                "ok": True,
                "url": "https://search.test/",
                "dom": "search mouse",
                "decision_observation": {"capture_id": "normal", "controls": [field], "url": "https://search.test/"},
            }
        )
    )
    context = SimpleNamespace(get_session_ref=lambda: session, get_messages=lambda: [])
    processor = Processor(BrowserStateContextProcessorConfig(provider=provider))
    _, window = await processor.on_get_context_window(context, ContextWindow(context_messages=[]))
    assert state["active_phase_contract"]["status"] == "verified"
    await processor.on_get_context_window(context, window)
    provider.capture_browser_state.assert_awaited_once_with(action_group_id="initial", include_decision=True)


@pytest.mark.asyncio
async def test_cart_reader_failure_keeps_unknown_effect_and_available_llm_context():
    state, condition = cart_state()
    session = session_for(state)
    runtime = cart_runtime({"mouse-black": 1})
    await read_cart(runtime, condition, state, baseline=True)
    record(session, call(), {"executed": None}, runtime)
    runtime._call_playwright_run_code_unsafe.side_effect = OSError("offline")
    await observe_runtime(
        runtime, session, {"ok": True, "decision_observation": {"capture_id": "fresh", "url": "https://shop.test/cart"}}
    )
    assert unresolved_writes(state) and condition["reason"] == "cart_verification_unavailable:OSError"


def test_native_ax_search_fill_is_local_without_jev_target_registry():
    from openjiuwen.harness.tools.browser_move.playwright_runtime.page_state import BrowserPageState

    page = BrowserPageState(page_id="search-page")
    page.register_ax_snapshot('- textbox "Search" [ref=f1e1]')
    inputs = call("native-search", "mcp_playwright-official_browser_type")
    inputs.tool_args = {"target": "f1e1", "text": "mouse"}
    state = new_state()
    record(session_for(state), inputs, {"ok": True}, SimpleNamespace(_ensure_page_state=lambda: page), success=True)
    assert state["execution_journal"][0]["impact"] == "local_ui"
    assert not state.get("cart_baseline_closed")


def test_rejected_duplicate_id_cannot_clear_prior_unknown_effect():
    state = new_state()
    session = session_for(state)
    inputs = call("same-call")
    record(session, inputs, {"executed": None})
    with pytest.raises(ValueError, match="already_prepared"):
        journal.prepare(session, inputs)
    journal.record_result(session, inputs, {"denied": True}, {"executed": False})
    assert unresolved_writes(state)[0]["execution_state"] == "dispatched_unknown"


def test_satisfied_optional_fill_condition_does_not_block_search_submit():
    from openjiuwen.harness.tools.browser_move.decision.action_space import build_menu
    from openjiuwen.harness.tools.browser_move.playwright_runtime.phase_contract import constrain_actions

    field = control("q", "Search", 1, tag="input", current_value="mouse", search_like=True)
    field["role"] = "searchbox"
    state = new_state()
    set_phase(
        state,
        {
            "objective": "搜索 mouse 并读取结果",
            "bound_values": [{"target_id": "q", "value": "mouse"}],
            "conditions": [{"kind": "control_value", "target_id": "q", "value": "mouse"}],
        },
        [field],
    )
    observe_conditions(state, {"capture_id": "filled", "controls": [field]})
    menu = build_menu([field], state["goal"], limit=30, field_bindings=state["active_phase_contract"]["bindings"])
    constrain_actions(menu, state, [field])
    assert list(menu.steps.values()) == [{"target_id": "q", "op": "press", "key": "Enter"}]


def test_navigation_expires_unexecuted_node_binding_without_forging_proof():
    state = new_state()
    old = control("old-sort", "Sort", 1)
    set_phase(
        state, {"objective": "查询", "conditions": [{"kind": "control_selected", "target_id": "old-sort"}]}, [old]
    )
    fresh = control("new-page", "Read article", 2)
    fresh["decision_state"]["node_guard"]["document"] = "new-document"
    observe_conditions(state, {"capture_id": "next-page", "controls": [fresh]})
    assert not missing_conditions(state)
    assert state["phase_requirements"][0]["status"] == "expired"
    assert state["active_phase_contract"]["status"] != "verified"


def test_hotel_room_quantity_is_not_a_cart_mutation():
    state = new_state()
    state["goal"] = "筛选酒店，填写房间数量"
    rooms = control("rooms", "房间数量", 1, tag="input", input_type="number")
    inputs = call("rooms")
    inputs.tool_args = {"steps": [{"op": "fill", "target_id": "rooms", "value": "2"}]}
    record(session_for(state), inputs, {"ok": True}, ui_runtime(rooms), success=True)
    assert not state.get("cart_write_seen") and not state.get("cart_baseline_closed")


def test_acknowledged_unknown_cart_script_cannot_be_followed_by_another_business_write():
    state, _ = cart_state()
    session = session_for(state)
    script = call("script-add", "mcp_playwright_browser_evaluate")
    script.tool_args = {"function": "() => customAdd()"}
    record(session, script, {"ok": True}, success=True)
    assert unresolved_writes(state)
    with pytest.raises(ValueError, match="requires_reconciliation"):
        journal.prepare(session, call("repeat"))
