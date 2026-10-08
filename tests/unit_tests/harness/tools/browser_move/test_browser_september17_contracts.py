# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Contract regressions tied to the September 17 browser traces."""
# pylint: disable=protected-access

import json
import os
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from openjiuwen.core.foundation.tool import ToolCard
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext, ToolCallInputs
from openjiuwen.harness.tools.browser_move.controllers.action import (
    _build_batch_interact_script,
    _compact_batch_conditions,
    normalize_batch_steps,
    validate_batch_steps,
)
from openjiuwen.harness.tools.browser_move.playwright_runtime.page_state import navigation_destination
from openjiuwen.harness.tools.browser_move.playwright_runtime.probes import build_card_probe_js
from openjiuwen.harness.tools.browser_move.playwright_runtime.runtime import BrowserAgentRuntime, BrowserRuntimeRail
from openjiuwen.harness.tools.subagent.task_tool import BROWSER_PARENT_QUERY_STATE_KEY, TaskTool
from tests.unit_tests.harness.tools.browser_move.test_browser_page_state import _interactive, _make_bare_runtime
from tests.unit_tests.harness.tools.browser_move.test_browser_runtime_rail import _FakeSession, _run

STATE_KEY = "__browser_phase_budget_state__"


@pytest.fixture
def dom_page():
    """Run generated Card code against a real DOM, without remote sites or saved HTML."""
    playwright = pytest.importorskip("playwright.sync_api")
    with playwright.sync_playwright() as driver:
        try:
            browser = driver.chromium.launch(headless=True, executable_path=os.getenv("BROWSER_TEST_CHROMIUM_PATH"))
        except playwright.Error as exc:
            pytest.skip(f"Optional local Chromium is unavailable: {exc}")
        try:
            yield browser.new_page(viewport={"width": 1280, "height": 900})
        finally:
            browser.close()


@pytest.mark.parametrize("selected", [
    'role="tab" aria-selected="true"', 'role="tab" aria-current="true"',
    'role="tab" data-state="active"', 'role="tab" class="selected"', 'class="sort-active"',
])
def test_actual_card_script_runs_selected_tab_branch(dom_page, selected):
    dom_page.set_content(f"""<style>article {{width:450px;height:140px;margin:12px}}</style>
        <button {selected}>Sales</button><main>
        <article class="product-card"><h2><a href="https://shop.test/item/1">Keyboard One</a></h2>
        <span class="price">$99</span><p>Mechanical keyboard product description</p></article>
        <article class="product-card"><h2><a href="https://shop.test/item/2">Keyboard Two</a></h2>
        <span class="price">$109</span><p>Mechanical keyboard product description</p></article></main>""")
    result = dom_page.evaluate("async code => await eval('(' + code + ')')({evaluate: (fn, arg) => fn(arg)})",
                               build_card_probe_js(max_cards=5, viewport_only=True))
    assert result["ok"] is True
    assert result["cards"]
    assert any(card.get("sort_state") == "Sales" for card in result["cards"])


