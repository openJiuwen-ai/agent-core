# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Remaining September 24 P0 boundaries after runtime simplification."""

import asyncio
import copy
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from jsonschema import ValidationError, validate
from openjiuwen.core.foundation.llm import ModelClientConfig, ModelRequestConfig
from openjiuwen.harness.subagents.browser_agent import _browser_model_with_temperature
from openjiuwen.harness.tools.browser_move.playwright_runtime import cart_verification as cart
from openjiuwen.harness.tools.browser_move.playwright_runtime import execution_journal as journal
from openjiuwen.harness.tools.browser_move.playwright_runtime.browser_state_context_processor import (
    BrowserStateContextProcessor as Processor,
)
from openjiuwen.harness.tools.browser_move.playwright_runtime.evidence import (
    explicit_acceptance,
    observe_acceptance,
    retain_acceptance,
)
from openjiuwen.harness.tools.browser_move.playwright_runtime.phase_contract import (
    PHASE_KEY,
    PHASE_SCHEMA,
    BrowserPhaseTool,
    PhaseInputError,
    binding_targets,
    set_phase,
    validate_request,
)
from openjiuwen.harness.tools.browser_move.playwright_runtime.probes import build_interactive_probe_js
from openjiuwen.harness.tools.browser_move.playwright_runtime.runtime import BrowserRuntimeRail as Rail
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


@pytest.mark.parametrize(
    "condition,path",
    [
        ({"type": "url_query", "key": "q", "value": "耳机"}, "conditions[0].kind"),
        ({"kind": "url_query", "value": "耳机"}, "conditions[0]"),
        ({"kind": "control_selected"}, "conditions[0]"),
        ({"kind": "evidence", "field": "title", "variant": "default"}, "conditions[0]"),
        ({"kind": "control_value", "target_id": "t1", "value": True}, "conditions[0].value"),
    ],
)
def test_categorical_schema_returns_actionable_error_without_mutation(condition, path):
    args = {"op": "set", "objective": "筛选酒店", "conditions": [condition]}
    with pytest.raises(ValidationError):
        validate(args, PHASE_SCHEMA)
    state = new_state()
    before = copy.deepcopy(state)
    with pytest.raises(PhaseInputError) as exc:
        set_phase(state, args, [])
    assert exc.value.details["path"] == path
    assert state == before


@pytest.mark.parametrize(
    "args", [{"op": "set"}, {"op": "verify", "objective": "x"}, {"op": "set", "objective": "x", "phase_version": 1}]
)
def test_set_and_verify_have_distinct_required_fields(args):
    with pytest.raises(PhaseInputError):
        validate_request(args)


@pytest.mark.parametrize("reason", ["not_in_current_observation", "missing_identity", "non_unique"])
def test_bad_binding_returns_exact_reason_and_current_bindable_targets(reason):
    good = control("fresh", "City", 1, tag="input")
    bad = control("stale", "City", 2)
    controls = [good]
    if reason == "missing_identity":
        bad["decision_state"]["node_guard"] = {"document": "doc", "node": None}
        controls.append(bad)
    if reason == "non_unique":
        controls += [bad, copy.deepcopy(bad)]
    state = new_state()
    with pytest.raises(PhaseInputError) as exc:
        set_phase(
            state, {"objective": "fill city", "bound_values": [{"target_id": "stale", "value": "上海"}]}, controls
        )
    assert exc.value.details["reason"] == reason
    assert [c["target_id"] for c in exc.value.details["bindings"]["targets"]] == ["fresh"]
    header = Processor._state_header({"decision_observation": {"controls": controls}})
    assert header["phase_bindings"] == binding_targets(controls, limit=12)
    set_phase(state, {"objective": "fill city", "bound_values": [{"target_id": "fresh", "value": "上海"}]}, controls)
    assert state["active_phase_contract"]["version"] == 1


