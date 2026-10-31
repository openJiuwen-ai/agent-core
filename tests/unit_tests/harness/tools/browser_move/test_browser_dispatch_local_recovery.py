# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Frozen executor receipts and shared local recovery; no model or remote website."""

import asyncio
import copy
import json
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from openjiuwen.core.single_agent.rail.base import AgentCallbackContext, ToolCallInputs
from openjiuwen.harness.tools.browser_move.decision.action_space import build_menu, build_request
from openjiuwen.harness.tools.browser_move.decision.guard import NODE_STATE_JS, _validate_script
from openjiuwen.harness.tools.browser_move.decision.policy_model import BrowserPolicyModel
from openjiuwen.harness.tools.browser_move.playwright_runtime import execution_journal as journal
from openjiuwen.harness.tools.browser_move.playwright_runtime.browser_state_context_processor import (
    BrowserStateContextProcessor,
)
from openjiuwen.harness.tools.browser_move.playwright_runtime.phase_contract import (
    binding_targets,
    constrain_actions,
    set_phase,
    unresolved_writes,
)
from openjiuwen.harness.tools.browser_move.playwright_runtime.runtime import BrowserRuntimeRail as Rail
from tests.unit_tests.harness.tools.browser_move.test_browser_decision_observation_handoff import vm_runtime
from tests.unit_tests.harness.tools.browser_move.test_browser_jev_phase_contract import call, control, session_for
from tests.unit_tests.harness.tools.browser_move.test_browser_jev_policy import (
    TOOLS,
    choose_operation,
    messages_for,
    setup_policy,
)
from tests.unit_tests.harness.tools.browser_move.test_browser_local_continuity import execute_stub
from tests.unit_tests.harness.tools.browser_move.test_browser_local_decision_loop import LOCAL_TOOLS
from tests.unit_tests.harness.tools.browser_move.test_browser_reader_stale_recovery import failed_group

