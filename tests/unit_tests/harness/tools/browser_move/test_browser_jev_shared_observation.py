# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""September 24 evening failures, shared capabilities and bounded Jev re-entry."""

import asyncio
import copy
import json
import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openjiuwen.harness.tools.browser_move.decision.action_space import build_menu
from openjiuwen.harness.tools.browser_move.decision.guard import (
    DecisionGuard,
    canonical_arguments,
    validate_guard,
)
from openjiuwen.harness.tools.browser_move.decision.intent import search_values
from openjiuwen.harness.tools.browser_move.decision.jev_client import (
    DecisionUnavailable,
)
from openjiuwen.harness.tools.browser_move.playwright_runtime import (
    execution_journal as journal,
)
from openjiuwen.harness.tools.browser_move.playwright_runtime.evidence import (
    explicit_acceptance,
    observe_acceptance,
)
from openjiuwen.harness.tools.browser_move.playwright_runtime.page_state import (
    BrowserPageState,
)
from openjiuwen.harness.tools.browser_move.playwright_runtime.phase_contract import (
    constrain_actions,
    set_phase,
    unresolved_writes,
)
from openjiuwen.harness.tools.browser_move.playwright_runtime.probes import (
    build_card_probe_js,
    build_interactive_probe_js,
)
from openjiuwen.harness.tools.browser_move.playwright_runtime.runtime import (
    BrowserRuntimeRail as Rail,
)
from openjiuwen.harness.tools.browser_move.playwright_runtime.runtime_tools import (
    BrowserProbeCardsTool,
)
from openjiuwen.harness.tools.browser_move.playwright_runtime.site_profiles import (
    site_profiles_for_url,
)
from tests.unit_tests.harness.tools.browser_move.test_browser_jev_phase_contract import (
    control,
    session_for,
)
from tests.unit_tests.harness.tools.browser_move.test_browser_jev_policy import (
    messages_for,
    setup_policy,
)
from tests.unit_tests.harness.tools.browser_move.test_browser_p0_closure import (
    search_state,
)
from tests.unit_tests.harness.tools.browser_move.test_browser_page_state import (
    _make_bare_runtime,
)
from tests.unit_tests.harness.tools.browser_move.test_browser_runtime_rail import _declare_output
from tests.unit_tests.harness.tools.browser_move.test_browser_september17_contracts import (
    dom_page as shared_dom_page,
)

dom_page = shared_dom_page


def invoke_args(name, args, call_id="call_search"):
    return SimpleNamespace(tool_name=name, tool_args=args, tool_call=SimpleNamespace(id=call_id))


def first_card(**changes):
    return {
        "title": "清华大学",
        "primary_link": "https://www.tsinghua.edu.cn/",
        "order_known": True,
        "result_index": 1,
        "region": "main_result",
        "is_ad": False,
        **changes,
    }


def choose_matching(client, needle):
    from tests.unit_tests.harness.tools.browser_move.test_browser_jev_policy import grouped_answer

    async def evaluate(payload, **kwargs):
        group, key = next((head.removeprefix("target_"), key)
                          for head, question in payload["questions"].items() if head.startswith("target_")
                          for key, option in question["criteria"].items() if needle in option["action"])
        return grouped_answer(payload, group, key)

    client.evaluate.side_effect = evaluate


@pytest.mark.parametrize(
    "goal,expected",
    [
        ("搜索“Python教程”，切换到“最多播放”，返回第一条", ["Python教程"]),
        ("搜索“蓝牙耳机”，按“销量”排序", ["蓝牙耳机"]),
        ('search for "mechanical keyboard" and sort by "sales"', ["mechanical keyboard"]),
        ("搜索“鼠标”和“键盘”", ["鼠标", "键盘"]),
        ("搜索“鼠标”，然后搜索“键盘”", ["鼠标", "键盘"]),
    ],
)
def test_literals_are_bound_to_search_role(goal, expected):
    assert search_values(goal) == expected


def test_populated_search_can_use_explicit_new_query_and_then_submit():
    field = control("search", "Search", 1, tag="input", search_like=True, current_value="old query")
    field["role"] = "searchbox"
    menu = build_menu([field], "搜索“Python教程”，切换到“最多播放”", limit=30)
    assert any(s.get("value") == "Python教程" for s in menu.steps.values())
    assert not any(s["op"] == "press" for s in menu.steps.values())
    field["decision_state"]["current_value"] = "Python教程"
    menu = build_menu([field], "搜索“Python教程”，切换到“最多播放”", limit=30)
    assert [s["op"] for s in menu.steps.values()] == ["press"]


