# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Local handoff and fixed-reader semantics; no model/provider requests."""

import copy
import json
from types import SimpleNamespace

import pytest
from openjiuwen.harness.tools.browser_move.decision.action_space import build_menu, build_request
from openjiuwen.harness.tools.browser_move.decision.guard import NODE_STATE_JS
from openjiuwen.harness.tools.browser_move.decision.jev_client import DecisionUnavailable, validate_action
from openjiuwen.harness.tools.browser_move.playwright_runtime.evidence import observed_label
from openjiuwen.harness.tools.browser_move.playwright_runtime.page_state import BrowserPageState
from openjiuwen.harness.tools.browser_move.playwright_runtime.phase_contract import (
    PHASE_KEY,
    PhaseInputError,
    constrain_actions,
    observe_conditions,
    observe_llm_scope,
    observe_runtime,
    project_phase,
    set_phase,
)
from tests.unit_tests.harness.tools.browser_move.test_browser_jev_integration import probe
from tests.unit_tests.harness.tools.browser_move.test_browser_jev_phase_contract import control
from tests.unit_tests.harness.tools.browser_move.test_browser_jev_policy import (
    grouped_answer,
    messages_for,
    setup_policy,
)
from tests.unit_tests.harness.tools.browser_move.test_browser_local_decision_loop import LOCAL_TOOLS
from tests.unit_tests.harness.tools.browser_move.test_browser_september17_contracts import dom_page as shared_dom_page

dom_page = shared_dom_page


def fragment(operations=None):
    state = {"goal": "Choose a suitable item, then perform the requested action"}
    old = control("old", "Search", 1)
    set_phase(state, {"objective": "Submit the search form", "target_ids": ["old"],
                      "allowed_operations": operations or ["click", "fill"],
                      "read_queries": ["Destination"]}, [old])
    return state, old


def list_control():
    item = control("rating", "Guest rating Great", 7)
    item["kind"] = "rating_filter"
    item["decision_state"]["node_guard"]["document"] = "list-document"
    return item


def new_document(state, controls):
    observe_conditions(state, {"capture_id": "list-capture", "url": "https://example.test/list",
                               "page_guard": {"document": "list-document"}, "controls": controls})


def test_form_fragment_no_longer_owns_result_filters_or_allows_unbound_business_button():
    state, old = fragment()
    rating = list_control()
    add = control("add", "Add to Cart", 8)
    add["decision_state"]["node_guard"]["document"] = "list-document"
    new_document(state, [rating, add])
    assert project_phase(state)["handoff"]["reason"] == "bound_document_changed"
    assert project_phase(state)["read_queries"] == []
    menu = build_menu([rating, add], "Choose the matching item", limit=30)
    constrain_actions(menu, state, [rating, add])
    assert not any(s["op"] == "click" for s in menu.steps.values())
    assert state["active_phase_contract"]["targets"][0]["document"] == "doc"
    # Only a new explicit LLM fragment admits the selected business control.
    set_phase(state, {"objective": "Add the selected variant", "target_ids": ["add"],
                      "allowed_operations": ["click"]}, [rating, add])
    menu = build_menu([rating, add], "Add the selected variant", limit=30)
    constrain_actions(menu, state, [rating, add])
    assert {s["target_id"] for s in menu.steps.values() if s["op"] == "click"} == {"add"}


def test_read_only_operation_ceiling_survives_document_change():
    state, _ = fragment(["read_text", "find", "snapshot"])
    rating = list_control()
    new_document(state, [rating])
    menu = build_menu([rating], "Read the selected details", limit=30)
    constrain_actions(menu, state, [rating])
    assert not any(s["op"] in {"click", "fill"} for s in menu.steps.values())


@pytest.mark.parametrize("snapshot", [
    {}, {"capture_id": "partial", "controls": []},
    {"capture_id": "partial", "controls": [control("unrelated", "Another control", 50)]},
    {"page_guard": {"document": "different-but-not-captured"}},
])
def test_missing_partial_or_uncaptured_controls_do_not_expire_fragment(snapshot):
    state, _ = fragment()
    observe_conditions(state, snapshot)
    assert not project_phase(state)["handoff"]


