# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Regress real reader-loop/stale-handoff failure shapes without a live provider."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from openjiuwen.core.context_engine import ContextWindow
from openjiuwen.core.foundation.llm import AssistantMessage, ToolCall, ToolMessage, UserMessage
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext, ToolCallInputs
from openjiuwen.harness.tools.browser_move.controllers.action import validate_batch_steps
from openjiuwen.harness.tools.browser_move.decision.config import BrowserDecisionConfig
from openjiuwen.harness.tools.browser_move.decision.guard import _validate_script
from openjiuwen.harness.tools.browser_move.decision.policy_model import BrowserPolicyModel, _Observation
from openjiuwen.harness.tools.browser_move.playwright_runtime.browser_state_context_processor import (
    BrowserStateContextProcessor,
    BrowserStateContextProcessorConfig,
)
from openjiuwen.harness.tools.browser_move.playwright_runtime.phase_contract import (
    PhaseInputError,
    set_phase,
    validate_request,
)
from openjiuwen.harness.tools.browser_move.playwright_runtime.probe_semantics import normalize_card_probe_payload
from openjiuwen.harness.tools.browser_move.playwright_runtime.probes import (
    build_card_probe_js,
    build_interactive_probe_js,
)
from openjiuwen.harness.tools.browser_move.playwright_runtime.runtime import BrowserRuntimeRail
from tests.unit_tests.harness.tools.browser_move.test_browser_decision_observation_handoff import vm_runtime
from tests.unit_tests.harness.tools.browser_move.test_browser_jev_policy import (
    choose_operation,
    grouped_answer,
    messages_for,
    setup_policy,
)
from tests.unit_tests.harness.tools.browser_move.test_browser_local_decision_loop import LOCAL_TOOLS
from tests.unit_tests.harness.tools.browser_move.test_browser_receipts_local_recovery import session_for
from tests.unit_tests.harness.tools.browser_move.test_browser_september17_contracts import dom_page as shared_dom_page

dom_page = shared_dom_page
PHASE = "__browser_phase_budget_state__"


@pytest.mark.asyncio
async def test_changing_body_cannot_reopen_successful_same_scope_reader():
    policy, llm, client, runtime, context, captured = setup_policy(goal="Search, select and add a suitable item")
    policy.decision_config = BrowserDecisionConfig(mode="hybrid", max_decisions=80)
    page = runtime.ensure_page_state()
    page.decision_snapshot["page_guard"] = {"document": "doc-a"}
    client.evaluate.side_effect = lambda payload, **_: grouped_answer(payload,
        "EXTRACT_TEXT" if "EXTRACT_TEXT" in payload["questions"]["action"]["criteria"] else "HANDOFF")
    result = await policy.invoke(await messages_for(policy, context, captured), tools=LOCAL_TOOLS)
    policy.record_execution(SimpleNamespace(tool_call=result.tool_calls[0]),
                            context.get_session_ref(), {"success": True})
    for index in range(39):
        # Any independently changing text, not a site-specific countdown filter.
        page.read_observation = {"operation": "read_text", "text": f"Live counter {index}. Same catalog and search.",
                                 "url": page.url, "interaction_revision": page.interaction_revision}
        page.decision_snapshot["capture_id"] = f"capture-{index}"
        response = await policy.invoke(await messages_for(policy, context, captured), tools=LOCAL_TOOLS)
        assert not response.tool_calls
        assert "EXTRACT_TEXT" not in client.evaluate.call_args.args[0]["questions"]["action"]["criteria"]
    assert len(next(iter(policy._tasks.values())).receipts) == 1
    assert llm.invoke.await_count == 39
    page.mark_interaction()  # Only an ACKed concrete interaction reopens automatic sampling.
    context.get_session_ref().get_state("")["execution_journal"] = [{"call_id": "clicked", "source": page.url,
        "steps": [{"op": "click", "execution_state": "acknowledged"}]}]
    response = await policy.invoke(await messages_for(policy, context, captured), tools=LOCAL_TOOLS)
    assert json.loads(response.tool_calls[0].arguments)["op"] == "read_text"


@pytest.mark.asyncio
@pytest.mark.parametrize("op,args", [("read_text", {}), ("probe_cards", {"max_cards": 12, "viewport_only": False,
                                                                        "include_buttons": False})])
