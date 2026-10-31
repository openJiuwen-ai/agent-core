# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Local capabilities, receipt and recovery regressions; fixtures never call remote providers."""

import asyncio
import copy
import json
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from openjiuwen.core.foundation.llm import AssistantMessageChunk
from openjiuwen.harness.tools.browser_move.decision import policy_model as policy_module
from openjiuwen.harness.tools.browser_move.decision.action_space import build_menu
from openjiuwen.harness.tools.browser_move.decision.guard import validate_guard
from openjiuwen.harness.tools.browser_move.decision.policy_model import BrowserPolicyModel, _Observation
from openjiuwen.harness.tools.browser_move.playwright_runtime import execution_journal as journal
from openjiuwen.harness.tools.browser_move.playwright_runtime.phase_contract import constrain_actions, set_phase
from openjiuwen.harness.tools.browser_move.playwright_runtime.probes import (
    build_card_probe_js,
    build_interactive_probe_js,
)
from openjiuwen.harness.tools.browser_move.playwright_runtime.runtime import BrowserRuntimeRail as Rail
from tests.unit_tests.harness.tools.browser_move.test_browser_jev_phase_contract import call, control, session_for
from tests.unit_tests.harness.tools.browser_move.test_browser_jev_policy import (
    choose_operation,
    messages_for,
    setup_policy,
)
from tests.unit_tests.harness.tools.browser_move.test_browser_local_decision_loop import LOCAL_TOOLS
from tests.unit_tests.harness.tools.browser_move.test_browser_september17_contracts import dom_page  # noqa: F401

ZERO = '### Error\nError: "div.c-result[srcid="www_index"] h3:text-is("國立清華大學")" does not match any elements.'


@pytest.mark.parametrize(
    "variant,expected",
    [
        ("original", "rejected_before_dispatch"),
        ("script", "dispatched_unknown"),
        ("executed", "dispatched_unknown"),
        ("prefix", "dispatched_unknown"),
        ("suffix", "dispatched_unknown"),
    ],
)
def test_frozen_x2_native_zero_match(variant, expected):
    state = Rail._build_phase_state("Read landing page")
    session = session_for(state)
    inputs = call("zero", "browser_evaluate" if variant == "script" else "mcp_playwright-official_browser_click")
    inputs.tool_args = {"element": "Observed title", "ref": "div.c-result h3"}
    error = ("Page says: " if variant == "prefix" else "") + ZERO
    if variant == "suffix":
        error += "\n  - performing click action"
    result = {"error": error, **({"executed": True} if variant == "executed" else {})}
    journal.prepare(session, inputs)
    receipt = journal.record_result(session, inputs, {"success": False}, result)
    assert receipt["execution_state"] == expected


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["llm", "hybrid"])
async def test_progressing_stream_survives_previous_whole_call_limit(monkeypatch, mode):
    policy, fallback, _, _, _, _ = setup_policy(mode)
    clock = [1000.0]
    monkeypatch.setattr(
        policy_module,
        "time",
        SimpleNamespace(time=lambda: clock[0], monotonic=lambda: clock[0], perf_counter=time.perf_counter),
    )
    policy._choose = AsyncMock(return_value=(None, {}, 1600.0))
    closed = []

    async def producer(**_):
        try:
            for _ in range(4):
                clock[0] += 21.0
                yield AssistantMessageChunk(content="progress")
        finally:
            closed.append(True)

    fallback.stream = producer
    assert len([chunk async for chunk in policy.stream([])]) == 4
    assert closed == [True]