@pytest.mark.parametrize("execution,op,expected", [
    ("acknowledged", "click", True), ("verified", "fill", True),
    ("rejected_before_dispatch", "click", False), ("dispatched_unknown", "click", False),
    ("acknowledged", "extract_text", False), ("acknowledged", "hover", False),
])
def test_only_acknowledged_llm_control_actions_signal_new_local_scope(execution, op, expected):
    state, _ = fragment()
    observe_llm_scope(state, [{"op": op, "execution_state": execution,
                              "control": control("new", "Other field", 2)}])
    assert bool(project_phase(state)["handoff"]) is expected


def test_overlay_dismiss_and_same_target_ack_do_not_replace_intent():
    state, old = fragment()
    for item in [old, control("close", "Close", 20)]:
        observe_llm_scope(state, [{"op": "click", "execution_state": "acknowledged", "control": item}])
    assert not project_phase(state)["handoff"]


def test_same_intent_can_be_explicitly_rebound_after_scope_changed():
    state, old = fragment()
    new_document(state, [list_control()])
    set_phase(state, {"objective": "Submit the search form", "target_ids": ["old"],
                      "allowed_operations": ["click", "fill"], "read_queries": ["Destination"]}, [old])
    assert project_phase(state)["version"] == 2
    assert not project_phase(state)["handoff"]


@pytest.mark.parametrize("queries", [[""], ["   "], ["x" * 161], ["x"] * 7, ["x", "x"]])
def test_reader_literal_schema_is_bounded_and_atomic(queries):
    state, _ = fragment()
    before = copy.deepcopy(state)
    with pytest.raises((PhaseInputError, ValueError)):
        set_phase(state, {"objective": "Read", "read_queries": queries}, [])
    assert state == before


def test_resolved_query_can_probe_with_existing_controls_without_becoming_a_fill_value():
    rating = list_control()
    field = control("price", "Price", 8, tag="input", current_value="")
    field["role"] = "textbox"
    menu = build_menu([rating, field], "Inspect available filters", limit=80,
                      page={"page_guard": {"document": "doc"}},
                      page_operations={"find"}, probe_tools=("browser_probe_interactives",),
                      read_queries=["Guest rating Great", "Breakfast included"])
    probes = [s for s in menu.steps.values() if s["op"] == "probe_interactives"]
    assert {s["query"] for s in probes} == {"Guest rating Great", "Breakfast included"}
    assert {s["query"] for s in menu.steps.values() if s["op"] == "find"} == {s["query"] for s in probes}
    assert not any(s["op"] == "fill" for s in menu.steps.values())


def test_queries_are_replaced_not_accumulated_and_verify_does_not_accept_them():
    state, _ = fragment()
    set_phase(state, {"objective": "Inspect result filters", "read_queries": ["Breakfast included"]}, [])
    assert project_phase(state)["read_queries"] == ["Breakfast included"]
    set_phase(state, {"objective": "Read details"}, [])
    assert project_phase(state)["read_queries"] == []
    from openjiuwen.harness.tools.browser_move.playwright_runtime.phase_contract import validate_request
    with pytest.raises(PhaseInputError):
        validate_request({"op": "verify", "read_queries": ["Breakfast included"]})


