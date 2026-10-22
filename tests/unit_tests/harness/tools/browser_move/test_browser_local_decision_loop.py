# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Candidate-first admission, shared observations and bounded hybrid handover."""

import asyncio
import copy
import json
import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from openjiuwen.core.foundation.llm.schema.message import ToolMessage
from openjiuwen.core.foundation.llm.schema.message_chunk import AssistantMessageChunk
from openjiuwen.harness.tools.browser_move.decision.action_space import build_menu, build_request
from openjiuwen.harness.tools.browser_move.decision.config import BrowserDecisionConfig
from openjiuwen.harness.tools.browser_move.decision.guard import PAGE_STATE_JS
from openjiuwen.harness.tools.browser_move.decision.jev_client import DecisionUnavailable, JevClient, validate_action
from openjiuwen.harness.tools.browser_move.playwright_runtime import execution_journal as journal
from openjiuwen.harness.tools.browser_move.playwright_runtime.browser_working_context import BrowserWorkingContextStore
from openjiuwen.harness.tools.browser_move.playwright_runtime.evidence import observed_label
from openjiuwen.harness.tools.browser_move.playwright_runtime.phase_contract import set_phase
from openjiuwen.harness.tools.browser_move.playwright_runtime.policy_page_action import (
    BrowserPageActionTool,
    fixed_text_script,
)
from openjiuwen.harness.tools.browser_move.playwright_runtime.runtime import BrowserRuntimeRail as Rail
from tests.unit_tests.harness.tools.browser_move.test_browser_jev_phase_contract import session_for
from tests.unit_tests.harness.tools.browser_move.test_browser_jev_policy import (
    TOOLS,
    choose_operation,
    grouped_answer,
    messages_for,
    setup_policy,
)
from tests.unit_tests.harness.tools.browser_move.test_browser_page_state import _make_bare_runtime
from tests.unit_tests.harness.tools.browser_move.test_browser_september17_contracts import dom_page as shared_dom_page

dom_page = shared_dom_page

PHASE = "__browser_phase_budget_state__"
LOCAL_TOOLS = [*TOOLS, {"name": "browser_page_action"}]


def page_operations(runtime):
    page = runtime.ensure_page_state()
    page.decision_snapshot["page_guard"] = {"document": "doc-a", "history_length": 1}
    return page


@pytest.mark.asyncio
@pytest.mark.parametrize("restriction", ["unknown", "failed", "stalled", "ambiguous"])
async def test_local_reader_is_offered_before_whole_state_fallback(restriction):
    policy, llm, client, runtime, context, captured = setup_policy()
    page_operations(runtime)
    state = context.get_session_ref().get_state(PHASE)
    if restriction == "unknown":
        state["execution_journal"] = [{"call_id": "uncertain-save", "impact": "business",
                                       "execution_state": "dispatched_unknown"}]
    if restriction == "ambiguous":
        state["task"] = '搜索“mouse”和“keyboard”，分别读取价格'
    if restriction == "stalled":
        captured["semantic_progress"] = {"consecutive_no_progress": 8}
    choose_operation(client, "EXTRACT_TEXT")
    messages = await messages_for(policy, context, captured)
    if restriction == "failed":
        messages.append(ToolMessage(tool_call_id="previous", content='{"ok":false}'))
    result = await policy.invoke(messages, tools=LOCAL_TOOLS)
    assert json.loads(result.tool_calls[0].arguments)["op"] == "read_text"
    assert result.metadata["browser_policy"]["evaluated"]
    client.evaluate.assert_awaited_once()
    llm.invoke.assert_not_awaited()
    if restriction == "unknown":
        assert "CLICK" not in client.evaluate.call_args.args[0]["questions"]["action"]["criteria"]
        assert state["execution_journal"][0]["execution_state"] == "dispatched_unknown"


@pytest.mark.asyncio
async def test_two_failed_clicks_remove_only_that_action_and_llm_recovery_reopens_it():
    policy, llm, client, runtime, context, captured = setup_policy()
    page = page_operations(runtime)
    session = context.get_session_ref()
    choose_operation(client, "CLICK")
    for index in range(2):
        call = (await policy.invoke(await messages_for(policy, context, captured), tools=LOCAL_TOOLS)).tool_calls[0]
        policy.record_execution(SimpleNamespace(tool_call=call), session, {"success": False, "executed": False})
    choose_operation(client, "EXTRACT_TEXT")
    result = await policy.invoke(await messages_for(policy, context, captured), tools=LOCAL_TOOLS)
    assert result.metadata["browser_policy"]["excluded"]["failed_target"] == 1
    assert "CLICK" not in client.evaluate.call_args.args[0]["questions"]["action"]["criteria"]
    assert "HOVER" in client.evaluate.call_args.args[0]["questions"]["action"]["criteria"]
    # A successful LLM recovery plus a changed observed capability permits re-entry.
    policy.record_execution(SimpleNamespace(tool_call=SimpleNamespace(id="llm-repair"),
                                            tool_name="browser_snapshot", tool_args={}), session, {"success": True})
    target = page.get_target(page.export_decision_targets()[0]["target_id"])
    target.decision_state["node_guard"]["expanded"] = True
    page.decision_snapshot["capture_id"] = "after-repair"
    choose_operation(client, "CLICK")
    result = await policy.invoke(await messages_for(policy, context, captured), tools=LOCAL_TOOLS)
    assert result.metadata["browser_policy"]["operation"] == "click"
    assert session.get_state(PHASE)["decision_policy"]["failed_actions"]  # Old failures remain scoped.
    assert client.evaluate.await_count == 4