FIXTURE = json.loads((Path(__file__).parent / "fixtures/oct09_dispatch_recovery.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("path", ["native", "compact", "primitive"])
def test_original_strict_resolution_is_undispatched_across_executors(path):
    state = Rail._build_phase_state("Read the requested landing page")
    session = session_for(state)
    inputs = call("strict", FIXTURE["strict_tool"] if path == "native" else "browser_batch_interact")
    if path == "native":
        inputs.tool_args = FIXTURE["strict_args"]
        result = {"ok": False, "error": FIXTURE["strict_error"]}
    else:
        inputs.tool_args = {"steps": [{"op": "click", "selector": "textarea#chat-textarea"}]}
        result = execute_stub(inputs.tool_args["steps"], FIXTURE["strict_error"])["result"] if path == "compact" else {
            "steps": [{"index": 0, "ok": False, "executed": None, "error": FIXTURE["strict_error"]}]}
    journal.prepare(session, inputs)
    with journal.execution_scope(session, inputs):
        journal.mark_dispatched()  # Calling an executor is not evidence that click reached the page.
    receipt = journal.record_result(session, inputs, {"success": False}, result)
    assert receipt["execution_state"] == "rejected_before_dispatch"
    assert receipt["executed"] is False and not unresolved_writes(state)


@pytest.mark.parametrize("variant", ["performed", "truncated", "missing_log", "explicit_true", "script", "prose"])
def test_strict_text_does_not_erase_possible_dispatch(variant):
    error = FIXTURE["strict_error"]
    if variant == "performed":
        error += "\n  - performing click action\n  - waiting for scheduled navigations to finish"
    elif variant == "truncated":
        error += "\n  - [log truncated]"
    elif variant == "missing_log":
        error = error.split("Call log:")[0]
    elif variant == "prose":
        error = "Page says: " + error
    state = Rail._build_phase_state("Perform an authorized action")
    session = session_for(state)
    inputs = call("uncertain", "browser_evaluate" if variant == "script" else FIXTURE["strict_tool"])
    inputs.tool_args = FIXTURE["strict_args"]
    journal.prepare(session, inputs)
    result = {"error": error, **({"executed": True} if variant == "explicit_true" else {})}
    receipt = journal.record_result(session, inputs, {"success": False}, result)
    assert receipt["execution_state"] == "dispatched_unknown"
    assert unresolved_writes(state)


@pytest.mark.parametrize("original", FIXTURE["generation_errors"])
def test_frozen_generation_error_triggers_existing_refresh_group(original):
    result = copy.deepcopy(original)
    page = SimpleNamespace(decision_snapshot={"capture_id": "old"}, read_observation={"text": "historical"},
                           card_probe_cache={"old": True}, listing_stale=False)
    runtime = SimpleNamespace(ensure_page_state=lambda: page, clear_post_observation=MagicMock())
    assert Rail._invalidate_rejected_observation(SimpleNamespace(_runtime=runtime), result)
    assert result["observation_error"]["code"] == "browser_observation_stale"
    group, ids, readonly = BrowserStateContextProcessor._completed_state_action_group(failed_group(result))
    assert group and ids == {"denied"} and readonly
    assert page.read_observation["text"] == "historical" and page.read_observation["interaction_revision"] == -1


@pytest.mark.asyncio
async def test_page_state_exception_path_preserves_undispatched_fact_and_refresh(monkeypatch):
    runtime = vm_runtime(monkeypatch)
    page = runtime.ensure_page_state()
    old_generation = page.generation_id
    page.advance(url=page.url)
    with pytest.raises(ValueError, match="browser_observation_stale: generation") as error:
        page.validate_generation(old_generation)
    inputs = ToolCallInputs(tool_call=call("stale").tool_call, tool_name="browser_batch_interact",
                            tool_args={"generation_id": "g1", "steps": [{"op": "click", "target_id": "old"}]})
    ctx = AgentCallbackContext(agent=MagicMock(), session=session_for(Rail._build_phase_state("Continue")),
                               inputs=inputs, exception=error.value)
    rail = Rail(runtime)
    await rail.on_tool_exception(ctx)
    await rail.after_tool_call(ctx)
    result = json.loads(inputs.tool_msg.content)
    assert result["executed"] is False and result["observation_invalidated"]
    assert inputs.tool_msg.metadata["execution_state"] == "rejected_before_dispatch"
    assert not unresolved_writes(ctx.session.get_state("__browser_phase_budget_state__"))


@pytest.mark.asyncio
@pytest.mark.parametrize("recover", [False, True])
async def test_actual_batch_generation_wrapper_joins_shared_invalidation(monkeypatch, recover):
    runtime = vm_runtime(monkeypatch)
    page = runtime.ensure_page_state()
    old_generation = page.generation_id
    page.advance(url=page.url)
    result = await runtime.batch_interact(generation_id=old_generation,
        steps=[{"op": "click", "ref": "e1"}], allow_stale_recovery=recover)
    assert result["executed"] is False
    assert result["reason"].startswith("browser_observation_stale: reference" if recover
                                       else "browser_observation_stale: generation")
    assert Rail._invalidate_rejected_observation(SimpleNamespace(_runtime=runtime), result)


def test_native_ref_preflight_emits_same_stale_code(monkeypatch):
    runtime = vm_runtime(monkeypatch)
    page = runtime.ensure_page_state()
    page.reference_generations["e1"] = page.generation
    page.advance(url=page.url)
    with pytest.raises(ValueError, match="browser_observation_stale: reference"):
        runtime.validate_reference_values(["e1"])


def test_legacy_batch_error_envelope_is_supported_without_string_mining(monkeypatch):
    runtime = vm_runtime(monkeypatch)
    result = {"executed": False, "error": "batch_target_validation_failed: "
              "Stale PageState generation g1; current generation is g2. Probe or snapshot the current page again."}
    assert Rail._invalidate_rejected_observation(SimpleNamespace(_runtime=runtime), result)


def test_stale_partial_batch_keeps_ack_and_requests_observation_repair():
    state = Rail._build_phase_state("Select two current controls")
    session, inputs = session_for(state), call("partial")
    inputs.tool_args = {"steps": [{"op": "click", "target_id": "a"}, {"op": "click", "target_id": "b"}]}
    result = {"steps": [{"index": 0, "ok": True, "executed": True},
                        {"index": 1, "ok": False, "executed": False,
                         "error": "browser_observation_stale: target; Stale target_id t_g1_2 belongs to g1"}]}
    journal.prepare(session, inputs)
    result.update(journal.record_result(session, inputs, {"success": False}, result))
    page = SimpleNamespace(decision_snapshot={}, card_probe_cache={}, read_observation={}, listing_stale=False)
    runtime = SimpleNamespace(ensure_page_state=lambda: page, clear_post_observation=lambda: None)
    assert Rail._invalidate_rejected_observation(SimpleNamespace(_runtime=runtime), result)
    assert result["execution_state"] == "partial" and result["executed"] is True
    assert result["execution"]["steps"][0]["execution_state"] == "acknowledged"
    assert not unresolved_writes(state)


@pytest.mark.parametrize("reason", ["Unknown PageState target_id: missing", "invalid_page_operation_arguments",
    "browser_policy_guard_failed: TimeoutError", "permission denied", "The page says Stale PageState generation g1"])
def test_unrelated_rejection_is_not_page_invalidation(reason):
    rail = SimpleNamespace(_runtime=MagicMock())
    assert not Rail._invalidate_rejected_observation(rail, {"executed": False, "reason": reason})
    rail._runtime.ensure_page_state.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("category", ["url", "target_count", "node_signature", "node_region", "node_fields"])
async def test_guard_reason_survives_dispatch_and_receipt(category):
    policy, _, client, runtime, context, captured = setup_policy()
    choose_operation(client, "CLICK")
    message = await policy.invoke(await messages_for(policy, context, captured), tools=TOOLS)
    call_ = message.tool_calls[0]
    runtime.call_playwright_run_code_unsafe.return_value = {"ok": False, "mismatch": category}
    with pytest.raises(ValueError, match=f"browser_policy_target_changed: browser_policy_stale_node: {category}"):
        await policy.validate_tool_call(SimpleNamespace(tool_call=call_, tool_name=call_.name,
            tool_args=json.loads(call_.arguments)), context.get_session_ref())


@pytest.mark.asyncio
async def test_guard_diagnostic_cannot_leak_arbitrary_executor_fields():
    runtime = SimpleNamespace(call_playwright_run_code_unsafe=AsyncMock(return_value={
        "ok": False, "mismatch": "private field value"}), unwrap_mcp_text_result=lambda x: x)
    with pytest.raises(ValueError) as caught:
        await _validate_script(runtime, "guard")
    assert str(caught.value) == "browser_policy_stale_node"


async def reject_twice(policy, client, runtime, context, captured):
    choose_operation(client, "CLICK")
    for index in range(2):
        page = runtime.ensure_page_state()
        page.decision_snapshot.update(capture_id=f"before-{index}", page_guard={"document": "doc-a"})
        page.read_observation = {"text": f"changing counter {index}", "url": page.url,
                                 "interaction_revision": page.interaction_revision}
        message = await policy.invoke(await messages_for(policy, context, captured), tools=LOCAL_TOOLS)
        call_ = message.tool_calls[0]
        policy.record_execution(SimpleNamespace(tool_call=call_, tool_name=call_.name,
            tool_args=json.loads(call_.arguments)), context.get_session_ref(),
            {"success": False, "executed": False, "observation_invalidated": True,
             "observation_error": {"code": "browser_observation_stale", "reason": "node_fields"}})


@pytest.mark.asyncio
async def test_repeated_guard_drift_hands_off_once_and_keeps_readers_then_recovers():
    policy, llm, client, runtime, context, captured = setup_policy()
    await reject_twice(policy, client, runtime, context, captured)
    page = runtime.ensure_page_state()
    page.decision_snapshot.update(capture_id="third", page_text="new third counter")
    calls_before = client.evaluate.await_count
    response = await policy.invoke(await messages_for(policy, context, captured), tools=LOCAL_TOOLS)
    assert not response.tool_calls and client.evaluate.await_count == calls_before
    assert "repeated_target_invalidation" in llm.invoke.call_args.kwargs["messages"][-1].content
    task = next(iter(policy._tasks.values()))
    assert not task.failed_actions  # Still not a failed business action or unknown write.

    choose_operation(client, "EXTRACT_TEXT")
    page.decision_snapshot.update(capture_id="fourth", page_text="new fourth counter")
    response = await policy.invoke(await messages_for(policy, context, captured), tools=LOCAL_TOOLS)
    assert json.loads(response.tool_calls[0].arguments)["op"] == "read_text"
    assert "CLICK" not in client.evaluate.call_args.args[0]["questions"]["action"]["criteria"]

    policy.record_execution(
        SimpleNamespace(
            tool_call=SimpleNamespace(id="llm-snapshot"),
            tool_name="browser_probe_interactives",
            tool_args={"query": "销量"},
        ),
        context.get_session_ref(),
        {"success": True},
    )
    choose_operation(client, "CLICK")
    page.decision_snapshot.update(capture_id="llm-fresh", page_text="refreshed binding")
    response = await policy.invoke(await messages_for(policy, context, captured), tools=LOCAL_TOOLS)
    assert response.tool_calls and json.loads(response.tool_calls[0].arguments)["steps"][0]["op"] == "click"


@pytest.mark.asyncio
async def test_navigation_feedback_is_advisory_and_distinguishes_changed_outcomes():
    policy, llm, client, runtime, context, captured = setup_policy(goal="Read page two")
    choose_operation(client, "HANDOFF")
    for index in range(3):
        await messages_for(policy, context, captured)
        policy.record_execution(SimpleNamespace(tool_call=SimpleNamespace(id=f"llm-nav-{index}"),
            tool_name="browser_page_action", tool_args={"op": "navigate", "url": "https://example.test/?page=2"}),
            context.get_session_ref(), {"success": True})
        runtime.ensure_page_state().decision_snapshot["capture_id"] = f"capture-{index}"
        await policy.invoke(await messages_for(policy, context, captured), tools=LOCAL_TOOLS)
    recovery = llm.invoke.call_args.kwargs["messages"][-1].content
    assert '"attempts": 3' in recovery and "repeated_navigation" in recovery
    task = next(iter(policy._tasks.values()))
    assert not task.failed_actions
    task.llm_actions[-1]["observed_page_key"] = "new actual outcome"
    assert BrowserPolicyModel._repeated_navigation(task) == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("refresh", ["failed_reader", "unchanged_capture"])
async def test_unsuccessful_llm_recovery_cannot_reopen_repeated_stale_action(refresh):
    policy, _, client, runtime, context, captured = setup_policy()
    await reject_twice(policy, client, runtime, context, captured)
    await policy.invoke(await messages_for(policy, context, captured), tools=LOCAL_TOOLS)
    page = runtime.ensure_page_state()
    policy.record_execution(SimpleNamespace(tool_call=SimpleNamespace(id="llm-snapshot"),
        tool_name="browser_page_action", tool_args={"op": "snapshot"}), context.get_session_ref(),
        {"success": refresh != "failed_reader"})
    if refresh == "failed_reader":
        page.decision_snapshot["capture_id"] = "passive-recapture-after-failure"
    page.read_observation["text"] = "different page decoration"
    choose_operation(client, "HANDOFF")
    await policy.invoke(await messages_for(policy, context, captured), tools=LOCAL_TOOLS)
    assert "CLICK" not in client.evaluate.call_args.args[0]["questions"]["action"]["criteria"]


@pytest.mark.asyncio
async def test_new_local_binding_reopens_action_without_replaying_old_call():
    policy, _, client, runtime, context, captured = setup_policy()
    await reject_twice(policy, client, runtime, context, captured)
    await policy.invoke(await messages_for(policy, context, captured), tools=LOCAL_TOOLS)
    page = runtime.ensure_page_state()
    state = context.get_session_ref().get_state("__browser_phase_budget_state__")
    controls = page.export_decision_targets()
    set_phase(state, {"objective": "Use this current sort control", "target_ids": [controls[0]["target_id"]],
                      "allowed_operations": ["click"]}, controls)
    choose_operation(client, "CLICK")
    response = await policy.invoke(await messages_for(policy, context, captured), tools=LOCAL_TOOLS)
    assert response.tool_calls
    assert response.tool_calls[0].id != next(iter(policy._tasks.values())).receipts[-1]["decision_id"]


@pytest.mark.asyncio
async def test_ack_resets_prior_stale_failures_for_that_object_only():
    policy, _, client, runtime, context, captured = setup_policy()
    choose_operation(client, "CLICK")
    for index, success in enumerate((False, True)):
        page = runtime.ensure_page_state()
        page.read_observation = {"text": f"new view {index}", "url": page.url,
                                 "interaction_revision": page.interaction_revision}
        response = await policy.invoke(await messages_for(policy, context, captured), tools=TOOLS)
        call_ = response.tool_calls[0]
        policy.record_execution(SimpleNamespace(tool_call=call_, tool_name=call_.name,
            tool_args=json.loads(call_.arguments)), context.get_session_ref(),
            {"success": success, "executed": success, "observation_invalidated": not success})
    receipts = next(iter(policy._tasks.values())).receipts
    assert receipts[0]["binding_recovered"] and receipts[1]["success"]


def test_bound_remove_control_excludes_same_object_link_and_retains_source_facts():
    link = control("link", "Selected object", 1)
    link.update(role="link", href="https://example.test/detail/1", region="selected row")
    remove = control("remove", "Remove", 2, nearby_text="Selected object quantity 1")
    controls = [link, remove]
    previews = binding_targets(controls)["targets"]
    assert previews[0]["href"] == link["href"] and previews[1]["nearby_text"] == "Selected object quantity 1"
    menu = build_menu(controls, "Remove selected object", limit=20)
    request = build_request("jev", {}, menu, controls)
    assert link["href"] in request["questions"]["action"]["criteria"]["CLICK"]
    state = {"task_id": "t"}
    set_phase(state, {"objective": "Remove selected object", "target_ids": ["remove"],
                      "allowed_operations": ["click"]}, controls)
    constrain_actions(menu, state, controls)
    assert {step["target_id"] for step in menu.steps.values()} == {"remove"}
    assert sum(step["op"] == "click" for step in menu.steps.values()) == 1


@pytest.mark.parametrize("change,expected", [
    ("none", None), ("url", "url"), ("count", "target_count"), ("document", "node_document"),
    ("region", "node_region"), ("fields", "node_fields"), ("value", "node_value"),
    ("signature", "node_signature"), ("disabled", "target_disabled"),
])
@pytest.mark.asyncio
async def test_generated_guard_reports_real_mismatch_without_relaxing_predicates(change, expected):
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js required for generated guard integration")
    policy, _, client, runtime, context, captured = setup_policy()
    choose_operation(client, "CLICK")
    message = await policy.invoke(await messages_for(policy, context, captured), tools=TOOLS)
    call_ = message.tool_calls[0]
    await policy.validate_tool_call(SimpleNamespace(tool_call=call_, tool_name=call_.name,
        tool_args=json.loads(call_.arguments)), context.get_session_ref())
    script = runtime.call_playwright_run_code_unsafe.call_args.args[0]
    runner = r'''
const input = JSON.parse(require('fs').readFileSync(0,'utf8'));
global.window = {__openjiuwenDecisionNodes:{document:'doc-a',nodes:new WeakMap()}};
global.document = {getElementById:()=>null};
const field = {name:'other',value:'original',checked:false,disabled:false};
const form = {innerText:'A form',querySelectorAll:()=>[field]};
const attrs = {};
const el = {tagName:'INPUT',value:'before',checked:false,labels:[],textContent:'Control',
  getAttribute:(name)=>attrs[name]??null, closest:(s)=>s.startsWith('form') ? form : null,
  matches:()=>input.change==='disabled'};
window.__openjiuwenDecisionNodes.nodes.set(el,1);
const expected = eval('(' + input.nodeState + ')')(el);
if (input.change==='document') window.__openjiuwenDecisionNodes.document='new-document';
if (input.change==='region') form.innerText='A changed form';
if (input.change==='fields') field.value='changed';
if (input.change==='value') el.value='changed';
if (input.change==='signature') attrs['aria-label']='Changed label';
const page = {url:()=>input.change==='url' ? 'https://other.test/' : 'https://example.test/',
  locator:()=>({count:async()=>input.change==='count' ? 2 : 1,
    evaluate:async(fn,args)=>fn(el,{...args,guard:expected})})};
(async()=>console.log(JSON.stringify(await eval('(' + input.script + ')')(page))))();
'''
    result = await asyncio.to_thread(subprocess.run, [node, "-e", runner], input=json.dumps({
        "script": script, "nodeState": NODE_STATE_JS, "change": change,
    }), text=True, capture_output=True, encoding="utf-8", check=True, timeout=10)
    payload = json.loads(result.stdout)
    assert payload == ({"ok": True} if expected is None else {"ok": False, "mismatch": expected})
