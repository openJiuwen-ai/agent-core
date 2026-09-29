# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Sanitized regressions for the September 23 ordering/input/cart trajectories.

These fixtures preserve observed failure sequences, not page content or claims
that the remote sites are deterministic. Real-site acceptance remains separate.
"""

import copy
import json
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from openjiuwen.harness.tools.browser_move.decision.action_space import build_menu
from openjiuwen.harness.tools.browser_move.decision.policy_model import (
    BrowserPolicyModel,
)
from openjiuwen.harness.tools.browser_move.playwright_runtime import cart_verification
from openjiuwen.harness.tools.browser_move.playwright_runtime import (
    execution_journal as journal,
)
from openjiuwen.harness.tools.browser_move.playwright_runtime.phase_contract import (
    CART_READER,
    PHASE_KEY,
    BrowserPhaseTool,
    constrain_actions,
    missing_conditions,
    observe_conditions,
    read_cart,
    set_phase,
    unresolved_writes,
)
from openjiuwen.harness.tools.browser_move.playwright_runtime.runtime import (
    BrowserRuntimeRail,
)
from openjiuwen.harness.tools.browser_move.playwright_runtime.runtime_tools import (
    BrowserBatchInteractTool,
)
from tests.unit_tests.harness.tools.browser_move.test_browser_jev_policy import (
    TOOLS,
    answer,
    messages_for,
    setup_policy,
)
from tests.unit_tests.harness.tools.browser_move.test_browser_runtime_rail import (
    _FakeSession,
)
from tests.unit_tests.harness.tools.browser_move.test_browser_september17_contracts import (
    dom_page,  # noqa: F401
)


def control(target, name, node, **details):
    return {
        "target_id": target,
        "name": name,
        "role": "button",
        "enabled": True,
        "actionable": True,
        "clickable": True,
        "selected": False,
        "decision_state": {
            "tag": "button",
            "node_guard": {"document": "doc", "node": node},
            **details,
        },
    }


def new_state():
    return {
        "task_id": "trace",
        "goal": "先综合读取，再最新排序读取",
        "deadline_at": time.time() + 60,
    }


def test_bilibili_g3_latest_must_not_return_to_default_g4():
    state = new_state()
    controls = [control("default", "综合排序", 1), control("latest", "最新发布", 2)]
    conditions = [
        {"kind": "url_query", "key": "order", "value": "pubdate"},
        {
            "kind": "evidence",
            "field": "title",
            "variant": "latest",
            "query": "openJiuwen",
        },
    ]
    set_phase(
        state,
        {
            "objective": "只读取最新第一条；综合结果已采集",
            "allowed_operations": ["click"],
            "conditions": conditions,
        },
        controls,
    )
    state["evidence_slots"] = [
        {
            "entity": "video",
            "field": "title",
            "variant": "comprehensive",
            "query_id": "trace",
            "source": "https://search.test/?keyword=openJiuwen",
            "value": "综合第一条",
        }
    ]
    snapshot = {
        "capture_id": "g3",
        "url": "https://search.test/?keyword=openJiuwen&order=pubdate",
        "controls": controls,
    }
    observe_conditions(state, snapshot)
    menu = build_menu(controls, state["goal"], limit=30)
    constrain_actions(menu, state, controls)
    assert not menu.steps  # Both sort toggles are unnecessary; extraction is next.
    assert state["active_phase_contract"]["status"] == "in_progress"
    state["evidence_slots"].append(
        {
            "entity": "video",
            "field": "title",
            "variant": "latest",
            "query_id": "trace",
            "source": snapshot["url"],
            "value": "最新第一条",
        }
    )
    observe_conditions(state, {**snapshot, "capture_id": "read-latest"})
    assert state["active_phase_contract"]["status"] == "verified"
    set_phase(
        state,
        {
            "objective": "下一段",
            "allowed_operations": ["navigate"],
            "conditions": [{"kind": "url", "value": "https://next.test/"}],
        },
        controls,
    )
    observe_conditions(state, {"capture_id": "other-page", "url": "https://next.test/"})
    assert not missing_conditions(state)  # Historical sort evidence need not be live simultaneously.


def test_lazada_successful_english_query_is_not_replaced_with_chinese_synonym():
    field = control(
        "search",
        "搜索",
        1,
        tag="input",
        search_like=True,
        current_value="wireless mouse",
    )
    field["role"] = "combobox"
    bound = {"document": "doc", "node": 1, "value": "wireless mouse"}
    menu = build_menu([field], "搜索“无线鼠标”，然后保留原有商品", limit=30, search_bindings=[bound])
    assert list(menu.steps.values()) == [{"target_id": "search", "op": "press", "key": "Enter"}]


def test_values_do_not_cross_fields_or_use_output_titles_as_input():
    name = control("name", "姓名", 1, tag="input", current_value="")
    city = control("city", "城市", 2, tag="input", current_value="")
    name["role"] = city["role"] = "textbox"
    goal = "输入“张三”和“北京”，输出标题“搜索结果”"
    assert not build_menu([name, city], goal, limit=30).steps
    menu = build_menu(
        [name, city],
        goal,
        limit=30,
        field_bindings=[{"document": "doc", "node": 2, "value": "北京"}],
    )
    assert list(menu.steps.values()) == [{"target_id": "city", "op": "fill", "value": "北京"}]
    assert not build_menu([name], "标题“搜索结果”", limit=30).steps


@pytest.mark.asyncio
async def test_phase_progress_and_executable_state_reach_jev_without_local_guards():
    policy, _, client, runtime, context, captured = setup_policy()
    state = context.get_session_ref().get_state(PHASE_KEY)
    controls = runtime._ensure_page_state().export_decision_targets()
    set_phase(
        state,
        {
            "objective": "只点击销量排序",
            "allowed_operations": ["click"],
            "target_ids": [controls[0]["target_id"]],
            "conditions": [{"kind": "control_selected", "target_id": controls[0]["target_id"]}],
        },
        controls,
    )
    captured["semantic_state"] = {
        "selected_filters": {"sort": "default"},
        "form_values": {"search": "mouse"},
    }
    await policy.invoke(await messages_for(policy, context, captured), tools=TOOLS)
    payload = client.evaluate.call_args.args[0]
    assert payload["state"]["current_intent"] == "只点击销量排序"
    assert payload["state"]["phase"]["version"] == 1
    assert "phase_contract" not in payload["state"]["runtime_progress"]  # Do not duplicate phase state.
    assert payload["state"]["executable_state"]["form_values"] == {"search": "mouse"}
    assert "node_guard" not in json.dumps(payload) and '"document"' not in json.dumps(payload)


@pytest.mark.asyncio
async def test_finish_reentry_preserves_budget_deadline_and_does_not_repeat_same_window():
    policy, llm, client, runtime, context, captured = setup_policy()
    client.evaluate.return_value = answer("FINISH")
    await policy.invoke(await messages_for(policy, context, captured), tools=TOOLS)
    await policy.invoke(await messages_for(policy, context, captured), tools=TOOLS)
    assert client.evaluate.await_count == 1
    state = context.get_session_ref().get_state(PHASE_KEY)
    deadline = state["deadline_at"]
    controls = runtime._ensure_page_state().export_decision_targets()
    set_phase(
        state,
        {
            "objective": "下一阶段点击销量",
            "allowed_operations": ["click"],
            "conditions": [{"kind": "control_selected", "target_id": controls[0]["target_id"]}],
        },
        controls,
    )
    client.evaluate.return_value = answer()
    policy = BrowserPolicyModel(llm, policy.decision_config, runtime, client=client)
    response = await policy.invoke(await messages_for(policy, context, captured), tools=TOOLS)
    assert response.tool_calls and client.evaluate.await_count == 2
    assert state["decision_policy"]["decisions"] == 2 and state["deadline_at"] == deadline


def cart_state():
    state = {
        "goal": "购物车增加鼠标和键盘各一件，保留原有商品",
        "task_id": "cart-trace",
        "decision_policy": {},
        "deadline_at": time.time() + 60,
    }
    spec = {
        "kind": "cart_delta",
        "items_selector": ".cart-row",
        "sku_attribute": "data-sku",
        "quantity_selector": "input.qty",
        "count_selector": "#cart-line-count",
        "deltas": {"mouse-black": 1, "keyboard-us": 1},
        "preserve_existing": True,
    }
    set_phase(
        state,
        {
            "objective": "加购并核对数量",
            "allowed_operations": ["click"],
            "conditions": [spec],
        },
        [],
    )
    return state, state["phase_requirements"][0]


def session_for(state):
    session = _FakeSession()
    session.update_state({PHASE_KEY: state})
    return session


def call(call_id="jev_add", name="browser_batch_interact"):
    return SimpleNamespace(
        tool_call=SimpleNamespace(id=call_id),
        tool_name=name,
        tool_args={"steps": [{"op": "click", "target_id": "add"}]},
    )


def cart_runtime(items):
    controls = [control("add", "Add to cart", 1), control("add-keyboard", "Add to cart", 2)]
    page = SimpleNamespace(export_decision_targets=lambda: controls,
                           get_target=lambda _: SimpleNamespace(selector=None, ref=None))
    return SimpleNamespace(
        _ensure_page_state=lambda: page,
        _call_playwright_run_code_unsafe=AsyncMock(
            return_value={"ok": True, "complete": True, "items": items, "url": "https://shop.test/cart"}
        ),
        _unwrap_mcp_text_result=lambda x: x,
    )


@pytest.mark.asyncio
async def test_add_timeout_blocks_both_models_until_actual_partial_delta_is_read():
    state, condition = cart_state()
    session = session_for(state)
    runtime = cart_runtime({"old-item": 2, "mouse-black": 1})
    await read_cart(runtime, condition, state, baseline=True)
    inputs = call()
    journal.prepare(session, inputs, runtime, effect_adapter=cart_verification.prepare_effects)
    with journal.execution_scope(session, inputs):
        journal.mark_dispatched()
    journal.record_result(session, inputs, {"success": False}, {"executed": None})
    for model_id in ("jev_retry", "llm_retry"):
        with pytest.raises(ValueError, match="requires_reconciliation"):
            journal.prepare(session, call(model_id))
    journal.prepare(session, call("read", "browser_snapshot"))
    with pytest.raises(ValueError, match="requires_reconciliation"):
        journal.prepare(session, call("js-retry", "mcp_playwright_browser_evaluate"))
    # An unrelated snapshot is not proof of either cart addition.
    observe_conditions(state, {"capture_id": "fresh", "url": "https://shop.test/cart"})
    assert unresolved_writes(state)
    runtime._call_playwright_run_code_unsafe.return_value["items"]["mouse-black"] = 2
    await read_cart(runtime, condition, state, baseline=False)
    assert not unresolved_writes(state)
    assert condition["status"] == "unsatisfied"  # Keyboard still missing; not full completion.
    journal.prepare(session, call("llm_add_keyboard"))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "observed",
    [
        {
            "old-item": 2,
            "mouse-black": 1,
            "keyboard-us": 1,
        },  # Logs: mouse increment was undone.
        {"old-item": 1, "mouse-black": 2, "keyboard-us": 1},  # Original item modified.
        {"old-item": 2, "mouse-black": 3, "keyboard-us": 1},  # Duplicate mutation.
        {"old-item": 2, "mouse-black": 2, "keyboard-uk": 1},  # Wrong variant.
    ],
)
async def test_cart_business_postconditions_reject_tool_ok_and_nonempty_cart(observed):
    state, condition = cart_state()
    runtime = cart_runtime({"old-item": 2, "mouse-black": 1})
    await read_cart(runtime, condition, state, baseline=True)
    runtime._call_playwright_run_code_unsafe.return_value["items"] = observed
    await read_cart(runtime, condition, state, baseline=False)
    assert condition["status"] == "unsatisfied"
    state.update(requirements_source="inferred", evidence_slots=[{"value": "cart is nonempty"}])
    session = session_for(state)
    BrowserRuntimeRail._apply_worker_progress_to_task_state(session, {"status": "completed"}, "全部加购成功")
    assert session.get_state(PHASE_KEY)["status"] == "partial"


@pytest.mark.asyncio
async def test_missing_baseline_cannot_be_reconstructed_after_unknown_write():
    state, condition = cart_state()
    state["cart_write_seen"] = True
    await read_cart(cart_runtime({"mouse-black": 2}), condition, state, baseline=True)
    assert "baseline" not in condition and condition["status"] == "unknown"


@pytest.mark.asyncio
async def test_cart_verified_delta_preserves_initial_baseline_and_original_items():
    state, condition = cart_state()
    runtime = cart_runtime({"old-item": 2, "mouse-black": 1})
    await read_cart(runtime, condition, state, baseline=True)
    baseline = copy.deepcopy(condition["baseline"])
    runtime._call_playwright_run_code_unsafe.return_value["items"] = {
        "old-item": 2,
        "mouse-black": 2,
        "keyboard-us": 1,
    }
    await read_cart(runtime, condition, state, baseline=True)
    assert condition["baseline"] == baseline and condition["status"] == "satisfied"
    assert observe_conditions(state, {"capture_id": "after", "url": "https://shop.test/cart"})["status"] == "verified"


@pytest.mark.asyncio
async def test_late_jev_guard_rejection_is_not_reported_as_executed():
    runtime = SimpleNamespace(
        decision_policy=SimpleNamespace(validate_tool_call=AsyncMock(side_effect=ValueError("stale"))),
        batch_interact=AsyncMock(),
    )
    inputs = call()
    output = await BrowserBatchInteractTool(runtime).invoke(
        inputs.tool_args,
        session=session_for(new_state()),
        _tool_callback_context=SimpleNamespace(inputs=inputs),
    )
    assert not output.success and output.data["executed"] is False
    assert output.data["execution_state"] == "rejected_before_dispatch"
    runtime.batch_interact.assert_not_awaited()


@pytest.mark.parametrize("invalid", ["duplicate", "missing", "fractional", "wrong_selector"])
def test_cart_reader_uses_real_dom_and_rejects_ambiguous_identity(dom_page, invalid):  # noqa: F811
    state, condition = cart_state()
    html = '<div class="cart-row" data-sku="mouse-black"><input class="qty" value="1"></div>'
    if invalid == "duplicate":
        html += html
    elif invalid == "missing":
        html = html.replace('data-sku="mouse-black"', "")
    elif invalid == "fractional":
        html = html.replace('value="1"', 'value="1.5"')
    else:
        html = html.replace('class="qty"', "")
    html += f'<span id="cart-line-count">{2 if invalid == "duplicate" else 1}</span>'
    dom_page.set_content(html)
    result = dom_page.evaluate(
        "async code => await eval('(' + code + ')')({evaluate:(fn,arg)=>fn(arg)})",
        CART_READER.replace("__SPEC__", json.dumps(condition["spec"])),
    )
    assert result["ok"] is False


@pytest.mark.parametrize("count,complete", [(2, True), (3, False), (None, False)])
def test_cart_reader_requires_line_count_to_prove_complete_dom(dom_page, count, complete):  # noqa: F811
    _, condition = cart_state()
    html = (
        '<div class="cart-row" data-sku="mouse-black"><input class="qty" value="2"></div>'
        '<div class="cart-row" data-sku="keyboard-us"><input class="qty" value="1"></div>'
    )
    if count is not None:
        html += f'<span id="cart-line-count">{count}</span>'
    dom_page.set_content(html)
    result = dom_page.evaluate(
        "async code => await eval('(' + code + ')')({evaluate:(fn,arg)=>fn(arg)})",
        CART_READER.replace("__SPEC__", json.dumps(condition["spec"])),
    )
    assert result["ok"] is complete
    if complete:
        assert result["complete"] and result["items"] == {"mouse-black": 2, "keyboard-us": 1}


@pytest.mark.asyncio
async def test_baseline_cannot_be_recreated_after_acknowledged_unstructured_write():
    state, condition = cart_state()
    session = session_for(state)
    inputs = call("llm-js-add", "mcp_playwright_browser_evaluate")
    inputs.tool_args = {"function": "() => document.querySelector('#add').click()"}
    journal.prepare(session, inputs)
    journal.record_result(session, inputs, {"success": True}, {"ok": True})
    cart_verification.record_effects(state, "llm-js-add")
    state["execution_journal"] = []  # Marker must outlive the bounded event history.
    await read_cart(cart_runtime({"mouse-black": 2}), condition, state, baseline=True)
    assert "baseline" not in condition
    assert condition["reason"] == "cart_baseline_not_available_before_write"


def test_read_only_batch_and_phase_verification_remain_available_after_unknown_write():
    state, _ = cart_state()
    session = session_for(state)
    inputs = call()
    journal.prepare(session, inputs)
    journal.record_result(session, inputs, {"success": False}, {"executed": None})
    read = call("read")
    read.tool_args = {"steps": [{"op": "extract_text", "selector": ".cart-row"}]}
    journal.prepare(session, read)
    verify = call("verify", "browser_phase")
    verify.tool_args = {"op": "verify", "phase_version": 1}
    journal.prepare(session, verify)
    assert BrowserRuntimeRail._is_read_only_recovery(verify.tool_name, verify.tool_args)
    assert len(unresolved_writes(state)) == 1  # Reading alone is not reconciliation.


@pytest.mark.asyncio
async def test_phase_tool_does_not_accept_model_supplied_proof_or_erase_requirements():
    policy, _, _, runtime, context, _ = setup_policy()
    runtime.decision_policy = policy
    tool = BrowserPhaseTool(runtime)
    session = context.get_session_ref()
    args = {
        "op": "set",
        "objective": "打开目标页",
        "allowed_operations": ["navigate"],
        "conditions": [{"kind": "url", "value": "https://next.test/"}],
    }
    assert (await tool.invoke(args, session=session)).success
    args["conditions"][0]["satisfied"] = True
    assert not (await tool.invoke(args, session=session)).success
    assert len(missing_conditions(session.get_state(PHASE_KEY))) == 1
    assert not (await tool.invoke({"op": "verify", "phase_version": 0}, session=session)).success


def test_confirmed_query_allows_submit_but_not_refill_or_binding_to_repurposed_field():
    state = new_state()
    field = control("query", "搜索", 1, tag="input", search_like=True, current_value="")
    field["role"] = "searchbox"
    field["decision_state"]["node_guard"]["signature"] = "search-label"
    set_phase(
        state,
        {
            "objective": "搜索无线鼠标",
            "allowed_operations": ["fill", "press"],
            "bound_values": [{"target_id": "query", "value": "wireless mouse"}],
            "conditions": [
                {"kind": "control_value", "target_id": "query", "value": "wireless mouse"},
                {"kind": "url_query", "key": "q", "value": "wireless mouse"},
            ],
        },
        [field],
    )
    field["decision_state"]["current_value"] = "wireless mouse"
    observe_conditions(state, {"capture_id": "filled", "url": "https://shop.test/", "controls": [field]})
    bindings = state["active_phase_contract"]["bindings"]
    menu = build_menu([field], "搜索无线鼠标", limit=30, field_bindings=bindings)
    constrain_actions(menu, state, [field])
    assert list(menu.steps.values()) == [{"target_id": "query", "op": "press", "key": "Enter"}]
    field["decision_state"]["current_value"] = ""
    field["decision_state"]["node_guard"]["signature"] = "address-label"
    assert not build_menu([field], "搜索无线鼠标", limit=30, field_bindings=bindings).steps


def test_new_phase_query_must_be_filled_before_submitting_previous_llm_query():
    field = control("query", "搜索", 1, tag="input", search_like=True, current_value="wireless mouse")
    field["role"] = "searchbox"
    previous = {"document": "doc", "node": 1, "value": "wireless mouse"}
    current = {"document": "doc", "node": 1, "value": "keyboard"}
    menu = build_menu([field], "接着搜索键盘", limit=30, search_bindings=[previous], field_bindings=[current])
    assert list(menu.steps.values()) == [{"target_id": "query", "op": "fill", "value": "keyboard"}]
    field["decision_state"]["current_value"] = "keyboard"
    menu = build_menu([field], "接着搜索键盘", limit=30, search_bindings=[previous], field_bindings=[current])
    assert list(menu.steps.values()) == [{"target_id": "query", "op": "press", "key": "Enter"}]


@pytest.mark.asyncio
async def test_phase_revision_invalidates_previously_compiled_action_before_dispatch():
    policy, _, _, runtime, context, captured = setup_policy()
    response = await policy.invoke(await messages_for(policy, context, captured), tools=TOOLS)
    compiled = response.tool_calls[0]
    controls = runtime._ensure_page_state().export_decision_targets()
    set_phase(
        context.get_session_ref().get_state(PHASE_KEY),
        {
            "objective": "新阶段",
            "allowed_operations": ["click"],
            "conditions": [{"kind": "control_selected", "target_id": controls[0]["target_id"]}],
        },
        controls,
    )
    with pytest.raises(ValueError, match="phase_changed"):
        policy.check_tool_call_binding(
            SimpleNamespace(tool_call=compiled, tool_name=compiled.name, tool_args=compiled.arguments),
            context.get_session_ref(),
        )
    runtime._call_playwright_run_code_unsafe.assert_not_awaited()


@pytest.mark.asyncio
async def test_jev_can_request_bounded_verification_through_normal_tool_binding():
    policy, _, client, runtime, context, captured = setup_policy()
    state = context.get_session_ref().get_state(PHASE_KEY)
    controls = runtime._ensure_page_state().export_decision_targets()
    set_phase(
        state,
        {
            "objective": "确认销量已选中",
            "allowed_operations": ["click"],
            "conditions": [{"kind": "control_selected", "target_id": controls[0]["target_id"]}],
        },
        controls,
    )

    def choose_verify(payload, **kwargs):
        from tests.unit_tests.harness.tools.browser_move.test_browser_jev_policy import grouped_answer
        return grouped_answer(payload, "VERIFY", "VERIFY")

    client.evaluate.side_effect = choose_verify
    response = await policy.invoke(
        await messages_for(policy, context, captured), tools=[*TOOLS, {"name": "browser_phase"}]
    )
    compiled = response.tool_calls[0]
    assert compiled.name == "browser_phase"
    inputs = SimpleNamespace(tool_call=compiled, tool_name=compiled.name, tool_args=compiled.arguments)
    policy.check_tool_call_binding(inputs, context.get_session_ref())
    runtime.decision_policy = policy
    runtime.capture_reconciliation_browser_state = AsyncMock(
        return_value={"ok": True, "decision_observation": runtime._ensure_page_state().export_decision_observation()}
    )
    output = await BrowserPhaseTool(runtime).invoke(
        json.loads(compiled.arguments),
        session=context.get_session_ref(),
        _tool_callback_context=SimpleNamespace(inputs=inputs),
    )
    assert output.success and output.data["phase"]["status"] == "in_progress"
    assert not policy._guards  # A verification request consumes its capability too.
    runtime.capture_reconciliation_browser_state.assert_awaited_once()
    runtime._call_playwright_run_code_unsafe.assert_not_awaited()


@pytest.mark.asyncio
async def test_unknown_multi_write_batch_cannot_resume_from_only_an_intermediate_cart_delta():
    state, condition = cart_state()
    session = session_for(state)
    runtime = cart_runtime({"old-item": 2, "mouse-black": 1})
    await read_cart(runtime, condition, state, baseline=True)
    inputs = call()
    inputs.tool_args["steps"].append({"op": "click", "target_id": "add-keyboard"})
    journal.prepare(session, inputs, runtime, effect_adapter=cart_verification.prepare_effects)
    with journal.execution_scope(session, inputs):
        journal.mark_dispatched()
    journal.record_result(session, inputs, {"success": False}, {"executed": None})
    runtime._call_playwright_run_code_unsafe.return_value["items"]["mouse-black"] = 2
    await read_cart(runtime, condition, state, baseline=False)
    assert unresolved_writes(state)  # The remote batch may still be executing its later step.


@pytest.mark.asyncio
async def test_verified_cart_quantities_do_not_prove_a_requested_total():
    state, condition = cart_state()
    state["goal"] += "并给出当前合计"
    runtime = cart_runtime({"old-item": 2, "mouse-black": 1})
    await read_cart(runtime, condition, state, baseline=True)
    runtime._call_playwright_run_code_unsafe.return_value["items"] = {"old-item": 2, "mouse-black": 2, "keyboard-us": 1}
    await read_cart(runtime, condition, state, baseline=False)
    state.update(requirements_source="inferred", evidence_slots=[{"value": "cart nonempty"}])
    session = session_for(state)
    BrowserRuntimeRail._apply_worker_progress_to_task_state(session, {"status": "completed"}, "全部完成，合计100")
    result = session.get_state(PHASE_KEY)
    assert result["status"] == "partial"
    assert any("cart_total_requires_observed_evidence" in blocker for blocker in result["blockers"])


@pytest.mark.asyncio
async def test_decision_cannot_cross_task_with_same_session_page_and_phase_version():
    policy, _, _, runtime, context, captured = setup_policy()
    response = await policy.invoke(await messages_for(policy, context, captured), tools=TOOLS)
    compiled = response.tool_calls[0]
    context.get_session_ref().get_state(PHASE_KEY)["task_id"] = "another-task"
    with pytest.raises(ValueError, match="wrong_task"):
        policy.check_tool_call_binding(
            SimpleNamespace(tool_call=compiled, tool_name=compiled.name, tool_args=compiled.arguments),
            context.get_session_ref(),
        )
    runtime._call_playwright_run_code_unsafe.assert_not_awaited()


@pytest.mark.asyncio
async def test_phase_verification_refreshes_url_and_failed_read_cannot_recertify_cached_capture():
    _, _, _, runtime, context, _ = setup_policy()
    session = context.get_session_ref()
    page = runtime._ensure_page_state()
    current = page.export_decision_observation()
    args = {
        "op": "set",
        "objective": "验证目标页面",
        "allowed_operations": ["navigate"],
        "conditions": [{"kind": "url", "value": current["url"]}],
    }
    tool = BrowserPhaseTool(runtime)
    assert (await tool.invoke(args, session=session)).data["phase"]["status"] == "verified"
    runtime.capture_reconciliation_browser_state = AsyncMock(side_effect=TimeoutError("read timed out"))
    output = await tool.invoke({"op": "verify", "phase_version": 1}, session=session)
    assert not output.success and output.data["executed"] is None
    state = session.get_state(PHASE_KEY)
    assert missing_conditions(state)
    assert observe_conditions(state, current)["status"] == "in_progress"
    runtime.capture_reconciliation_browser_state = AsyncMock(
        return_value={
            "ok": True,
            "decision_observation": {**current, "capture_id": "really-fresh"},
        }
    )
    output = await tool.invoke({"op": "verify", "phase_version": 1}, session=session)
    assert output.success and output.data["phase"]["status"] == "verified"


@pytest.mark.asyncio
async def test_phase_verification_honors_invocation_deadline_before_reading():
    _, _, _, runtime, context, _ = setup_policy()
    state = context.get_session_ref().get_state(PHASE_KEY)
    state["invocation_remaining_s"] = 0
    runtime.capture_reconciliation_browser_state = AsyncMock()
    output = await BrowserPhaseTool(runtime).invoke(
        {"op": "verify", "phase_version": 1},
        session=context.get_session_ref(),
    )
    assert not output.success and output.data["executed"] is False
    assert output.error == "browser_task_deadline"
    runtime.capture_reconciliation_browser_state.assert_not_awaited()