@pytest.mark.asyncio
async def test_new_binding_changes_legal_menu_even_when_page_state_is_identical():
    policy, llm, client, runtime, context, captured = setup_policy(goal="查询汇率")
    page = page_operations(runtime)
    target = page.get_target(page.export_decision_targets()[0]["target_id"])
    target.role = "searchbox"
    target.decision_state.update(tag="input", search_like=True, current_value="resolved currency pair")
    choose_operation(client, "HANDOFF")
    for _ in range(2):
        await policy.invoke(await messages_for(policy, context, captured), tools=LOCAL_TOOLS)
    assert client.evaluate.await_count == 1
    policy.record_execution(SimpleNamespace(tool_call=SimpleNamespace(id="llm-fill"),
        tool_name="browser_type", tool_args={"target_id": target.target_id, "text": "resolved currency pair"}),
        context.get_session_ref(), {"success": True})
    choose_operation(client, "PRESS_ENTER")
    result = await policy.invoke(await messages_for(policy, context, captured), tools=LOCAL_TOOLS)
    assert json.loads(result.tool_calls[0].arguments)["steps"][0]["key"] == "Enter"
    assert client.evaluate.await_count == 2


@pytest.mark.asyncio
async def test_concise_phase_allows_decision_for_long_parent_goal_without_resetting_deadline():
    policy, llm, client, runtime, context, captured = setup_policy(goal="complex objective " * 800)
    state = context.get_session_ref().get_state(PHASE)
    deadline = state["deadline_at"]
    set_phase(state, {"objective": "点击销量排序"}, runtime.ensure_page_state().export_decision_targets())
    result = await policy.invoke(await messages_for(policy, context, captured), tools=TOOLS)
    assert result.tool_calls and state["deadline_at"] == deadline
    assert len(json.dumps(client.evaluate.call_args.args[0]["state"])) < 6000


@pytest.mark.parametrize("provider", ["typesafe", "openrouter"])
@pytest.mark.asyncio
async def test_two_heads_use_one_provider_request_and_validate_selected_target(monkeypatch, provider):
    config = BrowserDecisionConfig(provider=provider, mode="hybrid", api_key_env="LOCAL_TEST_JEV")
    monkeypatch.setenv("LOCAL_TEST_JEV", "synthetic-not-a-credential")
    menu = build_menu([], "Read this page", limit=30, page={"url": "https://example.test/",
                      "page_guard": {"document": "d"}}, page_operations={"read_text", "snapshot", "wait"})
    payload = build_request(config.model, {"current_intent": "Read this page"}, menu)
    calls = []

    def handler(request):
        calls.append(json.loads(request.content))
        assert request.url.path.endswith("/decisions" if provider == "openrouter" else "/systemone")
        response = grouped_answer(payload, "EXTRACT_TEXT")
        response["model"] = config.model
        return httpx.Response(200, json=response)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        result = await JevClient(config, client=http).evaluate(payload, deadline_at=time.time() + 2)
    key, _ = validate_action(result["answers"], payload["questions"], 0.65)
    assert menu.steps[key]["op"] == "read_text"
    assert calls == [payload] and len(payload["questions"]) == 4
    other = next(iter(payload["questions"]["target_SNAPSHOT"]["criteria"]))
    result["answers"]["target_EXTRACT_TEXT"]["choice"] = other
    with pytest.raises(DecisionUnavailable):
        validate_action(result["answers"], payload["questions"], 0.65)


@pytest.mark.parametrize("input_label", [{"name": "销量"}, {"text": "销量"}, {"accessible_name": "销量"}])
def test_observed_labels_share_one_normalization(input_label):
    assert observed_label(input_label) == "销量"


def metadata(url="https://example.test/search?q=widget", capture="capture-post", selected="销量"):
    return {"ok": True, "url": url, "title": "Results", "tabs": [{"index": 0, "url": url, "current": True}],
            "page_position": {"pixels_below": 300}, "semantic_state": {"selected_filters": {"sort": selected}},
            "decision_probe": {"ok": True, "url": url, "elements": [{"selector_hint": "#sort",
                "selector_hint_validated": True, "match_count": 1, "text": selected, "kind": "sort_tab",
                "role": "button", "visible": True, "enabled": True, "actionable": True, "clickable": True,
                "selected": True, "decision_state": {"tag": "button", "node_guard": {"document": "doc", "node": 1}}}],
                "decision_snapshot": {"capture_id": capture, "url": url, "page_text": "Observed results",
                                      "page_guard": {"document": "doc"}}}}


@pytest.mark.asyncio
async def test_sort_receipt_and_post_action_cards_share_revision_but_later_change_invalidates():
    runtime = _make_bare_runtime()
    runtime.ensure_runtime_ready = AsyncMock()
    page = runtime.ensure_page_state()
    page.observe(url="https://example.test/search?q=widget")
    runtime._invalidate_changed_listing(metadata(selected="默认"))
    page.mark_interaction()
    admitted_revision = page.interaction_revision
    runtime.capture_browser_metadata = AsyncMock(return_value=(metadata(), None))
    observation = await runtime.capture_reconciliation_browser_state(action_group_id="sort", include_decision=True)
    assert observation["page_state"]["interaction_revision"] == admitted_revision
    page.register_cards({"url": page.url, "cards": [{"title": "Widget", "primary_link": "https://example.test/1"}]})
    runtime._invalidate_changed_listing(metadata())
    assert page.interaction_revision == admitted_revision and page.export()["cards_observed"]
    runtime._invalidate_changed_listing(metadata(selected="价格"))
    assert page.interaction_revision == admitted_revision + 1 and not page.export()["cards_observed"]