@pytest.mark.asyncio
async def test_verify_can_refresh_bindable_targets_before_any_phase_exists():
    state = new_state()
    observation = {"capture_id": "fresh", "url": "https://test.example/", "controls": [control("t1", "City", 1)]}
    runtime = SimpleNamespace(
        _ensure_page_state=lambda: SimpleNamespace(export_decision_observation=lambda: {}),
        capture_reconciliation_browser_state=AsyncMock(return_value={"ok": True, "decision_observation": observation}),
    )
    output = await BrowserPhaseTool(runtime).invoke({"op": "verify"}, session=session_for(state))
    assert output.success and output.data["bindings"]["targets"][0]["target_id"] == "t1"
    assert output.data["state_changed"] is False and output.data["observation_updated"] is True


@pytest.mark.asyncio
async def test_fallback_instructions_reach_llm_and_local_correction_reenters_jev():
    policy, llm, client, runtime, context, captured = setup_policy(goal="搜索“鼠标”并按销量排序，然后搜索“键盘”")
    messages = await messages_for(policy, context, captured)
    await policy.invoke(messages, tools=TOOLS)
    handoff = llm.invoke.call_args.kwargs["messages"][-1]
    assert handoff.name == "browser_policy_handoff" and "no_supported_actions" in handoff.content
    assert "target_id" in handoff.content and "node_guard" not in handoff.content
    assert len(messages) == 1  # Never accumulate synthetic hints in the conversation.
    state = context.get_session_ref().get_state(PHASE_KEY)
    set_phase(state, {"objective": "点击销量排序"}, runtime._ensure_page_state().export_decision_targets())
    result = await policy.invoke(await messages_for(policy, context, captured), tools=TOOLS)
    assert result.tool_calls and result.tool_calls[0].id.startswith("jev_")
    assert client.evaluate.await_count == 1 and llm.invoke.await_count == 1
    assert client.evaluate.call_args.args[0]["state"]["phase"]["version"] == 1


@pytest.mark.asyncio
async def test_unknown_business_handoff_is_read_before_retry():
    policy, llm, _, _, context, captured = setup_policy()
    state = context.get_session_ref().get_state(PHASE_KEY)
    state["execution_journal"] = [{"call_id": "cart-1", "execution_state": "dispatched_unknown", "impact": "business"}]
    await policy.invoke(await messages_for(policy, context, captured), tools=TOOLS)
    hint = llm.invoke.call_args.kwargs["messages"][-1].content
    assert "no_supported_actions" in hint and "cart-1" in hint
    assert "never invent a baseline" in hint


def search_state(order="price-asc", *, goal="搜索蓝牙耳机，按销量排序，返回第一条商品标题"):
    state = Rail._build_phase_state(goal)
    source = f"https://s.taobao.com/search?q=蓝牙耳机&sort={order}"
    state["last_page"] = {"url": source}
    state["structured_evidence"] = [
        {
            "kind": "card_probe",
            "source": source,
            "query_id": state["task_id"],
            "cards": [
                {
                    "result_index": 1,
                    "order_known": True,
                    "region": "main_result",
                    "is_ad": False,
                    "title": "耳机A",
                    "primary_link": "https://item.taobao.com/item.htm?id=123",
                }
            ],
        }
    ]
    return state


def test_weak_query_phase_cannot_certify_sales_sort_or_parent_completed():
    state = search_state()
    set_phase(
        state,
        {"objective": "销量排序已完成", "conditions": [{"kind": "url_query", "key": "q", "value": "蓝牙耳机"}]},
        [],
    )
    session = session_for(state)
    Rail._apply_worker_progress_to_task_state(session, {"status": "completed"}, "已完成，第一条耳机A")
    assert state["status"] == "partial"
    result = Rail._authoritative_terminal_payload(state)
    assert "sort:sales" in result["missing_fields"] and "first_result:sales" in result["missing_fields"]
    assert result["status"] != "completed"


def test_sales_sort_and_first_result_proof_survive_node_rebinding_and_bounded_history():
    state = search_state("sale-desc")
    assert all(r["status"] == "satisfied" for r in explicit_acceptance(state))
    retain_acceptance(state)
    state["structured_evidence"] = [{"kind": "page_observation", "source": "https://item.taobao.com/item.htm?id=123"}]
    for i in range(3):
        set_phase(state, {"objective": f"read next field {i}"}, [])
    assert len(state["phase_history"]) == 2
    assert all(r["status"] == "satisfied" for r in explicit_acceptance(state))
    state["query_id"] = "new-task"
    assert all(r["status"] == "unknown" for r in explicit_acceptance(state))


