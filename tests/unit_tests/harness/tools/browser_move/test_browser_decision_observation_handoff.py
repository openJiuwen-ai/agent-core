# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Real VM boundary, shared observations and useful local decision handoff."""

import copy
import json
import os
import shutil
import subprocess
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openjiuwen.harness.tools.browser_move.decision.action_space import (
    build_menu,
    build_request,
)
from openjiuwen.harness.tools.browser_move.decision.config import BrowserDecisionConfig
from openjiuwen.harness.tools.browser_move.decision.jev_client import (
    DecisionUnavailable,
    validate_action,
)
from openjiuwen.harness.tools.browser_move.decision.policy_model import (
    BrowserPolicyModel,
    _Observation,
)
from openjiuwen.harness.tools.browser_move.playwright_runtime import (
    execution_journal as journal,
)
from openjiuwen.harness.tools.browser_move.playwright_runtime import (
    runtime as runtime_module,
)
from openjiuwen.harness.tools.browser_move.playwright_runtime.phase_contract import (
    set_phase,
)
from openjiuwen.harness.tools.browser_move.playwright_runtime.policy_page_action import (
    BrowserPageActionTool,
)
from tests.unit_tests.harness.tools.browser_move.test_browser_jev_policy import (
    TOOLS,
    choose_operation,
    grouped_answer,
    messages_for,
    setup_policy,
)
from tests.unit_tests.harness.tools.browser_move.test_browser_page_state import (
    _make_bare_runtime,
)

URL = "https://example.test/"
LOCAL_TOOLS = [*TOOLS, {"name": "browser_page_action"}, {"name": "browser_phase"}]
PROBE = """async page => ({ok:true,url:page.url(),elements:[],decision_snapshot:{
  capture_id:'vm-capture',url:page.url(),page_guard:{document:'vm-document'},page_text:'readable facts'}})"""
VM = r"""
const vm = require('node:vm');
const fs = require('node:fs');
const args = JSON.parse(fs.readFileSync(0, 'utf8'));
const waits = [];
const page = {
  url: () => 'https://example.test/',
  title: async () => args.mode === 'timeout' ? await new Promise(() => {}) : 'Fixture',
  evaluate: async () => ({page_position:{},semantic_state:{}}),
  waitForTimeout: ms => {
    waits.push(ms);
    return new Promise(resolve => {
      const timer = setTimeout(resolve, args.mode === 'timeout' ? 10 : ms);
      if (args.mode !== 'timeout') timer.unref();
    });
  },
};
page.context = () => {
  if (args.mode === 'capture_error') throw new TypeError('synthetic metadata failure');
  return {pages: () => [page]};
};
// Match the real MCP VM: Page and completion handle only, no Node timer globals.
const context = vm.createContext({page, __end__:{}});
const globals = vm.runInContext('[typeof setTimeout, typeof clearTimeout]', context);
context.__fn__ = vm.runInContext('(' + args.code + ')', context);
(async () => {
  try {
    const result = await vm.runInContext('__fn__(page)', context);
    process.stdout.write(JSON.stringify({globals, waits, result}));
  } catch (error) {
    process.stdout.write(JSON.stringify({globals, waits, error:String(error)}));
  }
})().catch(error => { process.stderr.write(String(error)); process.exitCode = 1; });
"""


def execute_vm(code, mode="success"):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is required for the actual MCP VM contract")
    result = subprocess.run([node, "-e", VM], input=json.dumps({"code": code, "mode": mode}),
                            capture_output=True, text=True, encoding="utf-8", timeout=8, check=True)
    return json.loads(result.stdout)


def vm_runtime(monkeypatch):
    runtime = _make_bare_runtime()
    runtime.ensure_runtime_ready = AsyncMock()
    runtime._service = SimpleNamespace(allowed_tool_names=None)
    runtime.ensure_page_state().observe(url=URL)
    # Keep the actual metadata builder and wrapper; the DOM probe alone is a local fixture.
    monkeypatch.setattr(runtime_module, "build_interactive_probe_js", lambda **_: PROBE)
    return runtime


@pytest.mark.parametrize("mode,error", [("success", None), ("capture_error", "post_observation_TypeError"),
                                        ("timeout", "post_observation_timeout")])