@pytest.mark.asyncio
async def test_combined_rpc_reuses_facts_once_and_never_after_another_action():
    runtime = _make_bare_runtime()
    runtime.ensure_runtime_ready = AsyncMock()
    runtime.call_playwright_tool = AsyncMock()
    runtime.call_playwright_run_code_unsafe = AsyncMock(return_value={
        "ok": True, "url": metadata()["url"], "_runtime_observation": metadata(),
    })
    result = await runtime.call_fixed_with_observation("async page => ({ok:true,url:page.url()})")
    assert result["ok"] and "_runtime_observation" not in result
    captured = await runtime.capture_browser_state(action_group_id="action", include_decision=True)
    assert captured["decision_observation"]["capture_id"] == "capture-post"
    assert captured["tabs"][0]["current"] and captured["page_position"]["pixels_below"] == 300
    runtime.call_playwright_run_code_unsafe.assert_awaited_once()
    runtime.call_playwright_tool.assert_not_awaited()
    assert not runtime._has_post_observation()
    await runtime.call_fixed_with_observation("async page => ({ok:true})")
    runtime.ensure_page_state().mark_interaction()
    assert not runtime._has_post_observation()


@pytest.mark.asyncio
async def test_fixed_text_read_joins_existing_evidence_without_cart_mutation():
    runtime = _make_bare_runtime()
    runtime._service = SimpleNamespace(allowed_tool_names=None)
    page = runtime.ensure_page_state()
    page.observe(url="https://shop.test/cart")
    runtime.call_fixed_with_observation = AsyncMock(return_value={
        "ok": True, "url": page.url, "title": "Cart", "text": "Cart has three products", "read_only": True,
    })
    state = Rail._build_phase_state("Read the cart product count")
    state["last_page"] = {"url": page.url}
    session = session_for(state)
    args = {"generation_id": page.generation_id, "op": "read_text"}
    result = await BrowserPageActionTool(runtime).invoke(args, session=session)
    assert result.success and result.data["state_changed"] is False
    assert page.read_observation["text"] == "Cart has three products"
    assert not journal.is_write("browser_page_action", args)
    assert Rail._page_observation_evidence(state, result.data, "browser_page_action", args)
    assert not state.get("cart_mutation_started")


def test_fixed_reader_treats_query_as_data_and_runs_in_real_dom(dom_page):
    query = 'needle "); window.injected = true; //'
    dom_page.set_content("<h1>Visible content</h1><p></p>")
    dom_page.locator("p").evaluate("(node, value) => node.textContent = value", query)
    value = dom_page.evaluate("async code => await eval('(' + code + ')')({evaluate:(fn,arg)=>fn(arg)})",
                              fixed_text_script(query))
    assert value["ok"] and query in value["text"] and len(value["matches"]) == 1
    assert dom_page.evaluate("typeof window.injected") == "undefined"


@pytest.mark.asyncio
async def test_llm_stream_total_timeout_closes_producer_without_replaying_partial_action(monkeypatch):
    policy, llm, client, runtime, context, captured = setup_policy("llm")
    closed = []

    async def producer(**kwargs):
        try:
            yield AssistantMessageChunk(content="Thinking")
            await asyncio.sleep(1)
        finally:
            closed.append(True)

    llm.stream = producer
    monkeypatch.setattr(policy, "_llm_wait_limit", lambda remaining: 0.02)
    with pytest.raises(TimeoutError):
        async for chunk in policy.stream(await messages_for(policy, context, captured), tools=TOOLS):
            assert not chunk.tool_calls
    assert closed == [True]
    client.evaluate.assert_not_awaited()
    runtime.call_playwright_run_code_unsafe.assert_not_awaited()


def test_replan_count_is_consecutive_and_progress_does_not_replenish_action_budget():
    state = Rail._build_phase_state("Read three pages")
    state.update(replan_count=3, replan_required=True, replan_trial_pending=True, deadline_at=time.time() + 600)
    deadline = state["deadline_at"]
    budgets = copy.deepcopy(state["phases"])
    session = session_for(state)
    assert BrowserWorkingContextStore.sync_semantic_progress(session, {
        "revision": 1, "observable_progress": True, "progress": "progress", "consecutive_no_progress": 0,
    })
    state = session.get_state(PHASE)
    assert state["replan_count"] == 0 and state["deadline_at"] == deadline and state["phases"] == budgets