@pytest.mark.parametrize("unproven", ["arguments", "other_query", "ad", "unordered"])
def test_sort_and_first_result_evidence_cannot_be_invented_from_tool_intent(unproven):
    state = search_state("sale-desc")
    record = state["structured_evidence"][0]
    if unproven == "arguments":
        record["source"] = "https://s.taobao.com/search?q=蓝牙耳机&sort=price-asc"
        variant, _ = Rail._evidence_variant_and_url(state, {"url": record["source"]}, {"text": "最新"})
        assert variant == "default"
    elif unproven == "other_query":
        record["source"] = "https://s.taobao.com/search?q=键盘&sort=sale-desc"
    elif unproven == "ad":
        record["cards"][0]["is_ad"] = True
    else:
        record["cards"][0]["order_known"] = False
    assert any(r["status"] == "unknown" for r in explicit_acceptance(state))


def test_fresh_observation_proves_sort_without_extra_phase_call():
    state = search_state("sale-desc", goal="当前页面切换到销量排序")
    state["structured_evidence"] = []
    observe_acceptance(state, {"url": state["last_page"]["url"], "capture_id": "new", "controls": []})
    assert explicit_acceptance(state)[0]["status"] == "satisfied"


def test_product_and_shop_rating_are_not_interchangeable_and_optional_is_advisory():
    state = search_state(goal="返回商品评分和店铺评分")
    state["evidence_slots"] = [
        {
            "field": "shop_rating",
            "status": "present",
            "value": "4.8",
            "source": "https://shop.test/one",
            "query_id": state["task_id"],
        }
    ]
    requirements = {r["id"]: r["status"] for r in explicit_acceptance(state)}
    assert requirements == {"field:product_rating": "unknown", "field:shop_rating": "satisfied"}
    state["goal"] = "返回标题，商品评分若有则提供，店铺评分不用"
    assert explicit_acceptance(state) == []


@pytest.mark.asyncio
async def test_lazada_unbound_click_plus_added_is_guarded_before_dispatch_and_preserves_feedback():
    state, condition = cart_state()
    session = session_for(state)
    inputs = call("llm_add")
    inputs.tool_args["steps"] = [
        {"op": "click", "selector": "#unregistered-add"},
        {"op": "wait_for_text", "text": "Added"},
    ]
    runtime = cart_runtime({"old-item": 2, "mouse-black": 1})
    with pytest.raises(ValueError, match="cart_baseline_required"):
        journal.prepare(session, inputs, runtime, effect_adapter=cart.prepare_effects)
    assert not state.get("execution_journal")
    await cart.read_cart(runtime, condition, state, baseline=True)
    journal.prepare(session, inputs, runtime, effect_adapter=cart.prepare_effects)
    journal.record_result(
        session, inputs, {"success": True}, {"steps": [{"index": 0, "ok": True}, {"index": 1, "ok": True}]}
    )
    cart.record_effects(state, "llm_add")
    result = Rail._authoritative_terminal_payload(state)
    assert result["execution"]["unresolved"][0]["steps"][1]["observed_feedback"] == "Added"
    assert state["cart_write_seen"] and "unknown_browser_write" in result["missing_fields"]


@pytest.mark.asyncio
async def test_failed_cart_read_cannot_reuse_old_valid_preflight():
    state, condition = cart_state()
    runtime = cart_runtime({"old-item": 2, "mouse-black": 1})
    await cart.read_cart(runtime, condition, state, baseline=True)
    baseline = copy.deepcopy(condition["baseline"])
    runtime._call_playwright_run_code_unsafe.return_value = {"ok": False}
    await cart.read_cart(runtime, condition, state, baseline=False)
    with pytest.raises(ValueError, match="cart_baseline_required"):
        journal.prepare(session_for(state), call(), runtime, effect_adapter=cart.prepare_effects)
    assert condition["baseline"] == baseline