@pytest.mark.asyncio
@pytest.mark.parametrize("streaming", [True, False])
async def test_shared_deadline_closes_started_request_and_keeps_source(streaming):
    policy, fallback, _, _, _, _ = setup_policy("llm")
    policy._choose = AsyncMock(return_value=(None, {}, time.time() + 0.03))
    closed = []

    async def stream(**_):
        try:
            yield AssistantMessageChunk(content="partial thinking")
            await asyncio.Event().wait()
        finally:
            closed.append(True)

    async def invoke(**_):
        try:
            await asyncio.Event().wait()
        finally:
            closed.append(True)

    fallback.stream, fallback.invoke = stream, invoke
    with pytest.raises(TimeoutError, match="browser_task_deadline_exhausted"):
        if streaming:
            async for chunk in policy.stream([]):
                assert not chunk.tool_calls
        else:
            await policy.invoke([])
    assert closed == [True]


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["first_chunk", "idle_chunk"])
async def test_client_wait_keeps_original_diagnostic(stage):
    policy, fallback, _, _, _, _ = setup_policy("llm")
    policy._choose = AsyncMock(return_value=(None, {}, time.time() + 60))

    async def stream(**_):
        if stage == "idle_chunk":
            yield AssistantMessageChunk(content="first")
        raise TimeoutError(f"stream frame timeout: stage={stage}, chunk_count=1")

    fallback.stream = stream
    with pytest.raises(TimeoutError, match=f"stage={stage}") as error:
        _ = [chunk async for chunk in policy.stream([])]
    assert Rail._model_failure_details(error.value)["stage"] == stage


@pytest.mark.parametrize(
    "error,source,code",
    [
        (TimeoutError("browser_task_deadline_exhausted"), "runtime", "task_deadline_exhausted"),
        (TimeoutError(), "unknown", "model_timeout"),
        (RuntimeError("upstream HTTP 503"), "upstream", "model_provider_unavailable"),
        (TimeoutError("browser_llm_total_timeout"), "local", "model_call_limit_exhausted"),
    ],
)
def test_failure_output_reports_source_without_provider_guess(error, source, code):
    ctx = SimpleNamespace(session=session_for(Rail._build_phase_state("Continue")), retry_attempt=0)
    result = Rail(SimpleNamespace())._structured_model_failure(ctx, error)
    assert result["error"] == code
    assert f'"source":"{source}"' in result["output"]


@pytest.mark.asyncio
async def test_x26_remaining_budget_enters_tool_disabled_summary(monkeypatch):
    state = Rail._build_phase_state("Read remaining detail")
    state.update(deadline_at=time.time() + 14.65, invocation_remaining_s=14.65)
    session = session_for(state)
    model = SimpleNamespace(
        _browser_first_chunk_limit=90, model_client_config=SimpleNamespace(stream_first_chunk_timeout=90)
    )
    rail = Rail(SimpleNamespace(llm_model=model))
    monkeypatch.setattr(rail, "_sync_semantic_progress", lambda _: None)
    ctx = SimpleNamespace(
        session=session,
        inputs=SimpleNamespace(messages=[], tools=["tool"]),
        extra={},
        agent=SimpleNamespace(system_prompt_builder=None),
        request_force_finish=MagicMock(),
    )
    await rail.before_model_call(ctx)
    assert ctx.inputs.tools == []
    assert 14 < model.model_client_config.stream_first_chunk_timeout <= 14.65
    assert state["terminal_reason"] == "task_deadline_insufficient_for_action"
    ctx.request_force_finish.assert_not_called()


# Shared receipts retain work history without promoting it to fresh DOM identity.


def reader_observation():
    return _Observation(
        "task",
        "session",
        time.time() + 90,
        {"current_intent": "Read current items", "phase": {}, "page_position": {}},
        [],
        {
            "page_id": "p",
            "generation_id": "g1",
            "url": "https://shop.test/list",
            "interaction_revision": 1,
            "page_guard": {"document": "doc"},
            "reader_view": {},
        },
    )


def test_reader_identity_survives_unrelated_revision_and_still_distinguishes_scope():
    obs = reader_observation()
    step = {"op": "probe_cards", "max_cards": 12, "viewport_only": False, "include_buttons": False}
    key = BrowserPolicyModel._reader_scope_key(step, obs)
    for revision in range(2, 14):  # X2: 13 reads interleaved with diagnostics/failed clicks.
        obs.page["interaction_revision"] = revision
        assert BrowserPolicyModel._reader_scope_key(step, obs) == key
    for changed in [dict(step, viewport_only=True), dict(step, query="price")]:
        assert BrowserPolicyModel._reader_scope_key(changed, obs) != key
    obs.page["page_guard"]["document"] = "new"
    assert BrowserPolicyModel._reader_scope_key(step, obs) != key