@pytest.mark.asyncio
async def test_fill_submit_sort_read_then_llm_repair_reenters_same_runtime():
    policy, llm, client, runtime, context, captured = setup_policy(goal='搜索“widget”，按销量排序并读取列表')
    page = page_operations(runtime)
    target = page.get_target(page.export_decision_targets()[0]["target_id"])
    target.role, target.name = "searchbox", "搜索"
    target.decision_state.update(tag="input", input_type="search", search_like=True, current_value="")
    session = context.get_session_ref()
    seen = []
    for index, operation in enumerate(("TYPE_TEXT", "PRESS_ENTER", "CLICK", "EXTRACT_TEXT")):
        choose_operation(client, operation)
        result = await policy.invoke(await messages_for(policy, context, captured), tools=LOCAL_TOOLS)
        call = result.tool_calls[0]
        inputs = SimpleNamespace(tool_call=call, tool_name=call.name, tool_args=call.arguments)
        await policy.validate_tool_call(inputs, session)
        args = json.loads(call.arguments)
        step = args["steps"][0] if "steps" in args else args
        seen.append(step["op"])
        # Exercise the same shared journal that normal tool rails use.
        journal.prepare(session, inputs, runtime)
        with journal.execution_scope(session, inputs):
            journal.mark_dispatched()
        journal.record_result(session, inputs, {"success": True}, {"ok": True, "executed": True})
        policy.record_execution(inputs, session, {"success": True, "executed": True})
        if operation == "TYPE_TEXT":
            assert step["value"] == "widget"
            target.decision_state["current_value"] = "widget"
        elif operation == "PRESS_ENTER":
            page.observe(url="https://example.test/search?q=widget")
            target.role, target.name, target.text, target.kind = "button", "销量", "销量", "sort_tab"
            target.decision_state = {"tag": "button", "node_guard": {"document": "doc-b", "node": 2}}
        elif operation == "CLICK":
            target.selected = True
        else:
            page.read_observation = {"text": "Widget costs 10", "url": page.url}
        page.decision_snapshot.update(capture_id=f"loop-{index}", url=page.url)
        captured["url"] = page.url
    assert seen == ["fill", "press", "click", "read_text"]
    llm.invoke.assert_not_awaited()
    assert len(session.get_state(PHASE)["execution_journal"]) == 3  # Fixed reader is not a write.
    choose_operation(client, "HANDOFF")
    result = await policy.invoke(await messages_for(policy, context, captured), tools=LOCAL_TOOLS)
    assert result.content == "original LLM answer"
    session.get_state(PHASE)["task"] = "Read the missing details for the selected product"
    choose_operation(client, "EXTRACT_TEXT")
    result = await policy.invoke(await messages_for(policy, context, captured), tools=LOCAL_TOOLS)
    assert result.metadata["browser_policy"]["route"] == "jev"
    assert client.evaluate.await_count == 6 and llm.invoke.await_count == 1


@pytest.mark.asyncio
async def test_completed_first_result_milestone_survives_return_to_listing():
    policy, llm, client, runtime, context, captured = setup_policy(goal="打开第一条搜索结果并读取后续链接")
    page = page_operations(runtime)
    page.observe(url="https://search.test/?q=widget")
    page.decision_snapshot["url"] = page.url
    captured["url"] = page.url
    state = context.get_session_ref().get_state(PHASE)
    state["structured_evidence"] = [{"kind": "page_metadata", "destination_verified": True,
                                     "entity_url": "https://result.test/", "phase_version": 0}]
    for index in range(25):
        Rail._record_structured_evidence(state, {
            "ok": True, "operation": "read_text", "result": {"text": f"Additional observed page content {index}"},
            "page_state": {"url": page.url},
        }, tool_name="browser_page_action", tool_args={"op": "read_text"})
    assert len(state["structured_evidence"]) == 20
    assert any(item.get("destination_verified") for item in state["structured_evidence"])
    await messages_for(policy, context, captured)
    assert next(reversed(policy._observations.values())).page["first_result_pending"] is False
    set_phase(state, {"objective": "打开新查询的第一条搜索结果"}, page.export_decision_targets())
    await messages_for(policy, context, captured)
    assert next(reversed(policy._observations.values())).page["first_result_pending"] is True


@pytest.mark.parametrize("probe_available", [True, False])
def test_fixed_action_probe_in_one_rpc_keeps_receipt_when_probe_fails(dom_page, probe_available):
    dom_page.set_content('<input aria-label="Search" type="search"><h1>Example</h1>')
    runtime = _make_bare_runtime()
    code = runtime._fixed_observation_script("""async page => {
      await page.evaluate(() => {window.actions = (window.actions || 0) + 1;
        document.querySelector('input').value = 'widget';});
      return {ok:true, executed:true, url:page.url()};
    }""")
    value = dom_page.evaluate("""async args => {
      const page = {evaluate: (fn,arg) => fn(arg), url: () => location.href, title: async () => document.title};
      if (args.probe) page.context = () => ({pages: () => [page]});
      return await eval('(' + args.code + ')')(page);
    }""", {"probe": probe_available, "code": code})
    assert value["ok"] and value["executed"] and dom_page.evaluate("window.actions") == 1
    assert ("_runtime_observation" in value) is probe_available
    if probe_available:
        elements = value["_runtime_observation"]["decision_probe"]["elements"]
        assert any(item["decision_state"].get("current_value") == "widget" for item in elements)


@pytest.mark.asyncio
async def test_tab_selection_does_not_dispatch_after_observed_index_is_reused():
    runtime = _make_bare_runtime()
    runtime._service = SimpleNamespace(allowed_tool_names=("browser_tabs",))
    runtime.capture_browser_metadata = AsyncMock(return_value=({
        "tabs": [{"index": 1, "url": "https://different.test/"}],
    }, None))
    runtime.call_playwright_tool = AsyncMock()
    result = await BrowserPageActionTool(runtime).invoke({
        "generation_id": runtime.generation_id, "op": "select_tab", "index": 1, "url": "https://observed.test/",
    })
    assert not result.success and result.data["executed"] is False
    runtime.call_playwright_tool.assert_not_awaited()