def test_fixed_read_keeps_ack_and_bounded_observation_in_timer_free_mcp_vm(monkeypatch, mode, error):
    runtime = vm_runtime(monkeypatch)
    code = runtime._fixed_observation_script("async page => ({ok:true,executed:true,url:page.url(),text:'facts'})")
    value = execute_vm(code, mode)
    assert value["globals"] == ["undefined", "undefined"]
    assert value["waits"] == [2000]
    receipt = value["result"]
    assert receipt["ok"] and receipt["executed"] and receipt["text"] == "facts"
    if error:
        assert receipt["_runtime_observation_error"] == error
        assert "_runtime_observation" not in receipt
    else:
        assert receipt["_runtime_observation"]["decision_probe"]["decision_snapshot"]["capture_id"] == "vm-capture"


def test_primary_tool_failure_is_not_swallowed_as_optional_observation(monkeypatch):
    runtime = vm_runtime(monkeypatch)
    value = execute_vm(runtime._fixed_observation_script("async page => {throw new Error('primary failed');}"))
    assert value["error"] == "Error: primary failed" and not value["waits"]


def test_actual_page_and_full_probe_work_in_the_mcp_vm_without_waiting_for_timeout():
    playwright = pytest.importorskip("playwright.sync_api")
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is required for the MCP VM contract")
    runtime = _make_bare_runtime()
    code = runtime._fixed_observation_script("""async page => ({ok:true,executed:true,url:page.url(),
      text:await page.locator('body').innerText()})""")
    runner = r"""
const vm = require('node:vm');
const args = JSON.parse(require('node:fs').readFileSync(0, 'utf8'));
const {chromium} = require(args.package);
(async () => {
  const browser = await chromium.launch({headless:true,executablePath:args.executable || undefined});
  try {
    const page = await browser.newPage();
    await page.setContent('<input type="search" aria-label="Search" value="keyboard"><h1>Readable result</h1>');
    let timerCompleted = false;
    const wait = page.waitForTimeout.bind(page);
    page.waitForTimeout = ms => wait(ms).then(() => {timerCompleted = true;});
    const context = vm.createContext({page,__end__:{}});
    const result = await vm.runInContext('(' + args.code + ')(page)', context);
    process.stdout.write(JSON.stringify({result,timerCompleted,globals:vm.runInContext(
      '[typeof setTimeout,typeof clearTimeout]',context)}));
  } finally { await browser.close(); }
})().catch(error => {process.stderr.write(String(error));process.exitCode=1;});
"""
    package = Path(playwright.__file__).resolve().parents[1] / "driver" / "package"
    completed = subprocess.run([node, "-e", runner], input=json.dumps({
        "code": code, "package": str(package), "executable": os.getenv("BROWSER_TEST_CHROMIUM_PATH"),
    }), capture_output=True, text=True, encoding="utf-8", timeout=30)
    if "Executable doesn't exist" in completed.stderr:
        pytest.skip("Optional isolated Chromium is unavailable")
    assert completed.returncode == 0, completed.stderr
    value = json.loads(completed.stdout)
    assert value["globals"] == ["undefined", "undefined"] and not value["timerCompleted"]
    assert "Readable result" in value["result"]["text"]
    probe = value["result"]["_runtime_observation"]["decision_probe"]
    assert probe["ok"] and probe["decision_snapshot"]["capture_id"]
    assert any(item["decision_state"].get("current_value") == "keyboard" for item in probe["elements"])


@pytest.mark.asyncio
async def test_fixed_read_receipt_and_compact_capture_share_one_vm_rpc(monkeypatch):
    runtime = vm_runtime(monkeypatch)
    runtime.call_playwright_run_code_unsafe = AsyncMock(side_effect=lambda code: execute_vm(code)["result"])
    monkeypatch.setattr(
        "openjiuwen.harness.tools.browser_move.playwright_runtime.policy_page_action.fixed_text_script",
        lambda query="": "async page => ({ok:true,url:page.url(),title:'Fixture',text:'facts'})",
    )
    tool = BrowserPageActionTool(runtime)
    result = await tool.invoke({"op": "read_text", "generation_id": runtime.generation_id})
    assert result.success and result.data["observation_updated"]
    assert runtime.ensure_page_state().read_observation["text"] == "facts"
    captured = await runtime.capture_compact_browser_state(action_group_id="fixed-read")
    assert captured["decision_observation"]["capture_id"] == "vm-capture"
    assert captured["capture_source"] == "post_action_rpc"
    runtime.call_playwright_run_code_unsafe.assert_awaited_once()
    policy, llm, client, _, context, _ = setup_policy()
    policy.runtime = runtime
    choose_operation(client, "EXTRACT_TEXT")
    decision = await policy.invoke(await messages_for(policy, context, captured), tools=LOCAL_TOOLS)
    assert decision.tool_calls and decision.metadata["browser_policy"]["evaluated"]
    llm.invoke.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("fresh,mode,expected", [(True, "hybrid", 0), (False, "llm", 0), (False, "hybrid", 1)])