async def test_llm_reader_receipt_also_retires_same_scope_jev_read(op, args):
    policy, _, client, runtime, context, captured = setup_policy(goal="Read current object")
    page = runtime.ensure_page_state()
    page.decision_snapshot["page_guard"] = {"document": "doc-a"}
    await messages_for(policy, context, captured)  # Establish the shared intent.
    policy.record_execution(SimpleNamespace(tool_call=SimpleNamespace(id="llm-reader"),
        tool_name="browser_page_action" if op == "read_text" else "browser_probe_cards",
        tool_args={"op": op, **args} if op == "read_text" else args), context.get_session_ref(), {"success": True})
    choose_operation(client, "HANDOFF")
    await policy.invoke(await messages_for(policy, context, captured),
                        tools=[*LOCAL_TOOLS, {"name": "browser_probe_cards"}])
    group = "EXTRACT_TEXT" if op == "read_text" else "PROBE_CARDS"
    assert group not in client.evaluate.call_args.args[0]["questions"]["action"]["criteria"]
    assert next(iter(policy._tasks.values())).receipts[-1]["model_source"] == "llm"


@pytest.mark.parametrize("change,changed", [
    ("text", False), ("controls", False), ("capture", False), ("revision", False),
    ("document", True), ("intent", True), ("binding", True), ("query", True), ("selection", True),
])
def test_reader_retry_key_depends_on_scope_not_content(change, changed):
    obs = _Observation("t", "s", 9999999999, {"current_intent": "Read price", "read_content": "a"}, [],
        {"page_id": "p", "url": "https://example.test/", "generation_id": "g1", "interaction_revision": 3,
         "page_guard": {"document": "d"}, "reader_view": {"semantic": {"selected_filters": []}}})
    step = {"op": "find", "query": "price"}
    before = BrowserPolicyModel._action_key(step, {}, obs)
    if change == "text":
        obs.state["read_content"] = "b"
    elif change == "controls":
        obs.controls.append({"name": "unrelated"})
    elif change == "capture":
        obs.state["capture_id"] = "next"
    elif change == "revision":
        obs.page["interaction_revision"] += 1
    elif change == "document":
        obs.page["page_guard"]["document"] = "e"
    elif change == "intent":
        obs.state["current_intent"] = "Read remaining price"
    elif change == "binding":
        obs.state["phase"] = {"version": 2}
    elif change == "query":
        step["query"] = "stock"
    else:
        obs.page["reader_view"]["semantic"]["selected_filters"] = ["new"]
    assert (BrowserPolicyModel._action_key(step, {}, obs) != before) is changed


def failed_group(result, call_id="denied"):
    return [AssistantMessage(content="", tool_calls=[ToolCall(id=call_id, type="function",
        name="browser_page_action", arguments='{"op":"read_text","generation_id":"g1"}')]),
        ToolMessage(tool_call_id=call_id, content=json.dumps(result),
                    metadata={"executed": False, "state_changed": False, "success": False})]


@pytest.mark.asyncio
@pytest.mark.parametrize("source", ["jev", "llm"])
async def test_invalid_dispatch_basis_does_not_retire_the_button(source):
    policy, _, client, runtime, context, captured = setup_policy()
    choose_operation(client, "CLICK")
    result = await policy.invoke(await messages_for(policy, context, captured), tools=LOCAL_TOOLS)
    call = result.tool_calls[0]
    if source == "llm":
        call.id = "llm-stale"
    inputs = SimpleNamespace(tool_call=call, tool_name=call.name, tool_args=json.loads(call.arguments))
    policy.record_execution(inputs, context.get_session_ref(),
        {"success": False, "executed": False, "observation_invalidated": True})
    task = next(iter(policy._tasks.values()))
    assert not task.failed_actions
    assert not any(policy._failed_targets.values())