def test_page_capability_and_long_option_list_cannot_hide_fixed_readers():
    controls = [{"target_id": f"select-{n}", "name": f"field-{n}", "role": "combobox",
                 "enabled": True, "actionable": True, "decision_state": {"tag": "select",
                 "node_guard": {"document": "d", "node": n},
                 "options": [{"value": str(i), "label": str(i)} for i in range(80)]}}
                for n in range(6)]
    page = {"url": "https://example.test/", "page_guard": {"document": "d"},
            "page_position": {"pixels_below": 300}}
    menu = build_menu(controls, "Open https://other.test/ and read fields", limit=30, page=page,
                      allow_page_actions=True, page_operations={"read_text", "wait"})
    assert any(step["op"] == "read_text" for step in menu.steps.values())
    assert any(step["op"] == "wait" for step in menu.steps.values())
    assert not any(step["op"] in {"navigate", "scroll"} for step in menu.steps.values())
    assert sum(step["op"] == "select_option" for step in menu.steps.values()) <= 30


@pytest.mark.parametrize("foreign", ["query", "source"])
def test_landing_proof_cannot_reuse_an_unrelated_result_list(foreign):
    source = "https://search.test/search?q=widget"
    destination = "https://item.test/1"
    state = {"query_id": "current", "last_page": {"url": source}, "structured_evidence": [{
        "query_id": "other" if foreign == "query" else "current",
        "source": "https://search.test/search?q=other" if foreign == "source" else source,
        "cards": [{"title": "Widget", "primary_link": destination, "region": "main_result", "is_ad": False}],
    }]}
    assert not Rail._destination_selection(state, destination, "browser_navigate", {
        "url": destination, "_runtime_source_url": source,
    }, landed_url=destination)


@pytest.mark.parametrize("selected", [True, False])
def test_popup_landing_receipt_requires_the_shared_native_tab_to_be_selected(selected):
    source, destination = "https://search.test/search?q=widget", "https://item.test/1"
    state = Rail._build_phase_state("Open the first search result and return its title")
    state["last_page"] = {"url": source, "title": "Old results"}
    state["structured_evidence"] = [{"source": source, "cards": [{
        "title": "Result link", "primary_link": destination, "region": "main_result", "is_ad": False,
    }]}]
    result = {"ok": True, "execution_mode": "compact_rpc",
              "page_state": {"url": destination, "title": "Actual landing title"},
              "page_binding": {"tab_switched": True, "mcp_selected": selected, "mcp_current_url": destination}}
    evidence = Rail._page_metadata_evidence(state, result, "browser_batch_interact", {
        "_runtime_source_url": source, "_runtime_selected_url": destination,
    })
    assert bool(evidence) is selected
    if selected:
        assert evidence["values"] == {"url": destination, "title": "Actual landing title"}
        assert evidence["destination_verified"]


@pytest.mark.parametrize("operation,cap", [("browser_evaluate", 15.0), ("browser_navigate", 30.0)])
def test_browser_call_uses_existing_resilience_and_keeps_shorter_timeout(operation, cap):
    tool = SimpleNamespace(properties={}, input_params=None)
    context = SimpleNamespace(agent=SimpleNamespace(ability_manager=SimpleNamespace(get=lambda name: tool)))
    Rail._validate_model_tool_args(context, operation, {})
    assert tool.properties["resilience"]["timeout_s"] == cap
    tool.properties["resilience"]["timeout_s"] = 3
    Rail._validate_model_tool_args(context, operation, {})
    assert tool.properties["resilience"]["timeout_s"] == 3


def test_fixed_reader_tolerates_layout_change_but_rejects_another_document(dom_page):
    policy, llm, client, runtime, context, captured = setup_policy()
    page = page_operations(runtime)
    page.decision_snapshot["page_guard"] = dom_page.evaluate(PAGE_STATE_JS)
    scripts = []

    async def compile_reader():
        choose_operation(client, "EXTRACT_TEXT")
        call = (await policy.invoke(await messages_for(policy, context, captured), tools=LOCAL_TOOLS)).tool_calls[0]
        inputs = SimpleNamespace(tool_call=call, tool_name=call.name, tool_args=call.arguments)

        async def inspect(script):
            scripts.append(script)
            return {"ok": True}

        runtime.call_playwright_run_code_unsafe.side_effect = inspect
        await policy.validate_tool_call(inputs, context.get_session_ref())

    # Playwright's synchronous fixture owns the main thread's event loop.
    with ThreadPoolExecutor(max_workers=1) as executor:
        executor.submit(lambda: asyncio.run(compile_reader())).result(timeout=10)
    dom_page.evaluate("document.body.style.height = '4000px'; window.scrollTo(0, 500)")
    execute = "async a => await eval('(' + a.code + ')')({url:()=>a.url,evaluate:fn=>fn()})"
    assert dom_page.evaluate(execute, {"code": scripts[-1], "url": page.url})["ok"]
    dom_page.evaluate("delete window.__openjiuwenDecisionNodes")
    assert not dom_page.evaluate(execute, {"code": scripts[-1], "url": page.url})["ok"]


def rerender(page, url, node):
    """Same link after a navigation/re-render: new url, node id and target id, same label and href."""
    page.register_interactives({"url": url, "title": "Search", "elements": [{
        "selector_hint": "#sales", "selector_hint_validated": True, "match_count": 1,
        "role": "button", "accessible_name": "销量", "text": "销量", "visible": True,
        "enabled": True, "actionable": True, "clickable": True,
        "decision_state": {"tag": "button", "node_guard": {"document": f"doc-{node}", "node": node}},
    }]})
    page.decision_snapshot.update(capture_id=f"capture-{node}", url=page.url)
    page.decision_snapshot["page_guard"] = {"document": f"doc-{node}", "history_length": 1}
    return {"ok": True, "url": page.url, "dom": "button 销量", "page_state": page.export()}


