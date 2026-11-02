# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Regress U2/U3/U5/U16 receipts without replay, guessed binding or business gates."""

import asyncio
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
from openjiuwen.harness.tools.browser_move.controllers.action import (
    _build_batch_interact_script,
    validate_batch_steps,
)
from openjiuwen.harness.tools.browser_move.playwright_runtime import execution_journal as journal
from openjiuwen.harness.tools.browser_move.playwright_runtime.phase_contract import (
    constrain_actions,
    set_phase,
    unresolved_writes,
)
from openjiuwen.harness.tools.browser_move.playwright_runtime.runtime import BrowserAgentRuntime, BrowserRuntimeRail
from openjiuwen.harness.tools.browser_move.playwright_runtime.runtime_tools import BrowserBatchInteractTool
from tests.unit_tests.harness.tools.browser_move.test_browser_jev_phase_contract import control, session_for
from tests.unit_tests.harness.tools.browser_move.test_browser_jev_policy import (
    TOOLS,
    choose_operation,
    messages_for,
    setup_policy,
)
from tests.unit_tests.harness.tools.browser_move.test_browser_page_state import _make_bare_runtime


def batch_call(steps):
    return SimpleNamespace(tool_name="browser_batch_interact", tool_args={"steps": steps},
                           tool_call=SimpleNamespace(id="llm-batch"))


@pytest.mark.parametrize("optional", [False, True])
@pytest.mark.parametrize("wait_op", ["wait_for_url", "wait_for_text"])
def test_failed_wait_keeps_ack_and_failure_without_unknown_write_or_invented_feedback(optional, wait_op):
    state = BrowserRuntimeRail._build_phase_state("Click selected control, inspect feedback")
    session = session_for(state)
    call = batch_call([{"op": "click", "target_id": "t_g2_713"},
                       {"op": wait_op, "text": "Added", "optional": optional}, {"op": "extract_text"}])
    journal.prepare(session, call)
    result = {"ok": False, "status": "partial", "error": "optional_step_failed" if optional else "condition_timeout",
              "conditions": [{"ok": False, "elapsed_ms": 8000}], "steps": [
                  {"index": 0, "op": "click", "ok": True, "executed": True},
                  {"index": 1, "op": wait_op, "ok": False, "executed": True, "error": "condition_timeout"},
              ]}
    if optional:
        result["steps"].append({"index": 2, "op": "extract_text", "ok": True, "executed": True})
    receipt = journal.record_result(session, call, {"success": False}, result)
    assert receipt["execution_state"] == "partial" and receipt["executed"] is True
    steps = state["execution_journal"][-1]["steps"]
    assert [step["execution_state"] for step in steps] == [
        "acknowledged", "partial", "acknowledged" if optional else "not_started",
    ]
    assert steps[1]["error"] == "condition_timeout" and "observed_feedback" not in steps[1]
    assert not result["conditions"][0]["ok"] and not unresolved_writes(state)


@pytest.mark.parametrize("impact", ["unknown", "business", "local_ui"])
def test_unknown_mutation_is_not_reclassified_as_failed_wait(impact):
    entry = {"impact": impact, "steps": [
        {"execution_state": "dispatched_unknown", "executed": None, "impact": impact},
        {"execution_state": "dispatched_unknown", "executed": True, "impact": "read"},
    ]}
    journal._aggregate(entry)
    assert entry["execution_state"] == "dispatched_unknown" and entry["executed"] is None
    assert entry["steps"][1]["execution_state"] == "partial"


def test_standalone_failed_read_is_partial_but_ack_wait_certifies_feedback():
    call = SimpleNamespace(tool_name="browser_probe_interactives", tool_args={})
    receipt = journal.record_result(None, call, {"success": False}, {"ok": False, "executed": True})
    assert receipt == {"executed": True, "execution_state": "partial"}
    state = BrowserRuntimeRail._build_phase_state("Inspect feedback")
    session = session_for(state)
    call = batch_call([{"op": "click"}, {"op": "wait_for_text", "text": "Added"}])
    journal.prepare(session, call)
    journal.record_result(session, call, {"success": True}, {"ok": True})
    assert state["execution_journal"][-1]["steps"][1]["observed_feedback"] == "Added"