@pytest.mark.asyncio
@pytest.mark.parametrize("reason,invalidated", [
    ("browser_policy_target_changed: browser_policy_stale_node; refresh", True),
    ("browser_policy_stale_page", True), ("browser_policy_stale_target", True),
    ("browser_policy_guard_failed: TimeoutError", False),
    ("browser_policy_guard_failed: browser_policy_arguments_changed", False),
    ("locator.click: Timeout waiting for visible", False), ("permission denied", False),
])
async def test_denial_truth_and_observation_invalidation_are_independent(monkeypatch, reason, invalidated):
    runtime = vm_runtime(monkeypatch)
    page = runtime.ensure_page_state()
    page.decision_snapshot = {"capture_id": "old", "url": page.url}
    page.read_observation = {"text": "previous raw facts", "interaction_revision": page.interaction_revision}
    runtime._post_observation = {"old": True}
    original = {"ok": False, "executed": False, "state_changed": False,
                "error": "page_action_rejected", "reason": reason}
    inputs = ToolCallInputs(tool_name="browser_page_action", tool_args={"op": "read_text"},
        tool_result=original, tool_msg=ToolMessage(tool_call_id="denied", content=json.dumps(original)))
    ctx = AgentCallbackContext(agent=MagicMock(), session=session_for(BrowserRuntimeRail._build_phase_state("Read")),
                               inputs=inputs)
    await BrowserRuntimeRail(runtime).after_tool_call(ctx)
    result = json.loads(inputs.tool_msg.content)
    assert result["executed"] is False and inputs.tool_msg.metadata["state_changed"] is False
    assert bool(result.get("observation_invalidated")) is invalidated
    group, ids, read_only = BrowserStateContextProcessor._completed_state_action_group(failed_group(result))
    assert bool(group) is invalidated
    if invalidated:
        assert ids == {"denied"} and read_only
        assert not page.decision_snapshot.get("capture_id") and runtime._post_observation is None
        assert page.read_observation["text"] == "previous raw facts"
    else:
        assert page.decision_snapshot["capture_id"] == "old"


@pytest.mark.asyncio
@pytest.mark.parametrize("repair_fails", [False, True])
async def test_stale_group_repairs_once_and_never_reuses_old_snapshot(monkeypatch, repair_fails):
    runtime = vm_runtime(monkeypatch)
    page = runtime.ensure_page_state()
    page.decision_snapshot = {"invalidated": "browser_policy_stale_node"}
    runtime.decision_policy = SimpleNamespace(decision_config=BrowserDecisionConfig(mode="hybrid"))
    initial = {"ok": True, "url": page.url, "page_state": page.export_summary(), "dom": "old"}
    fresh = {**initial, "decision_observation": {"capture_id": "fresh", "url": page.url}}
    runtime.capture_browser_state = AsyncMock(return_value=initial)
    runtime.capture_reconciliation_browser_state = AsyncMock(
        side_effect=TimeoutError() if repair_fails else None, return_value=fresh)
    processor = BrowserStateContextProcessor(BrowserStateContextProcessorConfig(provider=runtime))
    window = ContextWindow(context_messages=[])
    _, window = await processor.on_get_context_window(None, window)
    group = failed_group({"ok": False, "executed": False, "state_changed": False,
                          "observation_invalidated": True})
    if not repair_fails:
        # An ACKed action in the same model group must not turn this into an unbounded full AX refresh.
        group[0].tool_calls.insert(0, ToolCall(id="ack", type="function", name="browser_click", arguments="{}"))
        group.insert(1, ToolMessage(tool_call_id="ack", content='{"ok":true,"executed":true,"state_changed":true}'))
    window.context_messages.extend(group)
    for _ in range(3):
        _, window = await processor.on_get_context_window(None, window)
    runtime.capture_browser_state.assert_awaited_once()
    runtime.capture_reconciliation_browser_state.assert_awaited_once()
    cached = processor._cached_state
    assert cached.get("decision_observation", {}).get("capture_id") == (None if repair_fails else "fresh")
    if repair_fails:
        assert cached["decision_observation_error"] == "decision_repair_TimeoutError"


@pytest.mark.asyncio
async def test_known_invalid_snapshot_cannot_publish_even_if_context_still_has_old_capture():
    policy, llm, client, runtime, context, captured = setup_policy()
    runtime.ensure_page_state().decision_snapshot = {"invalidated": "browser_policy_stale_node"}
    metadata = await policy.publish_context(context, captured, refresh=False, observation_only=False)
    await policy.invoke([UserMessage(content="cached state", metadata=metadata)], tools=LOCAL_TOOLS)
    client.evaluate.assert_not_awaited()
    llm.invoke.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("payload,code", [(None, "guard_observation_unavailable"),
                                         ({"ok": False}, "stale_node")])
async def test_guard_transport_shape_is_not_itself_proof_of_stale_node(payload, code):
    runtime = SimpleNamespace(call_playwright_run_code_unsafe=AsyncMock(return_value=payload),
                              unwrap_mcp_text_result=lambda value: value)
    with pytest.raises(ValueError, match=code):
        await _validate_script(runtime, "guard-code")