def test_only_known_ack_interaction_reopens_automatic_read_not_attempt_or_script():
    phase = {"execution_journal": []}
    for op, status, expected in [
        ("click", "rejected_before_dispatch", ""),
        ("evaluate", "acknowledged", ""),
        ("scroll", "acknowledged", ""),
        ("click", "acknowledged", "call:0"),
    ]:
        phase["execution_journal"] = [
            {"call_id": "call", "source": "https://shop.test/list", "steps": [{"op": op, "execution_state": status}]}
        ]
        assert BrowserPolicyModel._reader_interaction(phase, "https://shop.test/list") == expected


@pytest.mark.asyncio
async def test_reader_receipt_survives_guard_rejection_and_repeated_diagnostics():
    policy, _, client, runtime, context, captured = setup_policy(goal="Read details")
    page = runtime.ensure_page_state()
    page.decision_snapshot["page_guard"] = {"document": "doc-a"}
    await messages_for(policy, context, captured)
    policy.record_execution(
        SimpleNamespace(
            tool_call=SimpleNamespace(id="llm-read"), tool_name="browser_page_action", tool_args={"op": "read_text"}
        ),
        context.get_session_ref(),
        {"success": True},
    )
    choose_operation(client, "HANDOFF")
    for i in range(13):
        page.mark_interaction()
        policy.record_execution(
            SimpleNamespace(
                tool_call=SimpleNamespace(id=f"llm-denied-{i}"),
                tool_name="browser_batch_interact",
                tool_args={"steps": []},
            ),
            context.get_session_ref(),
            {"success": False, "executed": False, "observation_invalidated": True},
        )
        page.decision_snapshot["capture_id"] = f"recaptured-{i}"
        await policy.invoke(await messages_for(policy, context, captured), tools=LOCAL_TOOLS)
        assert "EXTRACT_TEXT" not in client.evaluate.call_args.args[0]["questions"]["action"]["criteria"]
    assert next(iter(policy._tasks.values())).receipts[0]["reader_scope"]
    # Explicit LLM reads are still passed through; the policy filter only builds Jev's menu.
    policy.record_execution(
        SimpleNamespace(
            tool_call=SimpleNamespace(id="llm-explicit-reread"),
            tool_name="browser_page_action",
            tool_args={"op": "read_text"},
        ),
        context.get_session_ref(),
        {"success": True},
    )
    assert len(next(iter(policy._tasks.values())).receipts) == 2


def test_navigation_recovery_preserves_full_url_and_source_identity():
    obs = reader_observation()
    step = {"op": "navigate", "url": "https://shop.test/item?id=123&variant=red"}
    key = BrowserPolicyModel._binding_recovery_key(step, {}, obs)
    assert key
    obs.page["interaction_revision"] = 77
    assert BrowserPolicyModel._binding_recovery_key(step, {}, obs) == key
    assert BrowserPolicyModel._binding_recovery_key(dict(step, url=step["url"] + "2"), {}, obs) != key
    obs.page["page_guard"]["document"] = "another-document"
    assert BrowserPolicyModel._binding_recovery_key(step, {}, obs) != key