@pytest.mark.parametrize("last_wait_fails", [False, True])
def test_actual_batch_click_sort_wait_does_not_replay_after_later_failure(last_wait_fails):
    playwright = pytest.importorskip("playwright.sync_api")
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is needed for generated Batch execution")
    steps = [
        {"op": "type", "selector": "#query", "value": "keyboard"},
        {"op": "click", "selector": "#sales"},
        {"op": "wait_for_sort_state", "selector": "#sales", "expected_label": "Sales"},
    ]
    if last_wait_fails:
        steps.append({"op": "wait_for_selector", "selector": "#never-present", "timeout_ms": 150})
    js = _build_batch_interact_script({"steps": steps, "timeout_ms": 2500, "condition_timeout_ms": 500})
    node_runner = r"""
const fs = require('fs');
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const {chromium} = require(input.package);
(async () => {
  const browser = await chromium.launch({headless: true, executablePath: input.executable || undefined});
  try {
    const page = await browser.newPage();
    await page.setContent(`<input id="query"><button id="sales"
      onclick="window.clicks=(window.clicks||0)+1;this.className='tab-active'">Sales</button>`);
    const result = await eval('(' + input.code + ')')(page);
    const clicks = await page.evaluate(() => window.clicks || 0);
    const value = await page.locator('#query').inputValue();
    console.log(JSON.stringify({result, clicks, value}));
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
"""
    package = Path(playwright.__file__).resolve().parents[1] / "driver" / "package"
    completed = subprocess.run([node, "-e", node_runner], input=json.dumps({
        "package": str(package), "executable": os.getenv("BROWSER_TEST_CHROMIUM_PATH"), "code": js,
    }), capture_output=True, text=True, encoding="utf-8", timeout=30, check=False)
    if "Executable doesn't exist" in completed.stderr:
        pytest.skip("Optional local Chromium is unavailable")
    assert completed.returncode == 0, completed.stderr
    observed = json.loads(completed.stdout)
    assert observed["clicks"] == 1 and observed["value"] == "keyboard"
    assert observed["result"]["ok"] is not last_wait_fails
    conditions = _compact_batch_conditions(observed["result"], observed["result"]["steps"])
    values, _ = BrowserRuntimeRail._successful_condition_values({"conditions": conditions})
    assert values["sort_state"] == "Sales"
    if last_wait_fails:
        assert observed["result"]["status"] == "partial"
        assert observed["result"]["steps"][1]["ok"] is True


@pytest.mark.parametrize("href,role,kind,expected", [
    ("#", "link", "", ""), ("https://gebiz.test/BOListing.xhtml#", "link", "", ""),
    ("javascript:void(0)", "link", "", ""), ("?sort=sales", "tab", "sort_tab", ""),
    ("/detail/1", "link", "result", "https://gebiz.test/detail/1"),
])
def test_navigation_destination_distinguishes_links_and_state_controls(href, role, kind, expected):
    assert navigation_destination(href, "https://gebiz.test/BOListing.xhtml", role=role, kind=kind) == expected


def test_gebiz_match_any_link_is_clickable_and_not_rewritten():
    runtime = _make_bare_runtime()
    page = runtime._ensure_page_state()
    page.observe(url="https://gebiz.test/BOListing.xhtml")
    control = {**_interactive("#match-any", "Match Any"), "role": "link",
               "href": "https://gebiz.test/BOListing.xhtml#"}
    payload = {"elements": [control]}
    page.register_interactives(payload)
    args = {"target_id": control["target_id"]}
    assert not runtime.resolve_primary_link(args)
    assert not runtime.resolve_primary_link({**args, "href": "https://gebiz.test/other"})
    resolved = _run(runtime._resolve_batch_steps(
        [{"op": "click", **args}, {"op": "wait_for_load_state"}], generation_id=page.generation_id,
    ))
    assert resolved[0]["selector"] == "#match-any"
    assert "_navigate_url" not in resolved[0]


def test_bing_type_text_is_normalized_before_both_validators():
    source = [{"op": "type", "target_id": "t_g1_1", "text": "Singapore weather"},
              {"op": "press", "key": "Enter"}]
    steps = normalize_batch_steps(source)
    assert source[0]["text"] == "Singapore weather"
    assert steps[0]["value"] == "Singapore weather" and "text" not in steps[0]
    assert validate_batch_steps(steps) == []
    assert BrowserAgentRuntime._validate_batch_target_contract(steps) == []
    conflicting = normalize_batch_steps([{"op": "type", "target_id": "t_g1_1", "text": "A", "value": "B"}])
    assert validate_batch_steps(conflicting)


def test_sort_wait_consumes_the_same_runtime_target_and_preserves_observed_source():
    runtime = _make_bare_runtime()
    page = runtime._ensure_page_state()
    payload = {"elements": [{**_interactive("#sales", "Sales"), "role": "tab", "kind": "sort_tab"}]}
    page.register_interactives(payload)
    target = payload["elements"][0]["target_id"]
    steps = [{"op": "click", "target_id": target}, {"op": "wait_for_sort_state", "target_id": target}]
    assert not validate_batch_steps(steps)
    resolved = _run(runtime._resolve_batch_steps(steps, generation_id=page.generation_id))
    assert resolved[1]["selector"] == "#sales" and resolved[1]["expected_label"] == "Sales"
    assert not validate_batch_steps(resolved)
    values, sources = BrowserRuntimeRail._successful_condition_values({"generation_id": "g1", "conditions": [{
        "op": "wait_for_sort_state", "ok": True, "selector": "#sales",
        "observed": {"value": {"selected": True, "selected_source": "class", "text": "Sales"}},
    }]})
    assert values == {"sort_state": "Sales"}
    assert sources["sort_state"]["selector"] == "#sales"


