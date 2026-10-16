# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Generic regressions derived from October 7 receipts, not live-site success claims."""

import copy
import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from openjiuwen.harness.tools.browser_move.decision.intent import explicit_urls
from openjiuwen.harness.tools.browser_move.playwright_runtime import cart_verification as cart
from openjiuwen.harness.tools.browser_move.playwright_runtime import execution_journal as journal
from openjiuwen.harness.tools.browser_move.playwright_runtime.phase_contract import (
    PHASE_KEY,
    set_phase,
    unresolved_writes,
)
from openjiuwen.harness.tools.browser_move.playwright_runtime.runtime import BrowserRuntimeRail as Rail
from tests.unit_tests.harness.tools.browser_move.test_browser_jev_phase_contract import (
    call,
    cart_runtime,
    cart_state,
    session_for,
)
from tests.unit_tests.harness.tools.browser_move.test_browser_jev_policy import (
    TOOLS,
    choose_operation,
    messages_for,
    setup_policy,
)
from tests.unit_tests.harness.tools.browser_move.test_browser_lean_guards import NEVER_PERFORMED, PERFORMED


@pytest.mark.parametrize("source", ["jev", "llm"])
@pytest.mark.parametrize(
    "tool,args",
    [
        ("browser_page_action", {"op": "find", "text": "price"}),
        ("mcp_playwright-official_browser_find", {"text": "price"}),
        ("browser_page_action", {"op": "find", "text": "早餐"}),
        ("mcp_playwright-official_browser_find", {"text": "早餐"}),
        ("browser_batch_interact", {"steps": [{"op": "click", "target_id": "expand"}]}),
        (
            "browser_batch_interact",
            {"steps": [{"op": "click", "target_id": "expand"}], "description": "open room price details"},
        ),
    ],
)
def test_phase_classifier_cannot_veto_equivalent_tools_or_descriptions(source, tool, args):
    state = Rail._build_phase_state("填写表单并读取结果")
    state.update(current_phase="form", replan_required=True, replan_trial_pending=True, replan_count=4)
    state["decision_policy"] = {"mode": "hybrid" if source == "jev" else "llm"}
    for phase in state["phases"].values():
        phase["attempts"] = phase["budget"]
    session = session_for(state)
    before = copy.deepcopy(state["phases"])
    for _ in range(4):
        Rail._consume_phase_budget(session, tool, args)
    assert sum(p["attempts"] for p in state["phases"].values()) == sum(p["attempts"] for p in before.values()) + 4
    assert state["status"] == "in_progress" and not state.get("action_budget_exhausted")
    assert state["replan_denial_count"] == 0


@pytest.mark.parametrize("source", ["jev", "llm"])
@pytest.mark.parametrize("native", [False, True])
@pytest.mark.parametrize(
    "error,executed,expected",
    [
        (NEVER_PERFORMED, None, "rejected_before_dispatch"),
        (PERFORMED, None, "dispatched_unknown"),
        (NEVER_PERFORMED, True, "dispatched_unknown"),
        ("Timeout 5000ms exceeded: waiting for response", None, "dispatched_unknown"),
        (NEVER_PERFORMED + "\n...", None, "dispatched_unknown"),
    ],
)
def test_receipt_truth_is_shared_and_only_proven_unperformed_actions_release_lock(
    source, native, error, executed, expected
):
    state = Rail._build_phase_state("提交所选对象")
    session = session_for(state)
    inputs = call(source + "_attempt", "mcp_playwright-official_browser_click" if native else "browser_batch_interact")
    inputs.tool_args = (
        {"element": "Submit", "ref": "e3"} if native else {"steps": [{"op": "click", "target_id": "save"}]}
    )
    journal.prepare(session, inputs)
    with journal.execution_scope(session, inputs):
        journal.mark_dispatched()
    result = (
        {"error": error, "executed": executed}
        if native
        else {"steps": [{"index": 0, "ok": False, "error": error, "executed": executed}]}
    )
    receipt = journal.record_result(session, inputs, {"success": False}, result)
    assert receipt["execution_state"] == expected
    # Same facts must govern either model's next action, including another tool entrance.
    other = "llm" if source == "jev" else "jev"
    if expected == "dispatched_unknown":
        with pytest.raises(ValueError, match="requires_reconciliation"):
            journal.prepare(session, call(other + "_retry", "mcp_playwright_browser_evaluate"))
        journal.prepare(session, call(other + "_read", "browser_snapshot"))
        assert unresolved_writes(state)
    else:
        assert not unresolved_writes(state)
        journal.prepare(session, call(other + "_retry"))


def test_phase_rebinding_drops_proof_debt_but_never_execution_uncertainty():
    state, initial = cart_state()
    state["execution_journal"] = [
        {"call_id": "uncertain", "impact": "business", "execution_state": "dispatched_unknown"}
    ]
    deadline = state["deadline_at"]
    goal = state["goal"]
    for selector in ("input.quantity", ".quantity input", "input[value]"):
        spec = {**initial["spec"], "quantity_selector": selector}
        set_phase(state, {"objective": "Read the current quantities", "conditions": [spec]}, [])
        assert len(state["phase_requirements"]) == 1
    set_phase(state, {"objective": "Inspect the current page"}, [])
    assert state["phase_requirements"] == []
    assert state["goal"] == goal and state["deadline_at"] == deadline
    assert unresolved_writes(state)
    assert Rail._business_missing_requirements(state) == ["unknown_browser_write"]