@pytest.mark.asyncio
@pytest.mark.parametrize("source", ["jev", "llm"])
async def test_target_failed_twice_in_one_run_is_not_reoffered_in_the_next_run(source):
    # The Kimberley Hotel link timed out twice, then jev picked it again in each new
    # subagent run for the same query: the retirement must outlive the run key.
    policy, llm, client, runtime, context, captured = setup_policy()
    page = page_operations(runtime)
    session = context.get_session_ref()
    phase = session.get_state(PHASE)
    choose_operation(client, "CLICK")
    for index in range(2):
        if source == "jev":
            call = (await policy.invoke(await messages_for(policy, context, captured), tools=LOCAL_TOOLS)).tool_calls[0]
        else:
            target_id = page.export_decision_targets()[0]["target_id"]
            call = SimpleNamespace(id=f"llm-{index}", name="browser_batch_interact",
                                   arguments=json.dumps({"steps": [{"op": "click", "target_id": target_id}]}))
        policy.record_execution(SimpleNamespace(tool_call=call, tool_name=call.name, tool_args=call.arguments),
                                session, {"success": False, "executed": True})
    # Run B: new subagent deadline, different url, re-rendered node.
    phase["deadline_started_at"] = 2
    captured = rerender(page, "https://example.test/list?page=2", node=7)
    choose_operation(client, "EXTRACT_TEXT")
    result = await policy.invoke(await messages_for(policy, context, captured), tools=LOCAL_TOOLS)
    assert result.metadata["browser_policy"]["excluded"]["failed_target"] == 1
    assert "CLICK" not in client.evaluate.call_args.args[0]["questions"]["action"]["criteria"]
    # Another query in the same process has its own history.
    phase["query_id"] = "another-query"
    await policy.invoke(await messages_for(policy, context, rerender(page, page.url, node=8)), tools=LOCAL_TOOLS)
    assert "CLICK" in client.evaluate.call_args.args[0]["questions"]["action"]["criteria"]
    del phase["query_id"]
    # The same target succeeding later (here by the LLM) re-admits it.
    target_id = page.export_decision_targets()[0]["target_id"]
    policy.record_execution(SimpleNamespace(tool_call=SimpleNamespace(id="llm-ok"), tool_name="browser_batch_interact",
                                            tool_args={"steps": [{"op": "click", "target_id": target_id}]}),
                            session, {"success": True})
    phase["deadline_started_at"] = 3
    await policy.invoke(await messages_for(policy, context, rerender(page, page.url, node=9)), tools=LOCAL_TOOLS)
    assert "CLICK" in client.evaluate.call_args.args[0]["questions"]["action"]["criteria"]


@pytest.mark.asyncio
async def test_next_jev_payload_shows_what_the_llm_just_did_without_secrets():
    # Jev otherwise sees only its own receipts and would re-pick a filter the LLM just set.
    policy, llm, client, runtime, context, captured = setup_policy(goal="筛选四星酒店")
    page = page_operations(runtime)
    element = {"selector_hint_validated": True, "match_count": 1, "visible": True, "enabled": True, "actionable": True}
    page.register_interactives({"url": page.url, "title": "Hotels", "elements": [
        {**element, "selector_hint": "#stars", "role": "checkbox", "accessible_name": "4 stars", "clickable": True,
         "region": "filters", "decision_state": {"tag": "label", "node_guard": {"document": "doc-a", "node": 2}}},
        {**element, "selector_hint": "#pw", "role": "textbox", "accessible_name": "Password",
         "decision_state": {"tag": "input", "sensitive": True, "node_guard": {"document": "doc-a", "node": 3}}},
    ]})
    page.decision_snapshot.update(capture_id="capture-hotels", url=page.url, page_guard={"document": "doc-a"})
    stars, password = (c["target_id"] for c in page.export_decision_targets())
    steps = [{"op": "click", "target_id": stars}, {"op": "fill", "target_id": password, "value": "hunter2"}]
    policy.record_execution(SimpleNamespace(tool_call=SimpleNamespace(id="llm-1"), tool_name="browser_batch_interact",
                                            tool_args={"steps": steps}), context.get_session_ref(), {"success": True})
    choose_operation(client, "HANDOFF")
    await policy.invoke(await messages_for(policy, context, captured), tools=LOCAL_TOOLS)
    payload = client.evaluate.call_args.args[0]
    assert payload["state"]["llm_recent_actions"] == [
        {"op": "click", "target_label": "4 stars [region: filters]", "outcome": "ok"},
        {"op": "fill", "target_label": "Password", "outcome": "ok"},
    ]
    assert "hunter2" not in json.dumps(payload)


@pytest.mark.asyncio
async def test_debug_logs_are_opt_in_and_cannot_change_the_route(monkeypatch):
    records = []

    def log(marker, data):
        if any(name in marker for name in ("PAYLOAD", "PICK", "LLM_ACTION")):
            records.append(marker)
            raise RuntimeError("broken log sink")

    monkeypatch.setattr("openjiuwen.harness.tools.browser_move.decision.policy_model.browser_agent_log_info", log)
    policy, llm, client, runtime, context, captured = setup_policy()
    choose_operation(client, "CLICK")
    await policy.invoke(await messages_for(policy, context, captured), tools=TOOLS)
    assert records == []  # Payloads carry task text; default logs never do.
    monkeypatch.setenv("OPENJIUWEN_BROWSER_POLICY_DEBUG_LOG", "1")
    runtime.ensure_page_state().decision_snapshot["capture_id"] = "capture-b"
    captured = {**captured, "semantic_state": {"result_count": 1}}
    result = await policy.invoke(await messages_for(policy, context, captured), tools=TOOLS)
    call = result.tool_calls[0]
    policy.check_tool_call_binding(SimpleNamespace(tool_call=SimpleNamespace(id="llm-1"), tool_name="browser_click",
                                                   tool_args={}), context.get_session_ref())
    assert records == ["[BROWSER_POLICY_PAYLOAD] %s", "[BROWSER_POLICY_PICK] %s", "[BROWSER_LLM_ACTION] %s"]
    assert json.loads(call.arguments)["steps"][0]["op"] == "click"