async def test_only_missing_hybrid_decision_data_gets_one_metadata_repair(monkeypatch, fresh, mode, expected):
    runtime = vm_runtime(monkeypatch)
    runtime.decision_policy = SimpleNamespace(decision_config=BrowserDecisionConfig(mode=mode))
    if fresh:
        runtime.ensure_page_state().decision_snapshot = {"capture_id": "already-present", "url": URL}
    runtime.capture_reconciliation_browser_state = AsyncMock(return_value={
        "ok": True, "decision_observation": {"capture_id": "repaired"},
    })
    captured = await runtime.capture_compact_browser_state(action_group_id="read")
    assert runtime.capture_reconciliation_browser_state.await_count == expected
    if expected:
        assert captured["capture_source"] == "decision_repair_rpc"
        runtime.capture_reconciliation_browser_state.assert_awaited_once_with(
            action_group_id="read", include_decision=True,
        )


@pytest.mark.asyncio
async def test_repair_timeout_preserves_read_and_reports_specific_failure(monkeypatch):
    runtime = vm_runtime(monkeypatch)
    runtime.decision_policy = SimpleNamespace(decision_config=BrowserDecisionConfig(mode="hybrid"))
    page = runtime.ensure_page_state()
    page.read_observation = {"text": "retained facts", "url": URL}
    runtime.capture_reconciliation_browser_state = AsyncMock(side_effect=TimeoutError())
    captured = await runtime.capture_compact_browser_state(action_group_id="read")
    assert captured["decision_observation_error"] == "decision_repair_TimeoutError"
    assert page.read_observation["text"] == "retained facts"
    policy, llm, client, _, context, _ = setup_policy()
    policy.runtime = runtime
    await policy.invoke(await messages_for(policy, context, captured), tools=LOCAL_TOOLS)
    client.evaluate.assert_not_awaited()
    llm.invoke.assert_awaited_once()


def test_capture_failure_is_recorded_without_leaking_private_payload(monkeypatch):
    runtime = vm_runtime(monkeypatch)
    records = []
    monkeypatch.setattr(runtime_module, "browser_agent_log_info", lambda marker, data: records.append(json.loads(data)))
    receipt = {"ok": True, "text": "facts", "_runtime_observation_error": "post_observation_timeout"}
    runtime.retain_post_observation(receipt)
    assert receipt == {"ok": True, "text": "facts"}
    assert records[-1]["error"] == "post_observation_timeout"


@pytest.mark.parametrize("provider", ["typesafe", "openrouter"])
@pytest.mark.parametrize("action,target,head", [(0.50, 0.60, None), (0.49, 0.95, "action"),
                                               (0.95, 0.59, "target"), (0.55, 0.55, "target")])
@pytest.mark.asyncio
async def test_independent_confidence_thresholds_reach_dispatch_or_llm(provider, action, target, head):
    policy, llm, client, _, context, captured = setup_policy()
    policy.decision_config = BrowserDecisionConfig(mode="hybrid", provider=provider,
                                                   action_min_confidence=0.50, target_min_confidence=0.60)

    def response(payload, **_):
        answer = grouped_answer(payload, "CLICK", confidence=target)
        answer["answers"]["action"]["confidence"] = action
        return answer

    client.evaluate.side_effect = response
    result = await policy.invoke(await messages_for(policy, context, captured), tools=LOCAL_TOOLS)
    if head:
        llm.invoke.assert_awaited_once()
        handoff = llm.invoke.call_args.kwargs["messages"][-1].content
        assert f'"uncertain_head": "{head}"' in handoff
        assert '"action_min_confidence": 0.5' in handoff and '"target_min_confidence": 0.6' in handoff
    else:
        assert result.tool_calls[0].name == "browser_batch_interact"
        llm.invoke.assert_not_awaited()


@pytest.mark.parametrize("group", ["FINISH", "HANDOFF"])
def test_finish_and_handoff_require_no_target_head(group):
    questions = {"action": {"criteria": {group: "Delegate"}}}
    answers = {"action": {"type": "choice", "choice": group, "confidence": 0.5, "probabilities": {group: 1}}}
    assert validate_action(answers, questions, 0.5, target_threshold=0.6)[0] == group
    with pytest.raises(DecisionUnavailable, match="uncertain_choice"):
        validate_action(answers, questions, 0.55)