@pytest.mark.asyncio
@pytest.mark.parametrize("verified", [False, True])
async def test_finish_always_hands_judgment_to_llm_without_phase_certification(verified):
    policy, llm, client, runtime, context, captured = setup_policy()
    phase = context.get_session_ref().get_state(PHASE)
    set_phase(phase, {"objective": "Read current facts"}, runtime.ensure_page_state().export_decision_targets())
    phase["active_phase_contract"]["status"] = "verified" if verified else "in_progress"
    choose_operation(client, "FINISH")
    await policy.invoke(await messages_for(policy, context, captured), tools=LOCAL_TOOLS)
    assert next(iter(policy._tasks.values())).fallback_reason == "finish_to_llm"
    assert "do not require certification" in llm.invoke.call_args.kwargs["messages"][-1].content


@pytest.mark.parametrize("args", [{"objective": "Read"}, {"op": "set", "objective": "Read", "phase_version": 1}])
def test_phase_still_rejects_invalid_requests_with_corrective_examples(args):
    with pytest.raises(PhaseInputError) as caught:
        validate_request(args)
    assert '"op":"set"' in caught.value.details["correction"]


@pytest.mark.parametrize("op", ["wait", "snapshot"])
def test_invalid_batch_ops_point_to_existing_page_tool(op):
    errors = validate_batch_steps([{"op": op}])
    assert any("browser_page_action" in error and "generation_id" in error for error in errors)


def run_probe(page, code):
    return page.evaluate("async code => await eval('(' + code + ')')({evaluate: (fn, arg) => fn(arg)})", code)


def test_recommendation_region_is_source_fact_not_primary_cart_row(dom_page):
    html = '''<style>article {width:450px;height:140px;margin:12px}</style>
      <main><section aria-label="Shopping basket"><p>Chosen keyboard quantity 1</p></section>
      <section id="JustForYou"><h2>Recommended for you</h2>
      <article class="product-card"><h2><a href="https://example.test/item/lamp">Desk Lamp</a></h2>
      <span class="price">$19</span><p>Compact desk lamp product description</p></article>
      <article class="product-card"><h2><a href="https://example.test/item/cable">Cable</a></h2>
      <span class="price">$9</span><p>Portable charging cable product description</p></article></section>'''
    dom_page.set_content(html + '</main>')
    result = run_probe(dom_page, build_card_probe_js(max_cards=12, viewport_only=False, generation_id="g1"))
    normalize_card_probe_payload(result)
    lamp = next(c for c in result["cards"] if "lamp" in c.get("primary_link", ""))
    assert lamp["region"] == "recommendation" and lamp.get("result_index") is None
    # Existing readers prefer real main results when present; a title containing "recommended" is not a section cue.
    dom_page.set_content(html + '''<section><h2>Results</h2><article class="product-card">
      <h2><a href="https://example.test/item/book">Recommended travel guide</a></h2>
      <span class="price">$25</span><p>Travel guide book product description</p></article></section></main>''')
    result = run_probe(dom_page, build_card_probe_js(max_cards=12, viewport_only=False, generation_id="g1"))
    normalize_card_probe_payload(result)
    book = next(c for c in result["cards"] if "book" in c.get("primary_link", ""))
    assert book["region"] == "main_result"


def test_query_probe_reads_near_object_without_inventing_a_target(dom_page):
    dom_page.set_content('''<ul><li><span>Selected keyboard</span><span>Quantity 1</span>
      <button aria-label="Remove">×</button></li><li>Other item<button>Other action</button></li></ul>''')
    result = run_probe(dom_page, build_interactive_probe_js(query="Selected keyboard", generation_id="g1"))
    assert "Selected keyboard" in result["local_excerpt"] and "Quantity 1" in result["local_excerpt"]
    assert [item["accessible_name"] for item in result["elements"]] == ["Remove"]
    assert result["elements"][0]["selector_hint_validated"]  # Actual named row control, not a guessed selector.
    controls = run_probe(dom_page, build_interactive_probe_js(query="Remove", generation_id="g1", decision_mode=True))
    remove = next(item for item in controls["elements"] if item["accessible_name"] == "Remove")
    assert "Selected keyboard" in remove["decision_state"]["nearby_text"]