@pytest.mark.asyncio
async def test_covered_control_is_reported_to_jev_but_never_offered():
    policy, llm, client, runtime, context, captured = setup_policy()
    page = page_operations(runtime)
    target = page.get_target(page.export_decision_targets()[0]["target_id"])
    target.actionable = False
    target.decision_state["blocked_by"] = "Cookie consent"
    choose_operation(client, "HANDOFF")
    await policy.invoke(await messages_for(policy, context, captured), tools=LOCAL_TOOLS)
    payload = client.evaluate.call_args.args[0]
    assert payload["state"]["blocked_controls"] == [
        {"label": "销量", "role": "button", "blocked_by": "Cookie consent"}]
    targets = [option["action"] for group, question in payload["questions"].items() if group.startswith("target_")
               for option in question["criteria"].values()]
    assert targets and not any("销量" in text for text in targets)


def test_target_options_carry_observed_state_on_the_wire_only():
    # Jev reads structured options; strings stay in ActionMenu for logs, guards and validation.
    controls = [
        {"target_id": "t1", "name": "4 stars", "role": "checkbox", "region": "filters", "enabled": True,
         "actionable": True, "clickable": True, "decision_state": {
             "tag": "input", "input_type": "checkbox", "checked": False,
             "node_guard": {"document": "d", "node": 1, "expanded": None}}},
        {"target_id": "t2", "name": "Destination", "role": "textbox", "enabled": True, "actionable": True,
         "decision_state": {"tag": "input", "current_value": "Sydney", "node_guard": {"document": "d", "node": 2}}},
    ]
    menu = build_menu(controls, "筛选 4 stars", limit=30, page={"url": "https://example.test/",
                      "page_guard": {"document": "d"}}, page_operations={"read_text"})
    payload = build_request("jev-1.13.0", {"current_intent": "筛选 4 stars"}, menu, controls)
    assert all(isinstance(text, str) for text in menu.criteria.values())
    assert all(isinstance(text, str) for text in payload["questions"]["action"]["criteria"].values())
    options = {option["action"]: option for question in payload["questions"].values()
               if question is not payload["questions"]["action"] for option in question["criteria"].values()}
    check = next(option for action, option in options.items() if action.startswith("SET_CHECKED"))
    assert check == {"action": check["action"], "role": "checkbox", "region": "filters", "checked": False}
    assert options["READ visible page text and metadata for missing task facts"] == {
        "action": "READ visible page text and metadata for missing task facts"}


@pytest.mark.asyncio
async def test_fill_survives_a_later_failed_step_so_jev_can_submit_it():
    # Douban: the LLM filled "活着" then its click on 搜索 timed out. The whole call failed, so the
    # typed value was never bound and Jev could only re-click the stalled button.
    policy, llm, client, runtime, context, captured = setup_policy(goal="在豆瓣搜索“活着”，切换到“图书”结果")
    page = page_operations(runtime)
    box = page.get_target(page.export_decision_targets()[0]["target_id"])
    box.role = "searchbox"
    box.decision_state.update(tag="input", search_like=True, current_value="活着")
    session = context.get_session_ref()
    call = SimpleNamespace(id="llm-douban")
    session.get_state(PHASE)["execution_journal"] = [{"call_id": call.id, "steps": [
        {"index": 0, "op": "fill", "execution_state": "acknowledged"},
        {"index": 1, "op": "click", "execution_state": "dispatched_unknown"},
        {"index": 2, "op": "wait_for_url", "execution_state": "not_started"}]}]
    steps = [{"op": "fill", "target_id": box.target_id, "value": "活着"},
             {"op": "click", "target_id": box.target_id}, {"op": "wait_for_url"}]
    policy.record_execution(SimpleNamespace(tool_call=call, tool_name="browser_batch_interact",
                                            tool_args={"steps": steps}), session, {"success": False})
    choose_operation(client, "HANDOFF")
    await policy.invoke(await messages_for(policy, context, captured), tools=LOCAL_TOOLS)
    payload = client.evaluate.call_args.args[0]
    assert [a["outcome"] for a in payload["state"]["llm_recent_actions"]] == ["ok", "failed", "not_run"]
    assert "PRESS_ENTER" in payload["questions"]["action"]["criteria"]


@pytest.mark.asyncio
async def test_finish_returns_to_llm_even_with_unmapped_fields_and_cannot_certify_task():
    # Live shadow: Jev chose FINISH on the Baidu homepage while exchange_rate was still missing.
    policy, llm, client, runtime, context, captured = setup_policy()
    phase = context.get_session_ref().get_state(PHASE)
    phase["required_fields"] = ["exchange_rate"]
    choose_operation(client, "FINISH")
    result = await policy.invoke(await messages_for(policy, context, captured), tools=TOOLS)
    payload = client.evaluate.call_args.args[0]
    assert not payload["state"]["runtime_progress"]["missing_requirements"]
    assert "FINISH" in payload["questions"]["action"]["criteria"]
    assert "HANDOFF" in payload["questions"]["action"]["criteria"]
    assert result.content == llm.invoke.return_value.content and not result.tool_calls
    llm.invoke.assert_awaited_once()
    assert phase.get("status") != "completed"
    phase["field_coverage"] = ["exchange_rate"]
    runtime.ensure_page_state().decision_snapshot["capture_id"] = "capture-found"
    await policy.invoke(await messages_for(policy, context, {**captured, "semantic_state": {"result_count": 1}}),
                        tools=TOOLS)
    payload = client.evaluate.call_args.args[0]
    assert not payload["state"]["runtime_progress"]["missing_requirements"]
    assert "FINISH" in payload["questions"]["action"]["criteria"]