@pytest.mark.asyncio
async def test_first_link_uses_same_recipe_and_two_rejections_handoff_without_global_recovery():
    policy, fallback, client, runtime, context, captured = setup_policy(goal="打开第一条结果并读取标题")
    page = runtime.ensure_page_state()
    page.decision_snapshot["page_guard"] = {"document": "doc-a"}
    page.card_read = {
        "recipe": {
            "max_cards": 9,
            "viewport_only": False,
            "include_buttons": False,
            "query": "",
            "generation_id": page.generation_id,
            "selector_cache_records": [],
        }
    }
    cards = {
        "url": page.url,
        "cards": [
            {
                "title": "Selected document",
                "primary_link": "https://docs.test/?id=1",
                "order_known": True,
                "result_index": 1,
                "region": "main_result",
                "is_ad": False,
            }
        ],
    }
    choose_operation(client, "NAVIGATE")
    for i in range(2):
        page.register_cards(copy.deepcopy(cards))
        page.decision_snapshot["capture_id"] = f"fresh-{i}"
        page.read_observation = {"text": str(i), "url": page.url, "interaction_revision": page.interaction_revision}
        result = await policy.invoke(await messages_for(policy, context, captured), tools=LOCAL_TOOLS)
        assert result.tool_calls, result.metadata
        call_ = result.tool_calls[0]
        inputs = SimpleNamespace(tool_call=call_, tool_name=call_.name, tool_args=json.loads(call_.arguments))
        guard = policy._guards[call_.id]
        assert guard.first_result["probe"]["max_cards"] == 9
        await validate_guard(runtime, guard, inputs, context.get_session_ref())
        assert '"max_cards": 9' in runtime.call_playwright_run_code_unsafe.call_args.args[0]
        policy.record_execution(
            inputs, context.get_session_ref(), {"success": False, "executed": False, "observation_invalidated": True}
        )
    count = client.evaluate.await_count
    result = await policy.invoke(await messages_for(policy, context, captured), tools=LOCAL_TOOLS)
    assert not result.tool_calls and client.evaluate.await_count == count
    assert "repeated_target_invalidation" in fallback.invoke.call_args.kwargs["messages"][-1].content
    choose_operation(client, "HANDOFF")
    for i in range(3):
        policy.record_execution(
            SimpleNamespace(
                tool_call=SimpleNamespace(id=f"llm-read-{i}"),
                tool_name="browser_page_action",
                tool_args={"op": "snapshot"},
            ),
            context.get_session_ref(),
            {"success": True},
        )
        page.decision_snapshot.update(capture_id=f"unrelated-{i}", page_text=str(i))
        page.read_observation["text"] = f"diagnostic-{i}"
        await policy.invoke(await messages_for(policy, context, captured), tools=LOCAL_TOOLS)
        assert "NAVIGATE" not in client.evaluate.call_args.args[0]["questions"]["action"]["criteria"]


def test_invalidating_cards_retains_content_but_removes_target():
    _, _, _, runtime, _, _ = setup_policy()
    page = runtime.ensure_page_state()
    page.register_cards(
        {
            "url": page.url,
            "cards": [
                {
                    "title": "Item",
                    "selector_hint": "#item",
                    "selector_hint_validated": True,
                    "match_count": 1,
                    "primary_link": "https://shop.test/item",
                }
            ],
        }
    )
    target = page.export()["cards"][0]["target_id"]
    page.invalidate_listing()
    assert page.export()["cards"][0]["title"] == "Item"
    assert page.export()["listing_stale"] and not page.export()["cards_observed"]
    assert page.get_target(target) is None


def field(name="Quantity", readonly=False, role="spinbutton", value="2"):
    item = control("field", name, 1, tag="input", input_type="number", current_value=value, readonly=readonly)
    item["role"] = role
    return item


def test_bound_numeric_input_and_readonly_have_distinct_capabilities():
    item = field(readonly=True)
    menu = build_menu([item], "Read Quantity", limit=10)
    assert [s["op"] for s in menu.steps.values()] == ["extract_value"]
    item["decision_state"]["readonly"] = False
    binding = {"document": "doc", "node": 1, "value": "3"}
    menu = build_menu([item], "Apply Quantity", limit=10, field_bindings=[binding])
    assert any(s["op"] == "fill" and s["value"] == "3" for s in menu.steps.values())
    assert not any(s["op"] == "press" for s in menu.steps.values())
    item["decision_state"]["current_value"] = "3"
    menu = build_menu([item], "Apply Quantity", limit=10, field_bindings=[binding])
    assert any(s.get("key") == "Enter" for s in menu.steps.values())
    assert not any(s["op"] == "fill" for s in menu.steps.values())