@pytest.mark.asyncio
async def test_document_handoff_occurs_once_and_fresh_phase_reenters_policy():
    policy, llm, client, runtime, context, captured = setup_policy()
    page = runtime.ensure_page_state()
    state = context.get_session_ref().get_state(PHASE_KEY)
    original = page.export_decision_targets()
    set_phase(state, {"objective": "Submit old form", "target_ids": [original[0]["target_id"]]}, original)
    # A genuine new document, not just omitted old nodes in a narrow probe.
    page.register_interactives({"url": page.url, "elements": [{
        "selector_hint": "#rating", "selector_hint_validated": True, "match_count": 1,
        "role": "button", "accessible_name": "Great", "visible": True, "enabled": True,
        "actionable": True, "clickable": True,
        "decision_state": {"tag": "button", "node_guard": {"document": "doc-next", "node": 7}},
    }]})
    page.decision_snapshot = {"capture_id": "next-document", "url": page.url,
                              "page_guard": {"document": "doc-next"}}
    first = await policy.invoke(await messages_for(policy, context, captured), tools=LOCAL_TOOLS)
    assert first.metadata["browser_policy"]["reason"] == "local_intent_changed"
    assert "old form" not in policy.current_intent
    client.evaluate.assert_not_awaited()
    assert state["active_phase_contract"]["handoff"]["offered"]
    client.evaluate.side_effect = lambda payload, **_: grouped_answer(payload, "EXTRACT_TEXT")
    pending = await policy.invoke(await messages_for(policy, context, captured), tools=LOCAL_TOOLS)
    assert pending.tool_calls and pending.tool_calls[0].id.startswith("jev_")
    assert llm.invoke.await_count == 1  # Not a recurring whole-window phase gate.
    policy.record_execution(SimpleNamespace(tool_call=pending.tool_calls[0]),
                            context.get_session_ref(), {"success": True})
    current = page.export_decision_targets()
    set_phase(state, {"objective": "Choose Great rating", "target_ids": [current[0]["target_id"]],
                      "allowed_operations": ["click"]}, current)
    client.evaluate.side_effect = lambda payload, **_: grouped_answer(payload, "CLICK")
    second = await policy.invoke(await messages_for(policy, context, captured), tools=LOCAL_TOOLS)
    assert second.tool_calls[0].id.startswith("jev_")
    assert llm.invoke.await_count == 1


@pytest.mark.asyncio
async def test_targeted_probe_uses_common_read_receipts_and_does_not_loop():
    policy, _, client, runtime, context, captured = setup_policy()
    state = context.get_session_ref().get_state(PHASE_KEY)
    set_phase(state, {"objective": "Find local filter controls", "read_queries": ["Guest rating Great"]}, [])
    page = runtime.ensure_page_state()
    page.decision_snapshot["page_guard"] = {"document": "doc-a"}
    tools = [*LOCAL_TOOLS, {"name": "browser_probe_interactives"}]
    client.evaluate.side_effect = lambda payload, **_: grouped_answer(
        payload, "PROBE_INTERACTIVES" if "PROBE_INTERACTIVES" in payload["questions"]["action"]["criteria"]
        else "HANDOFF")
    response = await policy.invoke(await messages_for(policy, context, captured), tools=tools)
    call = response.tool_calls[0]
    assert json.loads(call.arguments)["query"] == "Guest rating Great"
    policy.record_execution(SimpleNamespace(tool_call=call), context.get_session_ref(), {"success": True})
    page.decision_snapshot["capture_id"] = "new-query-read"
    await policy.invoke(await messages_for(policy, context, captured), tools=tools)
    assert "PROBE_INTERACTIVES" not in client.evaluate.call_args.args[0]["questions"]["action"]["criteria"]
    # A changed literal is new work, not a restored generic reread quota.
    set_phase(state, {"objective": "Find local filter controls", "read_queries": ["Breakfast included"]}, [])
    response = await policy.invoke(await messages_for(policy, context, captured), tools=tools)
    assert json.loads(response.tool_calls[0].arguments)["query"] == "Breakfast included"