@pytest.mark.asyncio
async def test_cart_reader_rebinding_retains_logical_requirement_and_original_baseline():
    state, _ = cart_state()
    state["phase_requirements"] = []
    spec = copy.deepcopy(cart_state()[1]["spec"])
    spec["requirement_id"] = "cart-main"
    set_phase(state, {"objective": "cart", "conditions": [spec]}, [])
    item = state["phase_requirements"][0]
    runtime = cart_runtime({"old-item": 2, "mouse-black": 1})
    await cart.read_cart(runtime, item, state, baseline=True)
    baseline = copy.deepcopy(item["baseline"])
    spec["items_selector"] = ".new-cart-row"
    set_phase(state, {"objective": "cart reader changed", "conditions": [spec]}, [])
    assert len(state["phase_requirements"]) == 1
    item = state["phase_requirements"][0]
    assert item["baseline"] == baseline and item["status"] == "unknown"
    spec["deltas"] = {"mouse-black": 10}
    with pytest.raises(ValueError, match="cannot_change_expected_business_result"):
        set_phase(state, {"objective": "weaken", "conditions": [spec]}, [])


def test_probe_publishes_local_cart_capability_and_variant_identity(dom_page):  # noqa: F811
    dom_page.set_content('<div data-sku="mouse-black"><button id="add">Add to cart</button></div>')
    result = dom_page.evaluate(
        "async code => await eval('(' + code + ')')({evaluate: (fn,arg) => fn(arg)})",
        build_interactive_probe_js(decision_mode=True),
    )
    fields = result.get("decision_elements") or result.get("elements") or []
    effects = [(c.get("decision_state") or {}).get("effect") for c in fields]
    assert any(e and e["domain"] == "cart" and e["identities"] == {"data-sku": "mouse-black"} for e in effects), result


@pytest.mark.asyncio
async def test_observed_wrong_sku_cannot_use_another_products_baseline():
    state, item = cart_state()
    runtime = cart_runtime({"old-item": 2, "mouse-black": 1})
    await cart.read_cart(runtime, item, state, baseline=True)
    controls = runtime._ensure_page_state().export_decision_targets()
    controls[0]["decision_state"]["effect"] = {
        "domain": "cart",
        "operation": "add",
        "identities": {"data-sku": "wrong"},
    }
    with pytest.raises(ValueError, match="cart_target_sku"):
        journal.prepare(session_for(state), call(), runtime, effect_adapter=cart.prepare_effects)


def test_exhausted_action_budget_keeps_three_reads_without_resetting_budget():
    state = Rail._build_phase_state("搜索耳机返回标题")
    state["phases"]["extraction"]["attempts"] = state["phases"]["extraction"]["budget"]
    state["deadline_at"] = time.time() + 30
    session = session_for(state)
    with pytest.raises(ValueError, match="budget exhausted"):
        Rail._consume_phase_budget(session, "browser_probe_cards", {})
    for _ in range(3):
        assert Rail._consume_phase_budget(session, "browser_phase", {"op": "verify"}) == "verification"
    with pytest.raises(ValueError, match="action_budget_exhausted"):
        Rail._consume_phase_budget(session, "browser_phase", {"op": "verify"})
    assert state["status"] == "partial" and state["phases"]["extraction"]["attempts"] == 20


@pytest.mark.parametrize("configured,expected", [(300, 90), (20, 20), (None, 90)])
def test_browser_first_chunk_wait_is_local_and_respects_shorter_parent_setting(configured, expected):
    parent = SimpleNamespace(
        model_client_config=ModelClientConfig(
            client_provider="OpenAI",
            api_key="offline-test",
            api_base="https://model.test/v1",
            stream_first_chunk_timeout=configured,
        ),
        model_config=ModelRequestConfig(),
    )
    child = _browser_model_with_temperature(parent, 0.1)
    assert child.model_client_config.stream_first_chunk_timeout == expected
    assert parent.model_client_config.stream_first_chunk_timeout == configured