@pytest.mark.asyncio
async def test_navigation_only_target_preserves_capability_and_popup_intent():
    runtime = _make_bare_runtime()
    page = runtime.ensure_page_state()
    page.register_cards({"url": "https://search.test/", "cards": [{
        "title": "清华大学", "primary_link": "https://www.tsinghua.edu.cn/", "actionable": False,
    }]})
    target_id = page.export()["cards"][0]["target_id"]
    original = [{"op": "click", "target_id": target_id}, {"op": "wait_for_tab", "optional": True}]
    assert not validate_batch_steps(original)
    step = copy.deepcopy(original[0])
    with pytest.raises(ValueError, match="browser_target_navigation_only.*tsinghua.edu.cn"):
        await runtime._resolve_batch_target(step, generation_id=page.generation_id)
    assert step == original[0]
    runtime.ensure_runtime_ready = AsyncMock()
    result = await runtime.batch_interact(steps=original, generation_id=page.generation_id,
                                         allow_stale_recovery=False)
    assert not result["executed"] and "navigation_only" in result["error"]
    assert "requires a target" not in result["error"]
    assert result["page_state"]["cards"][0]["href"] == "https://www.tsinghua.edu.cn/"
    await runtime._resolve_batch_target(step, generation_id=page.generation_id, allow_navigation_rewrite=True)
    assert step["_navigate_url"] == "https://www.tsinghua.edu.cn/"


@pytest.mark.parametrize("observed,expected_mismatch", [
    ({"url": "https://search.test/results", "generation_id": "g2", "page_id": "current"}, False),
    ({"url": "https://foreign.test/"}, True),
    ({"url": "https://search.test/results", "generation_id": "g1"}, True),
    ({"url": "https://search.test/results", "page_id": "other-tab"}, True),
])
def test_probe_uses_current_binding_not_stale_phase_url(observed, expected_mismatch):
    state = {"last_page": {"url": "https://search.test/"}}
    current = {"url": "https://search.test/results", "generation_id": "g2", "page_id": "current"}
    mismatch = BrowserRuntimeRail._probe_source_mismatch(state, observed, "browser_probe_cards", current)
    assert mismatch == expected_mismatch
    assert BrowserRuntimeRail._probe_source_mismatch(state, {"url": "https://foreign.test/"}, "browser_probe_cards")


@pytest.mark.asyncio
@pytest.mark.parametrize("foreign", [False, True])
async def test_probe_body_metadata_policy_and_evidence_agree(foreign):
    runtime = MagicMock(spec=BrowserAgentRuntime)
    runtime.export_page_state.return_value = {"url": "https://search.test/results", "generation_id": "g2"}
    runtime.decision_policy = SimpleNamespace(record_execution=MagicMock())
    state = BrowserRuntimeRail._build_phase_state("Read the result title")
    state["last_page"] = {"url": "https://search.test/"}
    source = "https://foreign.test/" if foreign else "https://search.test/results"
    original = {"ok": True, "url": source, "generation_id": "g2", "cards": [{"title": "Observed title"}]}
    inputs = ToolCallInputs(tool_name="browser_probe_cards", tool_args={}, tool_result=copy.deepcopy(original),
                           tool_msg=ToolMessage(tool_call_id="probe", content=json.dumps(original)))
    ctx = AgentCallbackContext(agent=MagicMock(), session=session_for(state), inputs=inputs)
    await BrowserRuntimeRail(runtime).after_tool_call(ctx)
    body = json.loads(inputs.tool_msg.content)
    assert body["ok"] is (not foreign) and inputs.tool_msg.metadata["success"] is (not foreign)
    assert body["execution_state"] == inputs.tool_msg.metadata["execution_state"]
    recorded = runtime.decision_policy.record_execution.call_args.args[2]
    assert recorded["success"] is (not foreign)
    if foreign:
        assert body["error"] == "browser_observation_source_mismatch"
        assert body["raw_observation"]["cards"] == original["cards"]
        assert not state["evidence_slots"]
        assert state["last_page"]["url"] != source
        runtime.record_tool_reference_state.assert_not_called()
    else:
        runtime.record_tool_reference_state.assert_called_once()


def test_common_wait_schema_is_smaller_while_controller_keeps_legacy_validation():
    tool = BrowserBatchInteractTool(_make_bare_runtime())
    schema = tool.card.input_params
    ops = schema["properties"]["steps"]["items"]["properties"]["op"]["enum"]
    for op in ("wait_for_first_card_title", "wait_for_dom_text_change", "wait_for_result_count", "wait_for_stable"):
        assert op not in ops
    assert {"wait_for_selector", "wait_for_text", "wait_for_url", "wait_for_sort_state", "wait_for_tab"} <= set(ops)
    assert validate_batch_steps([{"op": "wait_for_dom_text_change"}])
    assert not validate_batch_steps([{"op": "wait_for_dom_text_change", "selector": "#known", "previous_text": "old"}])