def test_escape_requires_focused_expanded_target_and_intent():
    item = field(role="combobox")
    item["decision_state"].update(focused=True)
    item["decision_state"]["node_guard"]["expanded"] = "true"
    menu = build_menu([item], "Close the current dropdown", limit=10)
    assert any(s.get("key") == "Escape" for s in menu.steps.values())
    item["decision_state"]["focused"] = False
    assert not any(s.get("key") == "Escape" for s in build_menu([item], "Close dropdown", limit=10).steps.values())


def test_viewport_and_full_page_reads_have_separate_work_identity():
    obs = reader_observation()
    obs.page["page_position"] = {"pixels_above": 900, "pixels_below": 1200}
    obs.state["page_position"] = obs.page["page_position"]
    menu = build_menu([], "Read results", limit=10, page=obs.page, probe_tools=("browser_probe_cards",))
    reads = [s for s in menu.steps.values() if s["op"] == "probe_cards"]
    assert len(reads) == 2 and {s["viewport_only"] for s in reads} == {True, False}
    keys = {s["viewport_only"]: BrowserPolicyModel._reader_scope_key(s, obs) for s in reads}
    obs.page["interaction_revision"] += 1
    obs.state["page_position"]["pixels_above"] = 1800
    for s in reads:
        assert (BrowserPolicyModel._reader_scope_key(s, obs) != keys[s["viewport_only"]]) == s["viewport_only"]


def test_observed_navigation_is_not_lost_or_allowed_to_evade_local_target_binding():
    obs = reader_observation()
    obs.page.update(cards_observed=True, cards=[{"title": "Item", "primary_link": "https://shop.test/item?id=1"}])
    item = field()
    menu = build_menu(
        [item], "Read the selected item", limit=10, page=obs.page, allow_page_actions=True, page_operations={"navigate"}
    )
    assert any(s.get("_observed_link") for s in menu.steps.values())
    phase = Rail._build_phase_state("Read field")
    set_phase(phase, {"objective": "Read Quantity", "target_ids": ["field"]}, [item])
    constrain_actions(menu, phase, [item])
    assert not any(s["op"] == "navigate" for s in menu.steps.values())


@pytest.mark.asyncio
async def test_target_reader_compiles_one_normal_tool_call_and_is_retired_after_read():
    policy, _, client, runtime, context, captured = setup_policy(goal="Read the selected label")
    choose_operation(client, "READ_TARGET_TEXT")
    result = await policy.invoke(await messages_for(policy, context, captured), tools=LOCAL_TOOLS)
    tool = result.tool_calls[0]
    args = json.loads(tool.arguments)
    assert tool.name == "browser_batch_interact" and len(args["steps"]) == 1
    assert args["steps"][0]["op"] == "extract_text"
    assert "selector" not in args["steps"][0]
    policy.record_execution(
        SimpleNamespace(tool_call=tool, tool_name=tool.name, tool_args=args),
        context.get_session_ref(),
        {"success": True},
    )
    page = runtime.ensure_page_state()
    page.decision_snapshot["capture_id"] = "after-read"
    choose_operation(client, "HANDOFF")
    await policy.invoke(await messages_for(policy, context, captured), tools=LOCAL_TOOLS)
    assert "READ_TARGET_TEXT" not in client.evaluate.call_args.args[0]["questions"]["action"]["criteria"]


def eval_probe(page, script):
    return page.evaluate("async code => await eval('(' + code + ')')({evaluate: (fn, arg) => fn(arg)})", script)


def test_local_dom_probe_reads_readonly_and_finds_labeled_row_controls(dom_page):  # noqa: F811 - shared pytest fixture
    dom_page.set_content("""<main><article><h2>Selected keyboard</h2>
        <input aria-label="Quantity" readonly value="2"><button aria-label="Remove item">×</button>
        <button><svg width="20" height="20"></svg></button></article>
        <article><h2>Other item</h2><button aria-label="Remove other item">×</button></article></main>""")
    result = eval_probe(
        dom_page, build_interactive_probe_js(max_items=20, query="Selected keyboard", decision_mode=True)
    )
    items = result["elements"]
    assert {i["accessible_name"] for i in items} >= {"Quantity", "Remove item"}
    assert not any(i["accessible_name"] == "Remove other item" for i in items)
    assert not any(not (i["accessible_name"] or i["text"]) for i in items)
    quantity = next(i for i in items if i["accessible_name"] == "Quantity")
    assert quantity["decision_state"]["readonly"] and quantity["decision_state"]["node_guard"]
    assert "Selected keyboard" in quantity["decision_state"]["nearby_text"]