def test_ax_materialization_error_does_not_publish_a_fake_selector():
    runtime = _make_bare_runtime()
    page = runtime._ensure_page_state()
    page.register_ax_snapshot('- textbox "Search" [ref=e1]')
    tool = SimpleNamespace(invoke=AsyncMock(return_value={"result": "### Error\nRef e1 not found"}))
    runtime._get_playwright_mcp_tool = AsyncMock(return_value=tool)
    target = page.resolve_target(generation_id="g0", ref="e1")
    with pytest.raises(ValueError, match="Ref e1"):
        _run(runtime._materialize_ax_target(target))
    assert target.locator == {"ref": "e1"}


@pytest.mark.parametrize("ref", ["e1", "ref=e1", "[ref=e1]"])
def test_native_ax_ref_aliases_use_the_same_batch_resolver(ref):
    runtime = _make_bare_runtime()
    page = runtime._ensure_page_state()
    page.register_ax_snapshot('- textbox "Search" [ref=e1]')
    tool = SimpleNamespace(invoke=AsyncMock(return_value={"result": True}))
    runtime._get_playwright_mcp_tool = AsyncMock(return_value=tool)
    steps = normalize_batch_steps([{"op": "type", "ref": ref, "text": "Singapore weather"}])
    assert BrowserAgentRuntime._validate_batch_target_contract(steps) == []
    resolved = _run(runtime._resolve_batch_steps(steps, generation_id="g0"))
    assert resolved[0]["value"] == "Singapore weather"
    assert resolved[0]["selector"].startswith('[data-openjiuwen-target-id="t_g0_')
    assert tool.invoke.await_args.args[0]["target"] == "e1"


def test_original_query_does_not_inherit_delegated_button_enumeration():
    session = _FakeSession()
    session.update_state({BROWSER_PARENT_QUERY_STATE_KEY: "搜索B站 Python，按最多播放排序并返回首条标题"})
    task = TaskTool(ToolCard(name="task_tool"), MagicMock())
    query = task._prepare_browser_query(session, "query-parent", "找到综合、最新、最多播放，返回综合/最新标题", "")
    rail = BrowserRuntimeRail(MagicMock(spec=BrowserAgentRuntime))
    child = _FakeSession()
    state = rail._ensure_task_state(child, query.task_description, original_goal=query.record["original_user_goal"])
    assert {slot["variant"] for slot in state["required_evidence_slots"]} == {"default"}
    assert "最多播放" in state["goal"]


@pytest.mark.parametrize("task", [
    "页面有综合、最新、最多播放按钮。切换最多播放，返回首条视频标题",
    "找到综合、最新、最多播放，点击最多播放，告诉我第一个视频标题",
])
def test_button_enumeration_is_not_a_comparison_request(task):
    slots = BrowserRuntimeRail._infer_required_evidence_slots(task)
    assert {slot["variant"] for slot in slots} == {"default"}


def test_genuine_comparison_retains_two_source_variants():
    state = BrowserRuntimeRail._build_phase_state("对比B站综合和最新结果，返回两个首条视频标题")
    for order, title in (("totalrank", "First relevant"), ("pubdate", "First newest")):
        BrowserRuntimeRail._record_structured_evidence(state, {
            "result": {"title": title}, "generation_id": "g2",
            "page_state": {"url": f"https://search.bilibili.com/all?order={order}"},
        }, tool_name="browser_evaluate", tool_args={})
    assert {slot["variant"] for slot in state["evidence_slots"]} == {"comprehensive", "latest"}
    assert len({slot["source"] for slot in state["evidence_slots"]}) == 2