@pytest.mark.asyncio
async def test_normal_read_only_handoff_removes_clicks_before_confident_jev_decision():
    policy, llm, client, runtime, context, captured = setup_policy(goal="Read the chosen object's details")
    page = runtime.ensure_page_state()
    page.decision_snapshot["page_guard"] = {"document": "doc-a"}
    state = context.get_session_ref().get_state("")
    set_phase(state, {"objective": "Read remaining details of the chosen object",
                      "allowed_operations": ["read_text", "find", "snapshot"]}, page.export_decision_targets())
    choose_operation(client, "EXTRACT_TEXT")
    response = await policy.invoke(await messages_for(policy, context, captured),
                                   tools=[*TOOLS, {"name": "browser_page_action"}])
    assert json.loads(response.tool_calls[0].arguments)["op"] == "read_text"
    assert "CLICK" not in client.evaluate.call_args.args[0]["questions"]["action"]["criteria"]
    llm.invoke.assert_not_awaited()
    assert state.get("status") != "completed"


def test_normal_selected_object_scope_excludes_unrelated_link_without_domain_rules():
    state = {"goal": "Read selected object"}
    controls = [control("selected", "Chosen object", 1), control("unrelated", "Another object", 2)]
    set_phase(state, {"objective": "Open the selected object", "target_ids": ["selected"],
                      "allowed_operations": ["click"]}, controls)
    menu = SimpleNamespace(steps={item["target_id"]: {"op": "click", "target_id": item["target_id"]}
                                  for item in controls}, criteria={}, excluded={})
    constrain_actions(menu, state, controls)
    assert list(menu.steps) == ["selected"]


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [None, "capture failed"])
async def test_failed_read_refresh_is_once_bounded_and_never_replays_ack(error):
    runtime = _make_bare_runtime()
    runtime.ensure_page_state().observe(url="https://form.test/")
    probe = {"ok": True, "url": "https://form.test/", "elements": [],
             "decision_snapshot": {"capture_id": "fresh", "url": "https://form.test/"}}
    metadata = {"ok": True, "url": "https://form.test/", "decision_probe": probe}
    runtime.capture_browser_metadata = AsyncMock(return_value=(metadata, error))
    result = {"ok": False, "status": "partial", "steps": [
        {"op": "fill", "ok": True, "executed": True},
        {"op": "extract_value", "ok": False, "executed": False, "error": "matched 0"},
    ]}
    original = copy.deepcopy(result)
    await runtime._refresh_failed_batch_read(result)
    assert {key: result[key] for key in original} == original
    assert result["observation_updated"] is (error is None)
    assert runtime._has_post_observation() is (error is None)
    runtime.capture_browser_metadata.assert_awaited_once_with(include_decision=True)
    runtime.capture_browser_metadata.reset_mock()
    await runtime._refresh_failed_batch_read({"steps": [original["steps"][1]]})
    runtime.capture_browser_metadata.assert_not_awaited()


@pytest.mark.asyncio
async def test_batch_returns_refreshed_page_state_without_second_controller_call():
    runtime = _make_bare_runtime()
    runtime.ensure_runtime_ready = AsyncMock()
    runtime._code_executor = None
    page = runtime.ensure_page_state()
    element = {"role": "textbox", "text": "Guest", "selector_hint": "#old",
               "selector_hint_validated": True, "match_count": 1, "visible": True,
               "enabled": True, "actionable": True}
    page.register_interactives({"url": "https://form.test/", "elements": [element]})
    target_id = element["target_id"]
    runtime._controller = MagicMock()
    runtime._controller.run_action = AsyncMock(return_value={
        "ok": False, "status": "partial", "_runtime_page": {"url": page.url},
        "steps": [{"op": "fill", "ok": True, "executed": True},
                  {"op": "extract_value", "ok": False, "executed": False, "error": "matched 0"}],
    })
    fresh = {**element, "selector_hint": "#fresh"}
    fresh.pop("target_id", None)
    metadata = {"ok": True, "url": page.url, "decision_probe": {
        "ok": True, "url": page.url, "elements": [fresh],
        "decision_snapshot": {"capture_id": "fresh-read", "url": page.url},
    }}
    runtime.capture_browser_metadata = AsyncMock(return_value=(metadata, None))
    result = await runtime.batch_interact(generation_id=page.generation_id, allow_stale_recovery=False,
                                         steps=[{"op": "fill", "target_id": target_id, "value": "Guest name"},
                                                {"op": "extract_value", "target_id": target_id, "field": "guest"}])
    runtime._controller.run_action.assert_awaited_once()
    assert result["status"] == "partial" and result["steps"][0]["ok"]
    assert result["observation_updated"] and runtime._has_post_observation()
    assert result["page_state"] == page.export()
    assert fresh["target_id"] in {item["target_id"] for item in result["page_state"]["interactives"]}