def test_legacy_threshold_inheritance_and_partial_override():
    config = BrowserDecisionConfig(min_confidence=0.73, action_min_confidence=0.5)
    assert config.action_threshold == 0.5 and config.target_threshold == 0.73
    changed = replace(config, min_confidence=0.8)
    assert changed.target_threshold == 0.8
    default = BrowserDecisionConfig()
    assert default.action_threshold == default.target_threshold == default.min_confidence


@pytest.mark.parametrize("field", ["action_min_confidence", "target_min_confidence"])
@pytest.mark.parametrize("value", [True, -0.1, 1.01, float("nan"), float("inf"), "0.5"])
def test_head_thresholds_reject_invalid_configuration(field, value):
    with pytest.raises(ValueError, match=field):
        BrowserDecisionConfig(**{field: value})


def test_action_head_can_read_actual_bound_operation_without_target_question():
    control = {"target_id": "search", "name": "Search catalogue", "role": "searchbox", "enabled": True,
               "actionable": True, "decision_state": {"tag": "input", "search_like": True,
                                                      "node_guard": {"document": "d", "node": 1}}}
    menu = build_menu([control], 'Search "keyboard"', limit=30, page={"url": URL, "page_guard": {"document": "d"}},
                      page_operations={"read_text"})
    payload = build_request("jev-1.13.0", {"current_intent": 'Search "keyboard"'}, menu, [control])
    criteria = payload["questions"]["action"]["criteria"]
    assert "keyboard" in criteria["TYPE_TEXT"] and "Search catalogue" in criteria["TYPE_TEXT"]
    assert "not yet" in criteria["EXTRACT_TEXT"]
    assert "target_TYPE_TEXT" not in criteria["TYPE_TEXT"]
    assert "Assume TYPE_TEXT" in payload["questions"]["target_TYPE_TEXT"]["instructions"]


@pytest.mark.asyncio
async def test_action_uncertainty_requests_local_handoff_and_new_objective_reenters():
    policy, llm, client, runtime, context, captured = setup_policy(goal="Open site, search, compare and read details")
    page = runtime.ensure_page_state()
    client.evaluate.side_effect = lambda payload, **_: grouped_answer(payload, "CLICK", confidence=0.4)
    await policy.invoke(await messages_for(policy, context, captured), tools=LOCAL_TOOLS)
    assert "remaining local fragment" in llm.invoke.call_args.kwargs["messages"][-1].content
    set_phase(context.get_session_ref().get_state(""), {"objective": "点击销量排序"}, page.export_decision_targets())
    choose_operation(client, "CLICK")
    result = await policy.invoke(await messages_for(policy, context, captured), tools=LOCAL_TOOLS)
    assert result.tool_calls and client.evaluate.await_count == 2
    assert client.evaluate.call_args.args[0]["state"]["current_intent"] == "点击销量排序"


@pytest.mark.asyncio
async def test_initial_old_tab_is_not_a_new_popup_but_explicit_objective_can_select_it():
    policy, _, client, runtime, context, captured = setup_policy()
    page = runtime.ensure_page_state()
    page.decision_snapshot["page_guard"] = {"document": "d"}
    old = {"index": 0, "url": "https://old.test/", "title": "Previous query"}
    captured["tabs"] = [old, {"index": 1, "url": page.url, "current": True}]
    choose_operation(client, "EXTRACT_TEXT")
    await policy.invoke(await messages_for(policy, context, captured), tools=LOCAL_TOOLS)
    assert "SELECT_TAB" not in client.evaluate.call_args.args[0]["questions"]["action"]["criteria"]
    captured["tabs"].append({"index": 2, "url": "https://popup.test/", "title": "New result"})
    choose_operation(client, "SELECT_TAB")
    result = await policy.invoke(await messages_for(policy, context, captured), tools=LOCAL_TOOLS)
    assert json.loads(result.tool_calls[0].arguments)["index"] == 2
    set_phase(context.get_session_ref().get_state(""), {"objective": "Return to https://old.test/"}, [])
    await policy.invoke(await messages_for(policy, context, captured), tools=LOCAL_TOOLS)
    options = client.evaluate.call_args.args[0]["questions"]["target_SELECT_TAB"]["criteria"]
    assert any("https://old.test/" in item["action"] for item in options.values())