def test_multiple_actual_queries_do_not_become_implicit_field_values():
    field = control("search", "Search", 1, tag="input", search_like=True, current_value="")
    field["role"] = "searchbox"
    assert not build_menu([field], "搜索“鼠标”和“键盘”", limit=30).steps


def test_optional_click_intent_keeps_fixed_reads_available():
    state = Rail._build_phase_state("打开第一条结果")
    set_phase(state, {"objective": "打开第一条结果", "allowed_operations": ["click"]}, [])
    menu = build_menu([], "打开第一条结果", limit=30,
                      page={"page_guard": {"document": "d"}, "url": "https://search.test/?q=example"},
                      probe_tools=("browser_probe_cards",))
    constrain_actions(menu, state, [])
    assert any(step["op"] == "probe_cards" for step in menu.steps.values())


@pytest.mark.asyncio
async def test_quoted_sort_does_not_require_a_phase_to_enter_jev():
    policy, llm, client, runtime, context, captured = setup_policy(goal="搜索“Python教程”，切换到“最多播放”")
    await policy.invoke(await messages_for(policy, context, captured), tools=[{"name": "browser_batch_interact"}])
    client.evaluate.assert_awaited_once()
    llm.invoke.assert_not_awaited()


@pytest.mark.parametrize(
    "html,selector,kind,search",
    [
        ('<form><input name="wd"><input id="submit" type="submit" value="百度一下"></form>', "#submit", "", True),
        ('<form><input type="search"><button id="submit" hidden>搜索</button></form>', "#submit", "", True),
        ('<div class="search-input"><input type="search"><button id="submit"></button></div>', "#submit", "", True),
        ('<div class="sort-tabs"><button id="views">最多播放</button></div>', "#views", "sort_tab", False),
        (
            '<div><button>综合排序</button><button id="views">最多播放</button><button>最新发布</button></div>',
            "#views",
            "sort_tab",
            False,
        ),
        ('<form><input name="email"><button id="submit">Submit</button></form>', "#submit", "", False),
        ('<form><input name="q"><input type="password"><button id="submit">Save</button></form>', "#submit", "", False),
        ('<div class="search-results"><button id="cart">加入购物车</button></div>', "#cart", "", False),
    ],
)
def test_runtime_capabilities_include_hidden_and_unnamed_controls_without_promoting_business(
    dom_page, html, selector, kind, search
):
    dom_page.set_content(html)
    data = dom_page.evaluate(
        "async code => await eval('(' + code + ')')({evaluate:(fn,arg)=>fn(arg)})",
        build_interactive_probe_js(decision_mode=True, target_selectors=[selector]),
    )
    target = data["capabilities"][0]
    assert target["decision_state"]["search_like"] is search
    assert target["decision_state"]["node_guard"]["node"]
    if kind:
        assert target["kind"] == kind
    if selector == "#cart":
        assert target["decision_state"]["effect"]["domain"] == "cart"


@pytest.mark.asyncio
async def test_pure_llm_metadata_captures_shared_control_facts_without_extra_rpc():
    runtime = _make_bare_runtime()
    runtime.call_playwright_run_code_unsafe = AsyncMock(return_value={"ok": True})
    await runtime.capture_browser_metadata(include_decision=True)
    runtime.call_playwright_run_code_unsafe.assert_awaited_once()
    script = runtime.call_playwright_run_code_unsafe.call_args.args[0]
    assert '"decision_mode": true' in script and '"max_items": 100' in script