@pytest.mark.asyncio
async def test_first_chunk_timeout_preserves_execution_and_does_not_replay_model():
    state, _ = cart_state()
    state["worker_reported_blockers"] = ["need a future guest name"]
    state["execution_journal"] = [
        {
            "call_id": "added",
            "impact": "business",
            "execution_state": "acknowledged",
            "cart_mutation": True,
            "requires_verification": True,
            "steps": [
                {
                    "index": 0,
                    "op": "click",
                    "executed": True,
                    "execution_state": "acknowledged",
                    "observed_feedback": "Added",
                }
            ],
        }
    ]
    session = session_for(state)
    rail = Rail(SimpleNamespace())
    ctx = SimpleNamespace(
        session=session,
        exception=TimeoutError("first_chunk_elapsed"),
        retry_attempt=0,
        extra={},
        request_retry=MagicMock(),
        request_force_finish=MagicMock(),
    )
    await rail.on_model_exception(ctx)
    ctx.request_retry.assert_not_called()
    result = ctx.request_force_finish.call_args.args[0]
    facts = result["authoritative_browser_result"]
    assert facts["execution"]["business_effects"][0]["steps"][0]["observed_feedback"] == "Added"
    assert not facts["observed_blockers"] and facts["unconfirmed_blockers"] == ["need a future guest name"]
    data = TaskTool._build_result_data(
        result, result["output"], agent_id="browser", subagent_type="browser_agent", sub_session_id="sub"
    )
    assert data["resume_context"]["execution"] == facts["execution"]
    assert data["resume_context"]["unconfirmed_blockers"] == facts["unconfirmed_blockers"]


@pytest.mark.asyncio
async def test_stream_first_chunk_timeout_closes_producer_without_tools(monkeypatch):
    policy, fallback, _, _, _, _ = setup_policy()
    closed = []

    async def stuck(**_):
        try:
            await asyncio.Event().wait()
            yield
        finally:
            closed.append(True)

    fallback.stream = stuck
    monkeypatch.setattr(policy, "_llm_wait_limit", lambda _: 0.01)
    with pytest.raises(TimeoutError):
        async for _ in policy.stream([], tools=TOOLS):
            pass
    assert closed == [True]


@pytest.mark.asyncio
async def test_remaining_deadline_bounds_first_chunk_with_handoff_reserve(monkeypatch):
    parent = SimpleNamespace(
        model_client_config=ModelClientConfig(
            client_provider="OpenAI", api_key="offline-test", api_base="https://model.test/v1"
        ),
        model_config=ModelRequestConfig(),
    )
    child = _browser_model_with_temperature(parent, 0.1)
    state = new_state()
    state["deadline_at"] = time.time() + 20
    rail = Rail(SimpleNamespace(llm_model=child))
    monkeypatch.setattr(rail, "_sync_semantic_progress", lambda _: None)
    monkeypatch.setattr(rail, "_finish_if_task_deadline_exhausted", lambda *args: False)
    monkeypatch.setattr(rail, "_prepare_terminal_synthesis", lambda *args: False)
    ctx = SimpleNamespace(
        session=session_for(state),
        inputs=SimpleNamespace(messages=[]),
        extra={},
        agent=SimpleNamespace(system_prompt_builder=None),
    )
    await rail.before_model_call(ctx)
    assert 0 < child.model_client_config.stream_first_chunk_timeout <= 5
    assert parent.model_client_config.stream_first_chunk_timeout == 300


@pytest.mark.asyncio
async def test_other_requested_sku_increment_cannot_settle_known_mouse_action():
    state, item = cart_state()
    runtime = cart_runtime({"old-item": 2, "mouse-black": 1})
    await cart.read_cart(runtime, item, state, baseline=True)
    control = runtime._ensure_page_state().export_decision_targets()[0]
    control["decision_state"]["effect"] = {
        "domain": "cart",
        "operation": "add",
        "identities": {"data-sku": "mouse-black"},
    }
    session = session_for(state)
    inputs = call()
    journal.prepare(session, inputs, runtime, effect_adapter=cart.prepare_effects)
    journal.record_result(session, inputs, {"success": False}, {"executed": None})
    cart.record_effects(state, inputs.tool_call.id)
    runtime._call_playwright_run_code_unsafe.return_value["items"]["keyboard-us"] = 1
    await cart.read_cart(runtime, item, state, baseline=False)
    assert journal.unresolved_writes(state)