def test_resume_retires_an_obsolete_phase_limit_but_keeps_deadline_and_unknown_effect():
    state, _ = cart_state()
    state.update(status="partial", blockers=["extraction_phase_budget_exhausted"],
                 structured_evidence=[{"source": "https://example.test/", "raw_text": "Available page facts"}])
    state["execution_journal"] = [{
        "call_id": "uncertain", "impact": "business", "execution_state": "dispatched_unknown",
    }]
    deadline = state["deadline_at"]
    session = session_for(state)
    Rail(MagicMock())._resume_task_state(session, state, "Continue from the current page")
    assert state["resume_count"] == 1 and state["deadline_at"] == deadline
    assert state["status"] == "in_progress" and state["blockers"] == []
    assert unresolved_writes(state)
    with pytest.raises(ValueError, match="requires_reconciliation"):
        journal.prepare(session, call("llm_retry"))


@pytest.mark.asyncio
async def test_successful_action_does_not_create_a_cart_proof_lock_even_with_an_optional_reader():
    state, condition = cart_state()
    runtime = cart_runtime({"old-item": 2, "mouse-black": 1})
    await cart.read_cart(runtime, condition, state, baseline=True)
    session = session_for(state)
    action = call("llm_add")
    journal.prepare(session, action, runtime, effect_adapter=cart.prepare_effects)
    journal.record_result(session, action, {"success": True}, {"ok": True})
    cart.record_effects(state, "llm_add")
    assert not unresolved_writes(state)
    assert condition["status"] == "unknown"  # Never promote tool success to quantity proof.
    journal.prepare(session, call("jev_next"), runtime, effect_adapter=cart.prepare_effects)


@pytest.mark.asyncio
async def test_unrelated_llm_read_does_not_erase_jev_failed_action_suppression():
    policy, llm, client, runtime, context, captured = setup_policy()
    session = context.get_session_ref()
    choose_operation(client, "CLICK")
    for _ in range(2):
        selected = (await policy.invoke(await messages_for(policy, context, captured), tools=TOOLS)).tool_calls[0]
        policy.record_execution(SimpleNamespace(tool_call=selected), session, {"success": False, "executed": False})
    failures = copy.deepcopy(session.get_state(PHASE_KEY)["decision_policy"]["failed_actions"])
    policy.record_execution(
        SimpleNamespace(tool_call=SimpleNamespace(id="llm-read"), tool_name="browser_snapshot", tool_args={}),
        session,
        {"success": True},
    )
    runtime.ensure_page_state().decision_snapshot["capture_id"] = "unrelated-read"
    runtime.ensure_page_state().read_observation = {"text": "Different footer; the same button is still unavailable"}
    choose_operation(client, "HANDOFF")
    result = await policy.invoke(await messages_for(policy, context, captured), tools=TOOLS)
    assert session.get_state(PHASE_KEY)["decision_policy"]["failed_actions"] == failures
    assert result.metadata["browser_policy"]["excluded"]["failed_target"] == 1
    assert client.evaluate.await_count == 2  # No supported action remains in this minimal tool set.
    llm.invoke.assert_awaited()


@pytest.mark.asyncio
async def test_successful_llm_recovery_reopens_only_the_matching_action():
    policy, llm, client, runtime, context, captured = setup_policy()
    session = context.get_session_ref()
    choose_operation(client, "CLICK")
    for _ in range(2):
        selected = (await policy.invoke(await messages_for(policy, context, captured), tools=TOOLS)).tool_calls[0]
        policy.record_execution(SimpleNamespace(tool_call=selected), session, {"success": False, "executed": False})
    task = policy._bind_task(session, session.get_state(PHASE_KEY))
    task.failed_actions["another-target"] = 2
    args = json.loads(selected.arguments)
    policy.record_execution(
        SimpleNamespace(tool_call=SimpleNamespace(id="llm-recovered"), tool_name=selected.name, tool_args=args),
        session,
        {"success": True},
    )
    assert task.failed_actions == {"another-target": 2}
    recovered = await policy.invoke(await messages_for(policy, context, captured), tools=TOOLS)
    assert recovered.metadata["browser_policy"]["operation"] == "click"
    assert client.evaluate.await_count == 3


@pytest.mark.parametrize(
    "task",
    [
        "打开 `https://example.test/item?id=1` 再读取",
        "Open [this page](https://example.test/item?id=1).",
        'Visit "https://example.test/item?id=1"',
    ],
)
def test_markdown_url_identity_is_shared_with_the_runtime(task):
    assert explicit_urls(task) == ["https://example.test/item?id=1"]
    assert Rail._build_phase_state(task)["known_urls"] == explicit_urls(task)


@pytest.mark.asyncio
async def test_current_local_intent_does_not_reintroduce_an_already_visited_task_url():
    url = "https://example.test/item?id=1"
    policy, llm, client, runtime, context, captured = setup_policy(goal=f"Open `{url}` then inspect the selected item")
    session = context.get_session_ref()
    task = policy._bind_task(session, session.get_state(PHASE_KEY))
    task.visited_urls.append(url)
    set_phase(
        session.get_state(PHASE_KEY),
        {"objective": f"Inspect `{url}` from the current cart"},
        runtime.ensure_page_state().export_decision_targets(),
    )
    choose_operation(client, "HANDOFF")
    await policy.invoke(await messages_for(policy, context, captured), tools=TOOLS)
    assert "NAVIGATE" not in client.evaluate.call_args.args[0]["questions"]["action"]["criteria"]