@pytest.mark.parametrize(
    "change,expected",
    [("value", True), ("other_field", True), ("readonly", True), ("replacement", False), ("label", False)],
)
def test_local_dom_reader_checks_identity_but_reads_changed_value(dom_page, change, expected):  # noqa: F811 - shared pytest fixture
    dom_page.set_content(
        '<form><label for="sales">Quantity</label><input id="sales" value="2" readonly>'
        '<input id="other" value="old"></form>'
    )
    result = eval_probe(dom_page, build_interactive_probe_js(max_items=10, decision_mode=True))

    async def script_for_reader():
        policy, _, client, runtime, context, captured = setup_policy(goal="Read Quantity")
        runtime.ensure_page_state().register_interactives({**result, "url": "https://example.test/"})
        page = runtime.ensure_page_state()
        page.decision_snapshot.update(url=page.url)
        choose_operation(client, "READ_VALUE")
        message = await policy.invoke(await messages_for(policy, context, captured), tools=LOCAL_TOOLS)
        call_ = message.tool_calls[0]
        await validate_guard(
            runtime,
            policy._guards[call_.id],
            SimpleNamespace(tool_call=call_, tool_name=call_.name, tool_args=json.loads(call_.arguments)),
            context.get_session_ref(),
        )
        return runtime.call_playwright_run_code_unsafe.call_args.args[0]

    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=1) as pool:
        script = pool.submit(asyncio.run, script_for_reader()).result(timeout=10)
    dom_page.evaluate(
        """change => {
      const el = document.querySelector('#sales');
      if (change === 'value') el.value = '3';
      if (change === 'other_field') document.querySelector('#other').value = 'new';
      if (change === 'readonly') el.readOnly = false;
      if (change === 'replacement') el.replaceWith(el.cloneNode(true));
      if (change === 'label') el.setAttribute('aria-label', 'Different field');
    }""",
        change,
    )
    checked = dom_page.evaluate(
        """async code => await eval('(' + code + ')')({url:()=>'https://example.test/',
      locator:selector=>({count:async()=>document.querySelectorAll(selector).length,
        evaluate:async(fn,args)=>fn(document.querySelector(selector),args)})})""",
        script,
    )
    assert checked["ok"] is expected


def test_local_dom_card_range_truncation_and_async_new_rows(dom_page):  # noqa: F811 - shared pytest fixture
    dom_page.set_content(
        "<main>"
        + "".join(
            f'<article class="product-card" style="height:180px;width:500px">'
            f'<h2><a href="https://shop.test/{i}">Keyboard {i}</a></h2>'
            f"<p>Mechanical keyboard product {i} $99</p></article>"
            for i in range(16)
        )
        + "</main>"
    )
    full = eval_probe(dom_page, build_card_probe_js(max_cards=12, viewport_only=False))
    assert full["truncated"] and full["observed_count"] > len(full["cards"]) == 12
    assert not full["read_scope"]["viewport_only"]
    dom_page.evaluate("window.scrollTo(0, 1500)")
    viewport = eval_probe(dom_page, build_card_probe_js(max_cards=12, viewport_only=True))
    assert viewport["read_scope"]["viewport_only"] and viewport["cards"]
    assert viewport["cards"][0]["title"] != full["cards"][0]["title"]
    # An explicit fixed probe still reads asynchronous DOM changes on the same URL.
    dom_page.evaluate("""() => {document.querySelector('main').insertAdjacentHTML('afterbegin',
      '<article class="product-card" style="height:180px;width:500px"><h2><a href="https://shop.test/new">'
      + 'New keyboard</a></h2><p>Mechanical keyboard new $88</p></article>')}""")
    refreshed = eval_probe(dom_page, build_card_probe_js(max_cards=12, viewport_only=False))
    assert refreshed["cards"][0]["title"] == "New keyboard"