@pytest.mark.asyncio
@pytest.mark.parametrize("model_source", ["call_01a0d2ff7a7b77b38f023f67", "jev_search"])
async def test_exact_native_target_enrichment_breaks_search_timeout_deadlock_for_either_model(model_source):
    runtime = _make_bare_runtime()
    page = runtime.ensure_page_state()
    page.observe(url="https://www.baidu.com/")
    page.register_ax_snapshot('- button "百度一下" [ref=e12]')
    target = next(t for t in page._targets.values() if t.ref == "e12")

    async def materialize(value):
        page.update_target_locator(value.target_id, {"selector": "#search"})
        return value

    runtime._materialize_ax_target = AsyncMock(side_effect=materialize)
    runtime.call_playwright_run_code_unsafe = AsyncMock(
        return_value={
            "ok": True,
            "url": page.url,
            "capabilities": [
                {
                    "selector": "#search",
                    "kind": "search",
                    "role": "button",
                    "decision_state": {
                        "search_like": True,
                        "tag": "input",
                        "node_guard": {"document": "doc", "node": 12},
                    },
                }
            ],
        }
    )
    inputs = invoke_args(
        "browser_batch_interact", {"steps": [{"op": "click", "target_id": target.target_id}]}, call_id=model_source
    )
    await runtime.enrich_action_capabilities(inputs)
    state = Rail._build_phase_state("搜索天气")
    session = session_for(state)
    journal.prepare(session, inputs, runtime)
    journal.record_result(session, inputs, {"success": False}, {"steps": [{"ok": False, "error": "Timeout 2500ms"}]})
    assert state["execution_journal"][0]["steps"][0]["impact"] == "local_ui"
    assert not unresolved_writes(state)
    assert state["execution_journal"][0]["execution_state"] == "dispatched_unknown"  # Never fabricate success.
    await runtime.enrich_action_capabilities(inputs)
    runtime.call_playwright_run_code_unsafe.assert_awaited_once()  # Already captured facts are reused.


def evidence_pair():
    state = search_state("")
    scope = {"page_id": "page1", "generation_id": "g0", "interaction_revision": 4}
    base = {"query_id": state["task_id"], "source": state["last_page"]["url"], "observation_scope": scope}
    state["structured_evidence"] = [
        {
            **base,
            "kind": "interactive_probe",
            "values": {"sort_state": "销量"},
            "provenance": {"sort_state": {"selection_source": "aria-selected"}},
        },
        {
            **copy.deepcopy(base),
            "kind": "card_probe",
            "cards": [first_card(title="蓝牙耳机", primary_link="https://shop.test/1")],
        },
    ]
    return state


def test_split_sort_and_first_card_reads_close_the_actual_keyboard_gap():
    state = evidence_pair()
    assert {r["id"]: r["status"] for r in explicit_acceptance(state)} == {
        "sort:sales": "satisfied",
        "first_result:sales": "satisfied",
    }


@pytest.mark.parametrize(
    "impact,url,expected",
    [
        ("local_ui", "https://www.baidu.com/s?wd=weather", "verified"),
        ("business", "https://www.baidu.com/s?wd=weather", "dispatched_unknown"),
        ("unknown", "https://www.baidu.com/s?wd=weather", "dispatched_unknown"),
        ("local_ui", "https://other.test/s?wd=weather", "dispatched_unknown"),
        ("local_ui", "https://www.baidu.com/s?wd=wrong", "dispatched_unknown"),
    ],
)
def test_search_observation_can_reconcile_only_bound_local_effects(impact, url, expected):
    step = {"op": "click", "impact": impact, "execution_state": "dispatched_unknown", "expected_query": "weather"}
    state = {
        "phase_observation_sequence": 2,
        "execution_journal": [
            {
                "call_id": "click",
                "source": "https://www.baidu.com/",
                "steps": [step],
                "impact": impact,
                "dispatch_observation_sequence": 1,
                "execution_state": "dispatched_unknown",
            }
        ],
    }
    journal.reconcile_observation(state, {"capture_id": "results", "url": url, "controls": []})
    assert step["execution_state"] == expected
    if expected == "verified":
        assert step["evidence_ref"]["kind"] == "search_query_observed"


def test_primitive_link_navigation_records_actual_landing_title_after_redirect():
    state = Rail._build_phase_state("打开百度首页，搜索清华大学，进入第一条结果，返回页面标题")
    _declare_output(state, ['title'], entity='page', variants=('default',))
    source = "https://www.baidu.com/s?wd=清华大学"
    state["last_page"] = {"url": source}
    state["structured_evidence"] = [{"source": source, "cards": [first_card()]}]
    result = {
        "ok": True,
        "executed": True,
        "execution_mode": "primitive",
        "metrics": {"tool_name": "browser_navigate"},
        "page_state": {
            "page_id": "page",
            "generation_id": "g4",
            "interaction_revision": 4,
            "url": "https://www.tsinghua.edu.cn/",
            "title": "清华大学",
        },
    }
    Rail._record_structured_evidence(
        state,
        result,
        tool_name="browser_batch_interact",
        tool_args={"steps": [{"op": "click", "target_id": "first"}], "_runtime_source_url": source},
    )
    assert any(slot["field"] == "title" and slot["value"] == "清华大学" for slot in state["evidence_slots"])
    assert any(record.get("destination_verified") for record in state["structured_evidence"])