def test_fixed_cart_inspector_reads_hints_without_claiming_complete_cart(dom_page):  # noqa: F811
    dom_page.set_content(
        '<span id="cart-lines" aria-label="Distinct cart items">1</span>'
        '<div data-sku="mouse-black"><input type="number" value="2"></div>'
    )
    result = dom_page.evaluate(
        "async code => await eval('(' + code + ')')({evaluate: (fn,arg) => fn(arg)})", cart.CART_INSPECTOR
    )
    assert result["ok"] and result["complete"] is False
    assert result["reader_candidates"][0]["observed_skus"] == ["mouse-black"]
    assert result["reader_candidates"][0]["quantity_selector"] == 'input[type="number"]'
    assert result["count_candidates"][0]["selector"] == "#cart-lines"


@pytest.mark.asyncio
async def test_cart_inspection_does_not_close_baseline_or_create_proof():
    state = new_state()
    observation = {"capture_id": "new", "url": "https://shop.test/cart", "controls": []}
    hints = {"ok": True, "reader_candidates": [], "count_candidates": [], "complete": False}
    runtime = SimpleNamespace(
        _ensure_page_state=lambda: SimpleNamespace(export_decision_observation=lambda: observation),
        capture_reconciliation_browser_state=AsyncMock(return_value={"ok": True, "decision_observation": observation}),
        _call_playwright_run_code_unsafe=AsyncMock(return_value=hints),
        _unwrap_mcp_text_result=lambda x: x,
    )
    args = {"op": "verify", "inspect_cart": True}
    validate_request(args)
    result = await BrowserPhaseTool(runtime).invoke(args, session=session_for(state))
    assert result.success and result.data["cart_reader_hints"] == hints
    assert not state.get("cart_baseline_closed") and not state.get("phase_requirements")
    assert result.data["state_changed"] is False


def test_card_selected_sort_proves_variant_even_when_url_has_no_order():
    state = search_state()
    record = state["structured_evidence"][0]
    record["source"] = "https://s.taobao.com/search?q=蓝牙耳机"
    record["cards"][0]["sort_state"] = "销量"
    assert all(r["status"] == "satisfied" for r in explicit_acceptance(state))
    record["cards"][0]["sort_state"] = "最新发布"
    variant, _ = Rail._evidence_variant_and_url(state, {"url": record["source"], "cards": record["cards"]}, {})
    assert variant == "latest"


def test_phase_verify_shares_bounded_unknown_write_recovery_lane():
    state, _ = cart_state()
    state["execution_journal"] = [{"call_id": "add", "execution_state": "dispatched_unknown", "impact": "business"}]
    session = session_for(state)
    for _ in range(3):
        assert Rail._consume_phase_budget(session, "browser_phase", {"op": "verify"}) == "verification"
    assert Rail._consume_phase_budget(session, "browser_phase", {"op": "set", "objective": "inspect"}) == "management"
    with pytest.raises(ValueError, match="verification_recovery_budget_exhausted"):
        Rail._consume_phase_budget(session, "browser_phase", {"op": "verify", "inspect_cart": True})


def test_result_change_without_selected_order_cannot_certify_sort():
    state = search_state("")
    state["structured_evidence"] = [
        {
            "kind": "semantic_observation",
            "source": state["last_page"]["url"],
            "values": {"sort_state": "销量"},
            "provenance": {"sort_state": {"selection_source": "first_result_change"}},
        }
    ]
    assert all(r["status"] == "unknown" for r in explicit_acceptance(state))


def test_replacing_entity_rating_cannot_reuse_old_acceptance_cache():
    state = search_state(goal="返回商品评分")
    state["evidence_slots"] = [
        {
            "field": "product_rating",
            "status": "present",
            "value": "4.9",
            "source": "https://shop.test/product-a",
            "query_id": state["task_id"],
        }
    ]
    retain_acceptance(state)
    assert explicit_acceptance(state)[0]["status"] == "satisfied"
    state["evidence_slots"] = []  # Entity replacement evicts fields from the previous product.
    assert explicit_acceptance(state)[0]["status"] == "unknown"