ENVELOPE = ('{"source":"web","timestamp":"2026-09-29 08:44:41","preferred_response_language":"%s",'
            '"content":"%s","type":"user input","files_updated_by_user":"{}",'
            '"origin_kind":"external_user_authored"}')
BAIDU = "打开百度首页，搜索“人民币 新加坡元 汇率”，返回结果页中显示的汇率数字。"
# The browser subagent's real rewrite of BAIDU: its English plan quotes fragments that are not values.
REWRITE = ('Open the Baidu homepage (https://www.baidu.com), then in the search box type the query '
           '"人民币 新加坡元 汇率" and submit the search. On the search results page, report the rate shown '
           '(e.g. "1人民币 = 0.XXXX 新加坡元").')


@pytest.mark.parametrize("prefix,language", [("你收到一条消息：\n", "zh"), ("You receive a new message:\n", "en")])
def test_user_request_is_unwrapped_whichever_reply_language_the_host_used(prefix, language):
    from openjiuwen.harness.tools.browser_move.decision.intent import normalize_goal

    assert normalize_goal(prefix + ENVELOPE % (language, BAIDU)) == BAIDU


def test_search_terms_come_from_the_user_request_not_the_rewrite():
    from openjiuwen.harness.tools.browser_move.decision.intent import search_values, task_literals

    assert search_values(REWRITE) != ["人民币 新加坡元 汇率"]  # The rewrite alone yields a plan fragment.
    queries, _ = task_literals("You receive a new message:\n" + ENVELOPE % ("en", BAIDU), REWRITE)
    assert queries == ["人民币 新加坡元 汇率"]
    # A subtask keeps only the literals it still names, never another part's value.
    queries, values = task_literals("在京东搜索“鼠标”，在淘宝搜索“键盘”", 'Search Taobao for "键盘"')
    assert queries == ["键盘"] and values == ["键盘"]


@pytest.mark.asyncio
async def test_rewritten_intent_still_gets_the_users_search_term_on_the_menu():
    policy, llm, client, runtime, context, captured = setup_policy(goal=REWRITE)
    phase = context.get_session_ref().get_state(PHASE)
    phase["goal"] = "You receive a new message:\n" + ENVELOPE % ("en", BAIDU)
    page = page_operations(runtime)
    box = page.get_target(page.export_decision_targets()[0]["target_id"])
    box.role = "searchbox"
    box.decision_state.update(tag="input", search_like=True, current_value="")
    choose_operation(client, "HANDOFF")
    await policy.invoke(await messages_for(policy, context, captured), tools=LOCAL_TOOLS)
    payload = client.evaluate.call_args.args[0]
    assert payload["state"]["goal"] == BAIDU
    assert payload["state"]["intent_ambiguous"] is False
    fills = [o["action"] for o in payload["questions"]["target_TYPE_TEXT"]["criteria"].values()]
    assert fills == ['FILL 销量: "人民币 新加坡元 汇率" (do not submit)']


@pytest.mark.asyncio
@pytest.mark.parametrize("length,truncated", [(2200, False), (2201, True)])
async def test_jev_is_told_when_its_view_of_the_page_is_partial(length, truncated):
    # In live runs Jev never chose a reader where the LLM read; it did not know page_text was cut.
    policy, llm, client, runtime, context, captured = setup_policy()
    runtime.ensure_page_state().read_observation = {"text": "x" * length}
    choose_operation(client, "HANDOFF")
    await policy.invoke(await messages_for(policy, context, captured), tools=TOOLS)
    state = client.evaluate.call_args.args[0]["state"]
    assert len(state["page_text"]) == 2200 and state["page_text_truncated"] is truncated
    assert state["cards_observed"] is False


def test_jev_sees_where_the_llm_navigated_and_one_entry_per_pointer_drag():
    # Lazada, 2026-09-29: Jev saw six "page_action"/"mouse_*" entries with no labels and could not
    # tell that the LLM had just opened the cart to check an add.
    policy, llm, client, runtime, context, captured = setup_policy()
    session = context.get_session_ref()
    session.get_state(PHASE)["execution_journal"] = []
    calls = [("browser_page_action", {"op": "navigate", "url": "https://cart.lazada.sg/cart?spm=secret-token"}),
             ("browser_mouse_move_xy", {"x": 1, "y": 2}), ("browser_mouse_down", {}),
             ("browser_mouse_move_xy", {"x": 9}),
             ("browser_page_action", {"op": "read_text"})]
    for index, (tool, args) in enumerate(calls):
        policy.record_execution(SimpleNamespace(tool_call=SimpleNamespace(id=f"llm-{index}"), tool_name=tool,
                                                tool_args=args), session, {"success": True})
    task = next(iter(policy._tasks.values()))
    assert [(a["op"], a.get("url")) for a in task.llm_actions] == [
        ("navigate", "cart.lazada.sg/cart"), ("mouse", None), ("read_text", None)]