def test_resolving_one_effect_neither_blocks_reads_nor_clears_other_effect():
    state = Rail._build_phase_state("检查待核对效果")
    state["execution_journal"] = [
        dict(call_id=key, impact="business", execution_state="dispatched_unknown") for key in ("one", "two")
    ]
    session = session_for(state)
    for _ in range(3):
        Rail._consume_phase_budget(session, "browser_phase", {"op": "verify"})
    state["execution_journal"][0]["execution_state"] = "verified"
    Rail._consume_phase_budget(session, "browser_phase", {"op": "verify"})
    assert state["execution_journal"][1]["execution_state"] == "dispatched_unknown"


@pytest.mark.asyncio
async def test_capability_observation_cannot_overrun_task_deadline_and_dispatch(monkeypatch):
    runtime = _make_bare_runtime()
    now = [time.time()]
    clock = SimpleNamespace(**vars(time))
    clock.time = lambda: now[0]
    monkeypatch.setattr("openjiuwen.harness.tools.browser_move.playwright_runtime.runtime.time", clock)

    async def slow_observation(_):
        # Wall-clock expiry is deterministic even if the Windows event-loop timer fires early.
        now[0] += 1
        await asyncio.sleep(1)

    runtime.enrich_action_capabilities = AsyncMock(side_effect=slow_observation)
    state = Rail._build_phase_state("搜索天气")
    state["deadline_at"] = now[0] + 0.02
    session = session_for(state)
    inputs = invoke_args(
        "browser_batch_interact", {"generation_id": "g0", "steps": [{"op": "click", "target_id": "x"}]}
    )
    context = SimpleNamespace(inputs=inputs, session=session, extra={}, agent=None)
    with pytest.raises(ValueError, match="deadline_exhausted_during_observation"):
        await Rail(runtime)._prepare_tool_call(context)
    assert not state.get("execution_journal")


@pytest.mark.parametrize(
    "change",
    ["mutation", "new_page", "new_generation", "new_query", "new_source", "no_scope", "wrong_sort", "ad", "second"],
)
def test_sort_card_join_rejects_unrelated_or_stale_records(change):
    state = evidence_pair()
    record = state["structured_evidence"][1]
    if change == "mutation":
        record["observation_scope"]["interaction_revision"] += 1
    elif change == "new_page":
        record["observation_scope"]["page_id"] = "resumed_page"
    elif change == "new_generation":
        record["observation_scope"]["generation_id"] = "g1"
    elif change == "new_query":
        record["query_id"] = "other_task"
    elif change == "new_source":
        record["source"] += "&p=2"
    elif change == "no_scope":
        record.pop("observation_scope")
    elif change == "wrong_sort":
        record["cards"][0]["sort_state"] = "最新发布"
    elif change == "ad":
        record["cards"][0]["is_ad"] = True
    else:
        record["cards"][0]["result_index"] = 2
    assert next(r for r in explicit_acceptance(state) if r["kind"] == "first_result")["status"] == "unknown"


def test_runtime_stamps_and_retains_split_observations():
    state = search_state("")
    state["requirements_source"] = "explicit"
    state["structured_evidence"] = []
    source = state["last_page"]["url"]
    page = BrowserPageState()
    page.observe(url=source)
    observe_acceptance(
        state,
        {
            "url": source,
            "capture_id": "selected",
            "page": page.export_summary(),
            "controls": [{"kind": "sort_tab", "name": "销量", "selected": True}],
        },
    )
    Rail._record_structured_evidence(
        state,
        {
            "ok": True,
            "url": source,
            "page_state": page.export_summary(),
            "cards": [first_card(title="蓝牙耳机", primary_link="https://shop.test/item")],
        },
        tool_name="browser_probe_cards",
        tool_args={},
    )
    assert all(r["status"] == "satisfied" for r in explicit_acceptance(state))


