# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""October 8 regressions at executor/journal, shared observation and handoff boundaries."""

import copy
import json
import os
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from openjiuwen.core.foundation.llm.schema.message import ToolMessage
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext, ToolCallInputs
from openjiuwen.harness.tools.browser_move.controllers.action import _build_batch_interact_script
from openjiuwen.harness.tools.browser_move.decision.action_space import build_menu
from openjiuwen.harness.tools.browser_move.decision.policy_model import BrowserPolicyModel, _Observation
from openjiuwen.harness.tools.browser_move.playwright_runtime import execution_journal as journal
from openjiuwen.harness.tools.browser_move.playwright_runtime.phase_contract import unresolved_writes
from openjiuwen.harness.tools.browser_move.playwright_runtime.runtime import BrowserRuntimeRail as Rail
from tests.unit_tests.harness.tools.browser_move.test_browser_jev_phase_contract import call, session_for
from tests.unit_tests.harness.tools.browser_move.test_browser_jev_policy import (
    TOOLS,
    choose_operation,
    messages_for,
    setup_policy,
)
from tests.unit_tests.harness.tools.browser_move.test_browser_lean_guards import NEVER_PERFORMED, PERFORMED
from tests.unit_tests.harness.tools.browser_move.test_browser_probe_cards import _make_runtime

ERRORS = json.loads((Path(__file__).parent / "fixtures/oct08_unperformed_clicks.json").read_text(encoding="utf-8"))


def execute_stub(steps, error, *, failure="click"):
    """Execute the production generated RPC, including its real receipt producer."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js required for generated executor integration")
    code = _build_batch_interact_script({"steps": steps, "timeout_ms": 250, "generation_id": "g1"})
    runner = r"""
const input = JSON.parse(require('fs').readFileSync(0, 'utf8'));
const attempts = [];
const fail = (op) => { attempts.push(op); if (input.failure === op) throw new Error(input.error); };
const target = { first() {return this}, count: async () => 1,
  waitFor: async () => fail('preflight'), isVisible: async () => true, isEnabled: async () => true,
  click: async () => fail('click'), fill: async () => fail('fill'), press: async () => fail('press'),
  selectOption: async () => fail('select_option'), setChecked: async () => fail('set_checked') };
const page = { url: () => 'https://example.test/item/1', title: async () => 'Item',
  locator: () => target, context: () => ({pages: () => [page]}),
  keyboard: {press: async () => fail('keyboard'), type: async () => fail('type')},
  waitForTimeout: async () => {} };
(async () => {const result = await eval('(' + input.code + ')')(page);
  console.log(JSON.stringify({result, attempts}));})();
"""
    result = subprocess.run([node, "-e", runner], input=json.dumps({
        "code": code, "error": error, "failure": failure,
    }), capture_output=True, text=True, encoding="utf-8", check=True, timeout=10)
    return json.loads(result.stdout)


@pytest.mark.parametrize("fixture", ERRORS, ids=lambda row: row["trace"])
@pytest.mark.parametrize("source", ["llm", "jev"])
def test_original_unperformed_errors_roundtrip_through_executor_and_shared_journal(fixture, source):
    args = {"steps": [{"op": "click", "selector": "#submit"}]}
    if fixture["mode"] == "compact_rpc":
        generated = execute_stub(args["steps"], fixture["error"])
        result = generated["result"]
        assert generated["attempts"].count("click") == 1
        assert result["steps"][0]["executed"] is None  # Invocation is not an ACK.
        assert result["executed"] is None
    else:
        result = {"steps": [{"index": 0, "ok": False, "executed": None, "error": fixture["error"]}]}
    state = Rail._build_phase_state("Submit the selected object")
    session = session_for(state)
    inputs = call(source + "_click")
    inputs.tool_args = args
    journal.prepare(session, inputs)
    with journal.execution_scope(session, inputs):
        journal.mark_dispatched()
    receipt = journal.record_result(session, inputs, {"success": False}, result)
    assert receipt["execution_state"] == "rejected_before_dispatch"
    assert receipt["executed"] is False
    assert not unresolved_writes(state)
    journal.prepare(session, call("llm_diagnostic", "browser_evaluate"))  # T8/T16 false lock is gone.


@pytest.mark.parametrize("error", [PERFORMED, NEVER_PERFORMED + "\n\x1b[2m  - ...\x1b[22m",
                                  "intercepted, not stable: transport disconnected"])
def test_uncertain_click_is_not_replayed_or_relabelled_as_unperformed(error):
    result = execute_stub([{"op": "click", "selector": "#submit"}], error)
    assert result["attempts"].count("click") == 1
    state = Rail._build_phase_state("Submit object")
    session = session_for(state)
    inputs = call()
    inputs.tool_args = {"steps": [{"op": "click", "selector": "#submit"}]}
    journal.prepare(session, inputs)
    receipt = journal.record_result(session, inputs, {"success": False}, result["result"])
    assert receipt["execution_state"] == "dispatched_unknown"
    assert unresolved_writes(state)


def test_partial_typing_keeps_acknowledged_prefix_even_if_final_error_says_waiting():
    steps = [{"op": "type", "selector": "#input", "value": "new value"}]
    result = execute_stub(steps, NEVER_PERFORMED, failure="type")["result"]
    assert result["steps"][0]["executed"] is True  # click/clear already happened
    state = Rail._build_phase_state("Edit object")
    session = session_for(state)
    inputs = call()
    inputs.tool_args = {"steps": steps}
    journal.prepare(session, inputs)
    assert journal.record_result(session, inputs, {"success": False}, result)["execution_state"] == "dispatched_unknown"


def test_preflight_failure_remains_definitely_not_dispatched():
    value = execute_stub([{"op": "click", "selector": "#submit"}], "target missing", failure="preflight")
    assert "click" not in value["attempts"]
    assert value["result"]["executed"] is False


@pytest.mark.parametrize("prefix_fill", [False, True])
def test_real_playwright_overlay_timeout_keeps_unperformed_click_and_partial_prefix(prefix_fill):
    playwright = pytest.importorskip("playwright.sync_api")
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js needed for generated RPC")
    steps = ([{"op": "fill", "selector": "#input", "value": "new value"}] if prefix_fill else [])
    steps.append({"op": "click", "selector": "#button"})
    runner = r"""