def test_open_homepage_does_not_require_product_url():
    state = BrowserRuntimeRail._build_phase_state("打开淘宝首页")
    assert not state["required_evidence_slots"]
    session = _FakeSession()
    session.update_state({STATE_KEY: state})
    BrowserRuntimeRail._record_phase_result(session, "browser_navigate", {}, {"url": "https://www.taobao.com/"})
    BrowserRuntimeRail._apply_worker_progress_to_task_state(session, {"status": "completed"}, "已打开淘宝首页")
    assert state["status"] == "completed"


def test_search_card_title_cannot_certify_destination_page_title():
    state = BrowserRuntimeRail._build_phase_state("搜索清华大学，打开首条搜索结果并返回页面标题和网址")
    BrowserRuntimeRail._record_structured_evidence(state, {
        "url": "https://www.bing.com/search?q=tsinghua", "generation_id": "g1",
        "cards": [{"title": "Search title", "primary_link": "https://www.tsinghua.edu.cn/"}],
    }, tool_name="browser_probe_cards", tool_args={})
    assert not state["evidence_slots"]
    BrowserRuntimeRail._record_structured_evidence(state, {
        "page_state": {"url": "https://www.tsinghua.edu.cn/", "title": "Destination title", "generation_id": "g2"},
    }, tool_name="browser_navigate", tool_args={})
    slots = {item["field"]: item for item in state["evidence_slots"]}
    assert slots["title"]["value"] == "Destination title"
    assert slots["url"]["generation"] == "g2"


@pytest.mark.parametrize("task", [
    "打开淘宝首页，搜索键盘，点击销量排序并返回首条搜索结果标题",
    "点击最多播放排序，返回首条搜索结果的标题",
    "无需打开详情页，返回首条搜索结果的标题",
])
def test_sort_click_or_homepage_step_does_not_invent_a_detail_page_requirement(task):
    state = BrowserRuntimeRail._build_phase_state(task)
    assert not BrowserRuntimeRail._requires_destination_page(state)
    BrowserRuntimeRail._record_structured_evidence(state, {
        "url": "https://shop.test/search?q=keyboard", "generation_id": "g1",
        "cards": [{"title": "Keyboard", "primary_link": "https://shop.test/item/1"}],
    }, tool_name="browser_probe_cards", tool_args={})
    assert any(slot["field"] == "title" and slot["value"] == "Keyboard" for slot in state["evidence_slots"])


def test_foreign_probe_does_not_overwrite_keyboard_page_or_certify_earrings():
    runtime = MagicMock(spec=BrowserAgentRuntime)
    runtime.export_page_state.return_value = {"url": "https://item.taobao.com/item.htm?id=1"}
    state = BrowserRuntimeRail._build_phase_state("搜索机械键盘，返回第一款商品标题和价格")
    state["last_page"] = runtime.export_page_state.return_value
    session = _FakeSession()
    session.update_state({STATE_KEY: state})
    result = {"ok": True, "url": "https://www.taobao.com/", "cards": [{"title": "Earrings", "price": "2"}]}
    ctx = AgentCallbackContext(agent=MagicMock(), session=session, inputs=ToolCallInputs(
        tool_name="browser_probe_cards", tool_args={}, tool_result=result,
    ))
    _run(BrowserRuntimeRail(runtime).after_tool_call(ctx))
    assert ctx.inputs.tool_result["error"] == "browser_observation_source_mismatch"
    assert not state["evidence_slots"]
    assert state["status"] == "in_progress"
    runtime.record_tool_reference_state.assert_not_called()


def test_empty_slots_plus_dsml_intent_is_not_completion():
    state = BrowserRuntimeRail._build_phase_state("Open a browser and search Python")
    session = _FakeSession()
    session.update_state({STATE_KEY: state})
    BrowserRuntimeRail._record_phase_result(session, "browser_probe_cards", {}, {"cards": [{"title": "Search"}]})
    BrowserRuntimeRail._apply_worker_progress_to_task_state(
        session, {"status": "completed"}, '<DSML><invoke name="browser_click">next</invoke></DSML>',
    )
    assert state["status"] == "partial"
    assert state["terminal_reason"] == "model_tool_protocol_error"