def test_reads_and_optional_verify_do_not_create_a_separate_recovery_allowance():
    state = Rail._build_phase_state("查看购物车数量")
    state["execution_journal"] = [
        {"call_id": "uncertain", "execution_state": "dispatched_unknown", "impact": "business"}
    ]
    session = session_for(state)
    for _ in range(3):
        Rail._consume_phase_budget(session, "browser_probe_interactives", {})
    assert not state.get("verification_recovery_counts")
    for index in range(3):
        assert Rail._consume_phase_budget(session, "browser_phase", {"op": "verify"}) == "management"
        state["execution_journal"].append(
            {"call_id": f"sort-{index}", "execution_state": "acknowledged", "impact": "local_ui"}
        )
    Rail._consume_phase_budget(session, "browser_phase", {"op": "verify"})
    assert not state.get("verification_recovery_counts")
    assert state["execution_journal"][0]["execution_state"] == "dispatched_unknown"


def test_first_result_never_compiles_second_result_click_and_reads_when_order_missing():
    link = control("baike", "清华大学_百度百科", 2, tag="a")
    link.update(role="link", href="https://baike.baidu.com/item/清华大学")
    page = {"url": "https://www.baidu.com/s?wd=清华大学", "page_guard": {"document": "doc"}}
    menu = build_menu(
        [link], "打开第一条搜索结果", limit=30, page=page, allow_page_actions=True, probe_tools=("browser_probe_cards",)
    )
    assert all(s.get("target_id") != "baike" for s in menu.steps.values())
    assert any(s["op"] == "probe_cards" for s in menu.steps.values())
    page.update(cards=[first_card()], cards_observed=True)
    menu = build_menu(
        [link], "打开第一条搜索结果", limit=30, page=page, allow_page_actions=True, probe_tools=("browser_probe_cards",)
    )
    assert any(s.get("url") == "https://www.tsinghua.edu.cn/" for s in menu.steps.values())
    assert all(s.get("target_id") != "baike" and s["op"] != "probe_cards" for s in menu.steps.values())


@pytest.mark.asyncio
async def test_fixed_read_then_first_navigation_stays_in_one_guarded_loop():
    policy, llm, client, runtime, context, captured = setup_policy(goal="打开第一条搜索结果")
    runtime.decision_policy = policy
    page = runtime.ensure_page_state()
    page.observe(url="https://www.baidu.com/s?wd=清华大学")
    page.decision_snapshot["page_guard"] = {"document": "doc"}
    tools = [{"name": name} for name in ("browser_probe_cards", "browser_page_action")]
    choose_matching(client, "READ ordered result")
    result = await policy.invoke(await messages_for(policy, context, captured), tools=tools)
    call = result.tool_calls[0]
    assert call.name == "browser_probe_cards"
    args = json.loads(call.arguments)
    runtime.probe_cards = AsyncMock(return_value={"ok": True, "cards": [first_card()]})
    tool = BrowserProbeCardsTool(runtime)
    callback = SimpleNamespace(inputs=invoke_args(call.name, args, call.id))
    output = await tool.invoke(args, session=context.get_session_ref(), _tool_callback_context=callback)
    assert output.success
    policy.record_execution(callback.inputs, context.get_session_ref(), {"success": True})
    page.register_cards({"url": page.url, "cards": [first_card()]})
    choose_matching(client, "OPEN first organic")
    result = await policy.invoke(await messages_for(policy, context, captured), tools=tools)
    call = result.tool_calls[0]
    assert call.name == "browser_page_action"
    assert policy._guards[call.id].first_result["href"] == first_card()["primary_link"]
    runtime.call_playwright_run_code_unsafe.return_value = {"ok": False}  # Result order changed before dispatch.
    with pytest.raises(ValueError, match="browser_policy_target_changed"):
        await policy.validate_tool_call(invoke_args(call.name, call.arguments, call.id), context.get_session_ref())
    assert "first.length === 1" in runtime.call_playwright_run_code_unsafe.call_args.args[0]
    llm.invoke.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["arguments", "provider", "cancel"])
async def test_read_coverage_preserves_late_guards_llm_fallback_and_cancellation(failure):
    policy, llm, client, runtime, context, captured = setup_policy(goal="打开第一条搜索结果")
    runtime.decision_policy = policy
    page = runtime.ensure_page_state()
    page.decision_snapshot["page_guard"] = {"document": "doc"}
    choose_matching(client, "READ ordered result")
    tools = [{"name": "browser_probe_cards"}]
    if failure == "provider":
        client.evaluate.side_effect = DecisionUnavailable("jev_http_503")
    if failure == "cancel":
        client.evaluate.side_effect = asyncio.CancelledError()
        with pytest.raises(asyncio.CancelledError):
            await policy.invoke(await messages_for(policy, context, captured), tools=tools)
        llm.invoke.assert_not_awaited()
        assert not policy._guards
        return
    result = await policy.invoke(await messages_for(policy, context, captured), tools=tools)
    if failure == "provider":
        assert result.content == "original LLM answer"
        assert not policy._guards
        return
    call = result.tool_calls[0]
    runtime.probe_cards = AsyncMock()
    callback = SimpleNamespace(inputs=invoke_args(call.name, call.arguments, call.id))
    output = await BrowserProbeCardsTool(runtime).invoke(
        {"max_cards": 999}, session=context.get_session_ref(), _tool_callback_context=callback
    )
    assert not output.success and output.data["executed"] is False
    runtime.probe_cards.assert_not_awaited()