const input = JSON.parse(require('fs').readFileSync(0, 'utf8'));
const {chromium} = require(input.package);
(async () => {
 const browser = await chromium.launch({headless:true, executablePath:input.executable || undefined});
 try {
  const page = await browser.newPage();
  await page.setContent(`<input id="input"><button id="button" style="position:absolute;left:0;top:100px"
    onclick="window.clicks=(window.clicks||0)+1">Submit</button>
    <div style="position:absolute;left:0;top:90px;width:300px;height:200px;z-index:99">Overlay</div>`);
  const result = await eval('(' + input.code + ')')(page);
  console.log(JSON.stringify({result, clicks:await page.evaluate(() => window.clicks || 0),
    value:await page.locator('#input').inputValue()}));
 } finally {await browser.close()}
})().catch(error => {console.error(error);process.exitCode=1});
"""
    output = subprocess.run([node, "-e", runner], input=json.dumps({
        "package": str(Path(playwright.__file__).resolve().parents[1] / "driver/package"),
        "executable": os.getenv("BROWSER_TEST_CHROMIUM_PATH"),
        "code": _build_batch_interact_script({"steps": steps, "timeout_ms": 300, "generation_id": "g1"}),
    }), capture_output=True, encoding="utf-8", text=True, check=False, timeout=30)
    if "Executable doesn't exist" in output.stderr:
        pytest.skip("Local Chromium unavailable")
    assert output.returncode == 0, output.stderr
    data = json.loads(output.stdout)
    assert data["clicks"] == 0
    assert data["value"] == ("new value" if prefix_fill else "")
    state = Rail._build_phase_state("Submit object")
    session = session_for(state)
    inputs = call()
    inputs.tool_args = {"steps": steps}
    journal.prepare(session, inputs)
    receipt = journal.record_result(session, inputs, {"success": False}, data["result"])
    assert receipt["execution_state"] == ("partial" if prefix_fill else "rejected_before_dispatch")
    assert state["execution_journal"][-1]["steps"][-1]["executed"] is False
    assert not unresolved_writes(state)


@pytest.mark.asyncio
async def test_recall_lifecycle_preserves_current_page_revision_and_cards():
    runtime = _make_runtime()
    runtime.enrich_action_capabilities = AsyncMock()
    page = runtime.ensure_page_state()
    page.observe(url="https://example.test/current")
    page.register_cards({"cards": [{"title": "Current result", "price": "$10"}]})
    before = copy.deepcopy(page.export())
    session = session_for(Rail._build_phase_state("Read current results"))
    inputs = ToolCallInputs(tool_name="browser_recall_offload", tool_args={"handle": "saved"},
                            tool_msg=ToolMessage(tool_call_id="recall", content="saved text"))
    ctx = AgentCallbackContext(agent=MagicMock(), session=session, inputs=inputs)
    rail = Rail(runtime)
    await rail._prepare_tool_call(ctx)
    assert page.export() == before
    inputs.tool_result = {"ok": True, "content": "- Page URL: https://example.test/old\n- heading Old"}
    await rail.after_tool_call(ctx)
    assert page.export() == before
    assert inputs.tool_msg.metadata["state_changed"] is False


@pytest.mark.parametrize("name,args", [
    ("browser_recall_offload", {"handle": "saved"}),
    ("mcp_playwright-official_browser_recall_offload", {"handle": "saved"}),
    ("browser_page_action", {"op": "read_text"}),
    ("browser_page_action", {"op": "find", "query": "price"}),
])
def test_fixed_reads_and_offload_are_not_page_changes(name, args):
    assert not journal.is_write(name, args)
    assert Rail._is_read_only_recovery(name, args)
    assert not Rail._result_may_have_changed_browser_state(name, {"ok": True, "state_changed": False},
                                                         {"success": True}, args)


def test_script_claim_cannot_lower_the_mutation_boundary():
    args = {"function": "() => document.body.innerText", "read_only": True}
    assert journal.is_write("browser_evaluate", args)
    assert Rail._result_may_have_changed_browser_state("browser_evaluate", {"state_changed": False},
                                                      {"success": True}, args)


@pytest.mark.asyncio
async def test_card_reader_reuses_one_scope_and_invalidates_on_mutation_range_navigation_and_age():
    runtime = _make_runtime()
    runtime.ensure_runtime_ready = AsyncMock()
    page = runtime.ensure_page_state()
    page.observe(url="https://example.test/search?q=keyboard")
    runtime._code_executor = AsyncMock(return_value={"ok": True, "url": page.url, "cards": []})
    first = await runtime.probe_cards(max_cards=12, viewport_only=False)
    first.pop("_raw_observation")  # Tool rails consume this field; must not mutate the cache.
    second = await runtime.probe_cards(max_cards=12, viewport_only=False)
    assert second["diagnostics"]["observation_reused"] and "_raw_observation" in second
    assert not second["state_changed"]
    assert runtime._code_executor.await_count == 1
    await runtime.probe_cards(max_cards=12, viewport_only=False, query="other")
    assert runtime._code_executor.await_count == 2
    page.mark_interaction()
    await runtime.probe_cards(max_cards=12, viewport_only=False, query="other")
    assert runtime._code_executor.await_count == 3
    page.card_probe_cache["at"] -= 6
    await runtime.probe_cards(max_cards=12, viewport_only=False, query="other")
    assert runtime._code_executor.await_count == 4
    page.advance(url=page.url)
    await runtime.probe_cards(max_cards=12, viewport_only=False, query="other")
    assert runtime._code_executor.await_count == 5


def observation(*, cards=None, state=None):
    return _Observation("task", "session", 9999999999, state or {}, [],
                        {"url": "https://example.test/search?q=keyboard", "cards": cards or []})


def test_reader_freshness_and_history_cannot_manufacture_progress():
    cards = [{"title": "Keyboard", "primary_link": "https://example.test/item/1", "price": "$10",
              "result_index": 1, "target_id": "old"}]
    first = observation(cards=cards, state={"llm_recent_actions": [{"op": "recall"}],
                                          "read_observation": "old metadata"})
    repeated = copy.deepcopy(first)
    repeated.page.update(cards_observed=True, interaction_revision=19)
    repeated.page["cards"][0]["target_id"] = "new"
    repeated.state.update(llm_recent_actions=[{"op": "recall"}] * 6, read_observation="new metadata",
                          runtime_progress={"status": "in_progress"})
    assert BrowserPolicyModel._fingerprint(first, effect=True) == BrowserPolicyModel._fingerprint(repeated, effect=True)
    assert BrowserPolicyModel._fingerprint(first) != BrowserPolicyModel._fingerprint(repeated)
    repeated.page["cards"][0]["price"] = "$9"
    assert BrowserPolicyModel._fingerprint(first, effect=True) != BrowserPolicyModel._fingerprint(repeated, effect=True)


def test_changed_observation_readmits_reader_without_read_history_resetting_it():
    first = observation(cards=[{"title": "Keyboard", "price": "$10"}])
    step = {"op": "probe_cards", "max_cards": 12}
    key = BrowserPolicyModel._action_key(step, {}, first)
    first.state["llm_recent_actions"] = [{"op": "recall"}]
    first.page["interaction_revision"] = 20
    assert BrowserPolicyModel._action_key(step, {}, first) == key
    first.page["cards"][0]["price"] = "$9"
    assert BrowserPolicyModel._action_key(step, {}, first) != key


@pytest.mark.asyncio
async def test_stale_read_content_is_not_shown_as_fresh_or_counted_again_as_progress():
    policy, _, _, runtime, context, captured = setup_policy()
    page = runtime.ensure_page_state()
    page.read_observation = {"text": "Previously read details", "url": page.url,
                             "interaction_revision": page.interaction_revision}
    await messages_for(policy, context, captured)
    initial = list(policy._observations.values())[-1]
    page.mark_interaction()
    await messages_for(policy, context, captured)
    stale = list(policy._observations.values())[-1]
    assert stale.state["page_text"] == page.decision_snapshot["page_text"]
    assert stale.state["read_content"] == initial.state["read_content"]
    page.read_observation["interaction_revision"] = page.interaction_revision
    await messages_for(policy, context, captured)
    repeated = list(policy._observations.values())[-1]
    assert repeated.state["page_text"] == "Previously read details"
    assert policy._fingerprint(stale, effect=True) == policy._fingerprint(repeated, effect=True)


@pytest.mark.asyncio
async def test_repeated_cards_are_not_new_progress_after_a_local_invalidation():
    policy, _, client, runtime, context, captured = setup_policy(goal="搜索 keyboard 并读取结果")
    page = runtime.ensure_page_state()
    page.decision_snapshot["page_guard"] = {"document": "doc-a", "history_length": 1}
    cards = {"cards": [{"title": "Keyboard", "price": "$10", "primary_link": "https://example.test/item/1"}]}
    page.register_cards(copy.deepcopy(cards))
    page.mark_interaction()  # e.g. a conservative arbitrary-script invalidation
    choose_operation(client, "PROBE_CARDS")
    result = await policy.invoke(await messages_for(policy, context, captured), tools=[{"name": "browser_probe_cards"}])
    action = result.tool_calls[0]
    policy.record_execution(SimpleNamespace(tool_call=action), context.get_session_ref(), {"success": True})
    page.register_cards(copy.deepcopy(cards))
    page.decision_snapshot["capture_id"] = "next-capture"
    await messages_for(policy, context, captured)
    task = next(iter(policy._tasks.values()))
    assert task.receipts[-1]["postcondition"] == "no_observable_progress"


def search_control(target, *, button=False, value="keyboard"):
    return {"target_id": target, "name": "Search", "role": "button" if button else "searchbox",
            "enabled": True, "actionable": True, "clickable": True,
            "decision_state": {"tag": "button" if button else "input", "search_like": True,
                               "current_value": value, "search_query": {"value": value},
                               "node_guard": {"document": "doc", "node": target}}}


@pytest.mark.parametrize("query,expected", [("keyboard", False), ("mouse", True)])
def test_matching_search_submit_is_retired_but_new_literal_is_allowed(query, expected):
    controls = [search_control("input", value=query), search_control("submit", button=True, value=query)]
    menu = build_menu(controls, f'搜索 "{query}"，读取结果', limit=20,
                      page={"url": "https://example.test/search?q=keyboard&sort=sales"})
    assert any(s["op"] == "press" for s in menu.steps.values()) is expected
    assert any(s["op"] == "click" for s in menu.steps.values()) is expected


def test_search_button_without_bound_field_is_not_inferred_from_its_label():
    control = search_control("submit", button=True)
    control["decision_state"].update(search_like=False)
    menu = build_menu([control], "Search", limit=20, page={"url": "https://example.test/?q=keyboard"})
    assert any(s["op"] == "click" for s in menu.steps.values())


def test_visited_tab_is_recovery_but_new_popup_and_explicit_local_destination_remain_available():
    page = {"url": "https://example.test/results", "page_guard": {"document": "doc"},
            "visited_urls": ["https://example.test/", "https://example.test/results"],
            "tabs": [{"index": 0, "url": "https://example.test/"},
                     {"index": 1, "url": "https://example.test/results", "current": True},
                     {"index": 2, "url": "https://example.test/detail"}]}
    menu = build_menu([], "Open https://example.test/ and search", limit=20, page=page,
                      page_operations={"select_tab", "read_text"})
    assert [s["index"] for s in menu.steps.values() if s["op"] == "select_tab"] == [2]
    assert any(s["op"] == "read_text" for s in menu.steps.values())
    page["local_objective"] = True
    menu = build_menu([], "Return to https://example.test/", limit=20, page=page,
                      page_operations={"select_tab"})
    assert [s["index"] for s in menu.steps.values()] == [0, 2]


@pytest.mark.asyncio
async def test_llm_selected_result_tab_is_retained_in_the_next_jev_window_without_a_phase_call():
    policy, llm, client, runtime, context, captured = setup_policy(
        goal='Open https://example.test/ and search "keyboard"'
    )
    page = runtime.ensure_page_state()
    page.decision_snapshot["page_guard"] = {"document": "doc-a", "history_length": 1}
    await messages_for(policy, context, captured)  # Homepage already visited in this task.
    page.observe(url="https://example.test/search?q=keyboard")
    page.decision_snapshot.update(url=page.url, capture_id="results")
    captured.update(url=page.url, tabs=[{"index": 0, "url": "https://example.test/"},
                                      {"index": 1, "url": page.url, "current": True}])
    inputs = call("llm_tab", "browser_tabs")
    inputs.tool_args = {"action": "select", "index": 1}
    policy.record_execution(inputs, context.get_session_ref(), {"success": True})
    choose_operation(client, "PROBE_CARDS")
    result = await policy.invoke(await messages_for(policy, context, captured),
                                tools=TOOLS + [{"name": "browser_page_action"}, {"name": "browser_probe_cards"}])
    assert result.tool_calls[0].name == "browser_probe_cards"
    payload = client.evaluate.call_args.args[0]
    assert "SELECT_TAB" not in payload["questions"]["action"]["criteria"]
    assert payload["state"]["intent_source"] == "task"
    assert payload["state"]["local_context"]["observed_queries"] == ["keyboard"]
    assert payload["state"]["local_context"]["url"] == page.url
    llm.invoke.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("source", ["llm", "jev"])
async def test_acknowledged_object_action_is_not_repeated_by_jev_but_read_and_handoff_remain(source):
    policy, llm, client, runtime, context, captured = setup_policy(goal="Add the selected item")
    page = runtime.ensure_page_state()
    page.decision_snapshot["page_guard"] = {"document": "doc-a", "history_length": 1}
    state = context.get_session_ref().get_state("")
    control = page.export_decision_targets()[0]
    inputs = call(source + "_earlier")
    inputs.tool_args = {"steps": [{"op": "click", "target_id": control["target_id"]}]}
    journal.prepare(context.get_session_ref(), inputs, runtime)
    journal.record_result(context.get_session_ref(), inputs, {"success": True}, {"ok": True})
    # Retain exact object URL while changing document/node, as in the product -> cart -> product trace.
    old = copy.deepcopy(control)
    page.get_target(control["target_id"]).decision_state["node_guard"].update(document="new-doc", node=99)
    choose_operation(client, "EXTRACT_TEXT")
    result = await policy.invoke(await messages_for(policy, context, captured),
                                tools=TOOLS + [{"name": "browser_page_action"}])
    assert result.tool_calls[0].name == "browser_page_action"
    assert json.loads(result.tool_calls[0].arguments)["op"] == "read_text"
    payload = client.evaluate.call_args.args[0]
    assert "CLICK" not in payload["questions"]["action"]["criteria"]
    assert "HANDOFF" in payload["questions"]["action"]["criteria"]
    assert payload["state"]["local_context"]["acknowledged_actions"][0]["effect"] == "tool_ack_only"
    llm.invoke.assert_not_awaited()
    obs = observation()
    obs.page["url"] = page.url
    obs.controls.append(old)
    obs.phase_state.update(execution_journal=state["execution_journal"])
    assert policy._acknowledged_local_action({"op": "click"}, old, obs)
    obs.page["url"] = "https://example.test/different-item"
    assert not policy._acknowledged_local_action({"op": "click"}, old, obs)
    obs.page["url"] = page.url
    obs.phase_state["active_phase_contract"] = {"version": 1}
    assert not policy._acknowledged_local_action({"op": "click"}, old, obs)


def test_distinct_object_id_or_observed_new_binding_is_not_an_acknowledged_repeat():
    policy, _, _, runtime, context, _ = setup_policy()
    control = runtime.ensure_page_state().export_decision_targets()[0]
    prior = copy.deepcopy(control)
    prior["decision_state"]["effect"] = {"identities": {"sku": "first"}}
    control["decision_state"]["effect"] = {"identities": {"sku": "second"}}
    obs = observation()
    obs.page["url"] = runtime.ensure_page_state().url
    obs.controls.append(control)
    entry = {"source": obs.page["url"], "steps": [{"op": "click", "control": prior,
                                                "execution_state": "acknowledged"}]}
    obs.phase_state["execution_journal"] = [entry]
    assert not policy._acknowledged_local_action({"op": "click"}, control, obs)
    control["decision_state"]["effect"] = prior["decision_state"]["effect"]
    entry["steps"].append({"op": "fill", "execution_state": "acknowledged"})
    assert not policy._acknowledged_local_action({"op": "click"}, control, obs)