def test_reader_key_ignores_unrelated_controls_and_content_but_reopens_on_changed_scope_or_binding():
    obs = _Observation("task", "session", 9999999999, {}, [], {
        "url": URL, "interaction_revision": 20, "page_guard": {"document": "d"},
        "cards": [{"title": "Item", "price": "$10"}],
    })
    step = {"op": "probe_cards", "max_cards": 12, "viewport_only": False}
    original = BrowserPolicyModel._action_key(step, {}, obs)
    obs.controls.append({"name": "Unrelated footer", "role": "link"})
    obs.state.update(llm_recent_actions=[{"op": "recall"}], page_position={"pixels_above": 200})
    obs.page.update(cards_observed=False, interaction_revision=21)
    assert BrowserPolicyModel._action_key(step, {}, obs) == original
    for change in ("interaction", "query", "document", "binding"):
        changed = copy.deepcopy(obs)
        changed_step = dict(step)
        if change == "interaction":
            changed.page["reader_interaction"] = "ack-sort:0"
        elif change == "query":
            changed_step["query"] = "another range"
        elif change == "document":
            changed.page["page_guard"]["document"] = "new-document"
        else:
            changed.state["phase"] = {"version": 2}
        assert BrowserPolicyModel._action_key(changed_step, {}, changed) != original


@pytest.mark.asyncio
async def test_successful_cards_retire_without_a_second_sample_but_new_interaction_reopens():
    policy, llm, client, runtime, context, captured = setup_policy(goal="Read current result cards")
    page = runtime.ensure_page_state()
    page.decision_snapshot["page_guard"] = {"document": "d"}
    tools = [*LOCAL_TOOLS, {"name": "browser_probe_cards"}]
    choose_operation(client, "PROBE_CARDS")
    result = await policy.invoke(await messages_for(policy, context, captured), tools=tools)
    policy.record_execution(SimpleNamespace(tool_call=result.tool_calls[0]),
                            context.get_session_ref(), {"success": True})
    page.register_cards({"cards": [{"title": "Item", "price": "$10"}]})
    page.decision_snapshot["capture_id"] = "after-read"
    await messages_for(policy, context, captured)
    page.cards_observed_revision = -1  # An unrelated projection does not create new reader work.
    page.get_target(page.export_decision_targets()[0]["target_id"]).name = "Other control"
    choose_operation(client, "EXTRACT_TEXT")
    await policy.invoke(await messages_for(policy, context, captured), tools=tools)
    assert "PROBE_CARDS" not in client.evaluate.call_args.args[0]["questions"]["action"]["criteria"]
    page.mark_interaction()
    target = page.export_decision_targets()[0]["target_id"]
    inputs = SimpleNamespace(tool_name="browser_batch_interact", tool_call=SimpleNamespace(id="llm-sort"),
                             tool_args={"steps": [{"op": "click", "target_id": target}]})
    journal.prepare(context.get_session_ref(), inputs, runtime)
    journal.record_result(context.get_session_ref(), inputs, {"success": True}, {"ok": True})
    choose_operation(client, "PROBE_CARDS")
    result = await policy.invoke(await messages_for(policy, context, captured), tools=tools)
    assert result.metadata["browser_policy"]["operation"] == "probe_cards"
    llm.invoke.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("new_query", [False, True])
async def test_acknowledged_search_on_same_url_is_retired_but_changed_query_remains(new_query):
    policy, _, client, runtime, context, captured = setup_policy(goal='Search "keyboard"')
    page = runtime.ensure_page_state()
    page.decision_snapshot["page_guard"] = {"document": "d"}
    control = page.export_decision_targets()[0]
    target = page.get_target(control["target_id"])
    target.decision_state.update(search_like=True, search_query={"value": "keyboard"})
    inputs = SimpleNamespace(tool_name="browser_batch_interact", tool_call=SimpleNamespace(id="llm-search"),
                             tool_args={"steps": [{"op": "click", "target_id": control["target_id"]}]})
    journal.prepare(context.get_session_ref(), inputs, runtime)
    journal.record_result(context.get_session_ref(), inputs, {"success": True}, {"ok": True})
    if new_query:
        target.decision_state["search_query"] = {"value": "mouse"}
    choose_operation(client, "EXTRACT_TEXT")
    await policy.invoke(await messages_for(policy, context, captured), tools=LOCAL_TOOLS)
    assert ("CLICK" in client.evaluate.call_args.args[0]["questions"]["action"]["criteria"]) is new_query