def test_post_fill_search_expectation_uses_the_same_observed_form_field():
    field = control("q", "Search", 1, tag="input", current_value="old")
    field["role"] = "searchbox"
    submit = control(
        "submit", "Search", 2, search_like=True, search_query={"document": "doc", "node": 1, "value": "old"}
    )
    page = SimpleNamespace(
        export_decision_targets=lambda: [field, submit],
        url="https://search.test/",
        get_target=lambda _: SimpleNamespace(selector=None, ref=None),
    )
    state = Rail._build_phase_state("搜索新查询")
    inputs = invoke_args(
        "browser_batch_interact",
        {
            "steps": [
                {"op": "fill", "target_id": "q", "value": "new"},
                {"op": "click", "target_id": "submit"},
            ]
        },
    )
    journal.prepare(session_for(state), inputs, SimpleNamespace(ensure_page_state=lambda: page))
    assert state["execution_journal"][0]["steps"][1]["expected_query"] == "new"


def test_first_result_late_guard_runs_real_dom_and_rejects_reordered_results(dom_page):
    source = "https://www.baidu.com/s?wd=university"
    dom_page.route(
        "https://www.baidu.com/**",
        lambda route: route.fulfill(
            content_type="text/html",
            body="""
        <style>.result {width:650px;height:180px;margin:12px}</style>
        <div id="content_left"><div class="result c-container"><h3>
        <a href="https://first.test/">First university</a></h3>
        <p>Official university site</p></div><div class="result c-container"><h3>
        <a href="https://second.test/">Second university</a></h3>
        <p>Encyclopedia entry</p></div></div>""",
        ),
    )
    dom_page.goto(source)
    run_code = "async code => await eval('(' + code + ')')({url:()=>location.href, evaluate:(fn,arg)=>fn(arg)})"
    observed = dom_page.evaluate(run_code, build_interactive_probe_js(decision_mode=True))
    cards = dom_page.evaluate(run_code, build_card_probe_js(site_profiles=site_profiles_for_url(source)))
    first = next(c for c in cards["cards"] if c["result_index"] == 1 and c["order_known"])
    runtime = _make_bare_runtime()
    page = runtime.ensure_page_state()
    page.register_interactives(observed)
    args = {"generation_id": page.generation_id, "op": "navigate", "url": first["primary_link"]}
    state = {"query_id": "q", "task_id": "t", "deadline_started_at": 1}
    session = session_for(state)
    guard = DecisionGuard(
        session.get_session_id(),
        page.page_id,
        page.generation_id,
        source,
        "",
        canonical_arguments(args),
        observed["decision_snapshot"]["page_guard"],
        "q:t:1",
        0,
        tool_name="browser_page_action",
        first_result={"title": first["title"], "href": first["primary_link"]},
    )
    runtime.call_playwright_run_code_unsafe = AsyncMock(return_value={"ok": True})
    # The synchronous Playwright fixture owns a greenlet event loop. Compile the
    # async guard with a stub transport off that loop, then execute its real JS here.
    with ThreadPoolExecutor(max_workers=1) as worker:
        worker.submit(
            asyncio.run, validate_guard(runtime, guard, invoke_args("browser_page_action", args), session)
        ).result()
    script = runtime.call_playwright_run_code_unsafe.call_args.args[0]
    assert dom_page.evaluate(run_code, script)["ok"]
    dom_page.evaluate("document.querySelector('#content_left').append(document.querySelector('.result'))")
    after = dom_page.evaluate(run_code, build_card_probe_js(site_profiles=site_profiles_for_url(source)))
    diagnostic = {"before": [(c['title'], c['result_index']) for c in cards['cards']],
                  "after": [(c['title'], c['result_index']) for c in after['cards']]}
    assert not dom_page.evaluate(run_code, script)["ok"], diagnostic