@pytest.mark.asyncio
async def test_refresh_capture_error_keeps_partial_ack():
    runtime = _make_bare_runtime()
    runtime.capture_browser_metadata = AsyncMock(side_effect=TimeoutError("capture deadline"))
    result = {"ok": False, "status": "partial", "steps": [
        {"op": "click", "ok": True, "executed": True},
        {"op": "extract_value", "ok": False, "executed": False},
    ]}
    await runtime._refresh_failed_batch_read(result)
    assert result["steps"][0]["ok"] and result["status"] == "partial"
    assert not result["observation_updated"]
    assert "TimeoutError" in result["observation_error"]


def test_real_rerender_keeps_fill_ack_and_returns_fresh_read_binding():
    playwright = pytest.importorskip("playwright.sync_api")
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is required for the Playwright Page executor")
    runtime = _make_bare_runtime()
    captured_code = []
    runtime.call_playwright_run_code_unsafe = AsyncMock(side_effect=lambda code: captured_code.append(code) or {})
    asyncio.run(runtime.capture_browser_metadata(include_decision=True))
    steps = [{"op": "fill", "selector": "#old", "value": "Guest name"},
             {"op": "click", "selector": "#rerender"},
             {"op": "extract_value", "selector": "#old", "field": "guest"}]
    runner = r"""
const args = JSON.parse(require('fs').readFileSync(0,'utf8'));
const {chromium} = require(args.package);
(async () => {
 const browser = await chromium.launch({headless:true, executablePath:args.executable || undefined});
 try {
  const page = await browser.newPage();
  await page.setContent(`<input id="old" aria-label="Guest"><button id="rerender"
    onclick="window.clicks=(window.clicks||0)+1; const old=document.querySelector('#old');
      const fresh=old.cloneNode(); fresh.id='fresh'; old.replaceWith(fresh)">Update form</button>`);
  const result = await eval('('+args.batch+')')(page);
  const metadata = await eval('('+args.metadata+')')(page);
  const fresh = metadata.decision_probe.elements.find(item => item.role==='textbox');
  if (!fresh) throw Error('Fresh input absent from actual probe');
  const read = await eval('('+args.read.replace('FRESH_SELECTOR', fresh.selector_hint)+')')(page);
  console.log(JSON.stringify({result,metadata,read,clicks:await page.evaluate(() => window.clicks)}));
 } finally {await browser.close()}
})().catch(error => {console.error(error);process.exitCode=1});
"""
    output = subprocess.run([node, "-e", runner], input=json.dumps({
        "package": str(Path(playwright.__file__).resolve().parents[1] / "driver/package"),
        "executable": os.getenv("BROWSER_TEST_CHROMIUM_PATH"),
        "batch": _build_batch_interact_script({"steps": steps, "timeout_ms": 300, "generation_id": "g0"}),
        "metadata": captured_code[0],
        "read": _build_batch_interact_script({"steps": [{"op": "extract_value", "selector": "FRESH_SELECTOR",
                                                         "field": "guest"}], "generation_id": "g0"}),
    }), capture_output=True, encoding="utf-8", text=True, check=False, timeout=30)
    if "Executable doesn't exist" in output.stderr:
        pytest.skip("Local Chromium unavailable")
    assert output.returncode == 0, output.stderr
    data = json.loads(output.stdout)
    result = data["result"]
    assert [step["ok"] for step in result["steps"]] == [True, True, False]
    assert result["steps"][2]["executed"] is False and data["clicks"] == 1
    assert data["read"]["extracted"]["guest"] == "Guest name"
    runtime.capture_browser_metadata = AsyncMock(return_value=(data["metadata"], None))
    asyncio.run(runtime._refresh_failed_batch_read(result))
    assert result["observation_updated"]
    target = next(item for item in runtime.ensure_page_state().export_decision_targets() if item["role"] == "textbox")
    read_step = {"op": "extract_value", "target_id": target["target_id"]}
    asyncio.run(runtime._resolve_batch_target(read_step, generation_id=runtime.generation_id))
    assert read_step["selector"] == "#fresh"
    state = BrowserRuntimeRail._build_phase_state("Fill and read guest name")
    session = session_for(state)
    call = batch_call(steps)
    journal.prepare(session, call)
    receipt = journal.record_result(session, call, {"success": False}, result)
    assert receipt["execution_state"] == "partial" and receipt["executed"] is True
    assert not unresolved_writes(state)