def test_fixed_reader_scope_semantics_preserve_menu_and_both_thresholds():
    page = {"url": "https://example.test/list", "page_guard": {"document": "d"},
            "page_position": {"pixels_below": 500}}
    menu = build_menu([], "Read candidate information", limit=60, page=page,
                      probe_tools=("browser_probe_cards",), page_operations={"read_text"})
    before = copy.deepcopy(menu)
    payload = build_request("jev-latest", {"current_intent": "Read candidate information", "page": page}, menu)
    assert menu == before
    choices = payload["questions"]["target_PROBE_CARDS"]["criteria"]
    assert {v["scope"] for v in choices.values()} == {"current_document", "current_viewport"}
    assert all(v["choice_kind"] == "bound_reader_scope" for v in choices.values())
    assert "not selecting a result" in payload["questions"]["action"]["criteria"]["PROBE_CARDS"]
    response = grouped_answer(payload, "PROBE_CARDS")
    response["answers"]["action"]["confidence"] = 0.5
    response["answers"]["target_PROBE_CARDS"]["confidence"] = 0.599
    with pytest.raises(DecisionUnavailable, match="uncertain_choice"):
        validate_action(response["answers"], payload["questions"], 0.5, target_threshold=0.6)
    response["answers"]["target_PROBE_CARDS"]["confidence"] = 0.6
    key, _ = validate_action(response["answers"], payload["questions"], 0.5, target_threshold=0.6)
    assert menu.steps[key]["op"] == "probe_cards"


@pytest.mark.parametrize("role", ["checkbox", "switch"])
@pytest.mark.parametrize("checked,guard_value,accepted", [
    (False, "false", True), (True, "true", True), (False, None, False),
    (False, "true", False), (None, "mixed", False),
])
def test_aria_toggle_requires_an_explicit_consistent_checked_binding(role, checked, guard_value, accepted):
    item = control("toggle", "Selected filter", 1, tag="div", checked=checked)
    item["role"] = role
    item["decision_state"]["node_guard"]["checked"] = guard_value
    menu = build_menu([item], "Toggle the selected filter", limit=30)
    assert any(s["op"] == "click" for s in menu.steps.values()) is accepted
    assert not any(s["op"] == "set_checked" for s in menu.steps.values())
    state = {}
    set_phase(state, {"objective": "Inspect first", "allowed_operations": ["read_text"]}, [item])
    constrain_actions(menu, state, [item])
    assert not any(s["op"] == "click" for s in menu.steps.values())


def test_aria_filter_probe_menu_and_live_checked_guard_use_one_binding(dom_page):
    dom_page.set_content('''<div id="filter" role="checkbox" aria-checked="false" tabindex="0"
        onclick="this.setAttribute('aria-checked', this.getAttribute('aria-checked') === 'false' ? 'true' : 'false')">
        Breakfast included</div>''')
    page = BrowserPageState()
    page.register_interactives(probe(dom_page))
    controls = page.export_decision_targets()
    item = next(c for c in controls if observed_label(c).strip() == "Breakfast included")
    state = {}
    set_phase(state, {"objective": "Enable the chosen breakfast filter", "allowed_operations": ["click"],
                      "target_ids": [item["target_id"]]}, controls)
    menu = build_menu(controls, state["active_phase_contract"]["objective"], limit=30)
    constrain_actions(menu, state, controls)
    assert any(s == {"op": "click", "target_id": item["target_id"]} for s in menu.steps.values())
    expected = item["decision_state"]["node_guard"]
    assert dom_page.locator("#filter").evaluate(NODE_STATE_JS) == expected
    dom_page.locator("#filter").click()
    assert dom_page.locator("#filter").get_attribute("aria-checked") == "true"
    assert dom_page.locator("#filter").evaluate(NODE_STATE_JS) != expected


@pytest.mark.asyncio
@pytest.mark.parametrize("source,version,expected", [("llm", 1, True), ("jev", 1, False), ("llm", 0, False)])
async def test_local_handoff_belongs_to_shared_observation_lifecycle(source, version, expected):
    state, _ = fragment()
    state["execution_journal"] = [{"model_source": source, "phase_version": version,
        "execution_state": "acknowledged", "steps": [{"op": "click", "execution_state": "acknowledged",
                                                       "control": control("new", "Other field", 2)}]}]
    session = SimpleNamespace(get_state=lambda _: state)
    await observe_runtime(None, session, {"ok": True, "decision_observation": {
        "capture_id": "new-controls", "controls": [control("new", "Other field", 2)]}})
    assert bool(project_phase(state)["handoff"]) is expected