def test_targeted_weather_and_missing_rating_keep_provenance():
    state = BrowserRuntimeRail._build_phase_state("返回今日最高温和最低温")
    BrowserRuntimeRail._record_structured_evidence(state, {
        "result": {"sel": "#weather", "text": "今日最高33℃，最低26℃；明天最高35℃"},
        "page_state": {"url": "https://bing.test/search?q=weather", "generation_id": "g3"},
    }, tool_name="browser_evaluate", tool_args={})
    slots = {item["field"]: item for item in state["evidence_slots"]}
    assert slots["high_temperature"]["value"] == "33"
    assert slots["low_temperature"]["selector"] == "#weather"
    assert slots["low_temperature"]["generation"] == "g3"
    state = BrowserRuntimeRail._build_phase_state("Return product_rating")
    BrowserRuntimeRail._record_structured_evidence(state, {
        "result": {"product_rating": {"status": "unknown"}, "shop_rating": "4.9"},
        "page_state": {"url": "https://shop.test/item/1", "generation_id": "g3"},
    }, tool_name="browser_evaluate", tool_args={})
    assert state["evidence_slots"][0]["status"] == "unknown"
    assert "product_rating" not in state["field_coverage"]


def test_focused_resume_keeps_repair_instruction_and_original_constraints():
    record = {"original_user_goal": "Find a keyboard under $100", "original_task": "Find product",
              "browser_result": {"missing_slots": ["product.default.price"], "missing_fields": ["price"]}}
    text = TaskTool._focused_browser_resume_task(record, "The old title is earrings; use the open keyboard tab")
    assert "under $100" in text and "open keyboard tab" in text
    assert "inferred slots are not extra requirements" in text.lower()


def test_corrected_entity_replaces_old_product_link_as_well_as_title_and_price():
    state = BrowserRuntimeRail._build_phase_state("Return product title, url and price")
    for item_id, title in (("2", "Earrings"), ("1", "Keyboard")):
        BrowserRuntimeRail._record_structured_evidence(state, {
            "result": {"title": title, "url": f"https://shop.test/item/{item_id}",
                       "price": {"value": "99", "qualifier": "starting_from"}},
            "page_state": {"url": "https://shop.test/search?q=keyboard", "generation_id": "g3"},
        }, tool_name="browser_evaluate", tool_args={})
    slots = {item["field"]: item for item in state["evidence_slots"]}
    assert slots["title"]["value"] == "Keyboard"
    assert slots["url"]["value"] == "https://shop.test/item/1"
    assert slots["price"]["qualifier"] == "starting_from"
    assert len({item["entity_source"] for item in slots.values()}) == 1


def test_evaluated_link_and_title_do_not_impersonate_navigation_metadata():
    runtime = _make_bare_runtime()
    page = runtime._ensure_page_state()
    page.observe(url="https://www.bing.com/search?q=tsinghua", title="Bing results")
    runtime.record_tool_reference_state(
        tool_name="browser_evaluate", tool_args={},
        tool_result={"result": {"url": "https://www.tsinghua.edu.cn/", "title": "Result link title"}},
    )
    assert page.url == "https://www.bing.com/search?q=tsinghua"
    assert page.title == "Bing results"
    state = BrowserRuntimeRail._build_phase_state("打开首条搜索结果，返回页面标题和网址")
    BrowserRuntimeRail._record_structured_evidence(state, {
        "result": {"url": "https://www.tsinghua.edu.cn/", "title": "Result link title"},
        "page_state": page.export_summary(),
    }, tool_name="browser_evaluate", tool_args={})
    assert not state["evidence_slots"]
    runtime.record_tool_reference_state(
        tool_name="browser_evaluate", tool_args={},
        tool_result={"result": "### Page\n- Page URL: https://www.tsinghua.edu.cn/\n- Page Title: Tsinghua"},
    )
    assert page.url == "https://www.tsinghua.edu.cn/" and page.title == "Tsinghua"