@pytest.mark.asyncio
@pytest.mark.parametrize("following", ["none", "failed_wait", "fill"])
async def test_llm_bound_read_ack_is_shared_but_later_mutation_reopens_it(following):
    policy, _, client, runtime, context, captured = setup_policy(goal="Read current quantity")
    page = runtime.ensure_page_state()
    page.decision_snapshot["page_guard"] = {"document": "doc-a"}
    target = page.get_target(page.export_decision_targets()[0]["target_id"])
    target.name, target.role = "Quantity", "spinbutton"
    target.decision_state.update(tag="input", input_type="number", current_value="2")
    await messages_for(policy, context, captured)
    steps = [{"op": "extract_value", "target_id": target.target_id}]
    results = [{"ok": True, "executed": True, "value": "2"}]
    if following == "failed_wait":
        steps.append({"op": "wait_for_text", "text": "ready", "optional": True})
        results.append({"ok": False, "executed": True, "error": "Timeout"})
    elif following == "fill":
        steps.append({"op": "fill", "target_id": target.target_id, "value": "3"})
        results.append({"ok": True, "executed": True})
    inputs = SimpleNamespace(
        tool_name="browser_batch_interact", tool_call=SimpleNamespace(id="llm-read-value"), tool_args={"steps": steps}
    )
    session = context.get_session_ref()
    journal.prepare(session, inputs, runtime)
    outcome = {"success": following != "failed_wait"}
    inputs.tool_result = {"steps": results}
    journal.record_result(session, inputs, outcome, inputs.tool_result)
    policy.record_execution(inputs, session, outcome)
    policy.record_execution(inputs, session, outcome)  # Repeated rail notification cannot duplicate a receipt.
    task = next(iter(policy._tasks.values()))
    assert not task.failed_actions
    assert all(action["outcome"] == "ok" for action in task.llm_actions if action["op"] == "extract_value")
    choose_operation(client, "HANDOFF")
    await policy.invoke(await messages_for(policy, context, captured), tools=LOCAL_TOOLS)
    criteria = client.evaluate.call_args.args[0]["questions"]["action"]["criteria"]
    assert ("READ_VALUE" in criteria) is (following == "fill")
    receipts = [r for r in next(iter(policy._tasks.values())).receipts if r.get("operation") == "extract_value"]
    assert len(receipts) == (0 if following == "fill" else 1)


@pytest.mark.asyncio
@pytest.mark.parametrize("capture_before_receipt", [True, False])
async def test_scoped_llm_probe_reenters_with_observation_committed_before_callback(capture_before_receipt):
    policy, _, client, runtime, context, captured = setup_policy()
    page = runtime.ensure_page_state()
    choose_operation(client, "CLICK")
    for i in range(2):
        page.decision_snapshot["capture_id"] = f"attempt-{i}"
        page.read_observation = {"text": str(i), "url": page.url}
        response = await policy.invoke(await messages_for(policy, context, captured), tools=LOCAL_TOOLS)
        policy.record_execution(
            SimpleNamespace(tool_call=response.tool_calls[0]),
            context.get_session_ref(),
            {"success": False, "executed": False, "observation_invalidated": True},
        )
    response = await policy.invoke(await messages_for(policy, context, captured), tools=LOCAL_TOOLS)
    assert response.metadata["browser_policy"]["reason"] == "repeated_target_invalidation"
    if capture_before_receipt:
        page.decision_snapshot["capture_id"] = "llm-targeted-probe"
    policy.record_execution(
        SimpleNamespace(
            tool_call=SimpleNamespace(id="llm-probe"),
            tool_name="browser_probe_interactives",
            tool_args={"query": "销量"},
        ),
        context.get_session_ref(),
        {"success": True},
    )
    if not capture_before_receipt:
        page.decision_snapshot["capture_id"] = "llm-targeted-probe"
    response = await policy.invoke(await messages_for(policy, context, captured), tools=LOCAL_TOOLS)
    assert response.tool_calls, response.metadata
    assert json.loads(response.tool_calls[0].arguments)["steps"][0]["op"] == "click"
