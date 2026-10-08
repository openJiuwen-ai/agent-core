# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Generic execution/observation regressions from sanitized September 22 traces."""
# pylint: disable=protected-access

import json
import shutil
import subprocess
from unittest.mock import AsyncMock, MagicMock

import pytest

from openjiuwen.core.foundation.llm.schema.message import ToolMessage
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext, ToolCallInputs
from openjiuwen.harness.tools.browser_move.controllers.action import _build_batch_interact_script
from openjiuwen.harness.tools.browser_move.playwright_runtime.browser_working_context import BrowserWorkingContextStore
from openjiuwen.harness.tools.browser_move.playwright_runtime.evidence import same_page_url
from openjiuwen.harness.tools.browser_move.playwright_runtime.probe_semantics import normalize_card_probe_payload
from openjiuwen.harness.tools.browser_move.playwright_runtime.probes import (
    build_card_probe_js,
    build_interactive_probe_js,
)
from openjiuwen.harness.tools.browser_move.playwright_runtime.runtime import BrowserAgentRuntime, BrowserRuntimeRail
from openjiuwen.harness.tools.browser_move.utils.parsing import decode_mcp_result
from tests.unit_tests.harness.tools.browser_move.test_browser_query_observations import _record, _state
from tests.unit_tests.harness.tools.browser_move.test_browser_runtime_rail import _FakeSession, _make_bare_runtime, _run
from tests.unit_tests.harness.tools.browser_move.test_browser_september17_contracts import dom_page as shared_dom_page

dom_page = shared_dom_page
STATE_KEY = "__browser_phase_budget_state__"


@pytest.mark.parametrize("value", [
    [1, 2], "[1, 2]", '"[1, 2]"', {"result": "[1, 2]"}, "[1, 2]\n### Page state\nOther content",
    {"result": {"data": "[1, 2]"}},
    {"content": [{"type": "text", "text": "### Result\n[1, 2]\n### Ran Playwright code\nreturn [9]"}]},
    "### Result\n```json\n[1, 2]\n```\n### Page state\nOther content",
])
def test_mcp_result_decodes_data_not_executed_code(value):
    assert decode_mcp_result(value) == [1, 2]


def test_result_decoder_keeps_error_and_does_not_mine_json_from_page_or_code():
    error = {"isError": True, "content": [{"type": "text", "text": '{"ok": true}'}]}
    assert decode_mcp_result(error) == error
    assert decode_mcp_result('### Result\nNo matches\n### Ran Playwright code\n{"title":"fake"}') == "No matches"
    assert decode_mcp_result('An article containing {"title":"example"}') == 'An article containing {"title":"example"}'


def test_real_mcp_array_wrapper_and_relative_urls_share_the_evidence_path():
    state = _state("Return first five titles", ["title"])
    state["last_page"]["url"] = "https://search.test/search?q=lesson"
    state["requested_result_count"] = 5
    rows = [{"title": f"Lesson {i}", "href": f"//video.test/lesson/{i}"} for i in range(5)]
    _record(state, "### Result\n" + json.dumps(rows) + "\n### Ran Playwright code\nreturn [];")
    assert state["observed_result_count"] == 5
    assert state["evidence_slots"][0]["source"] == "https://search.test/search?q=lesson"
    assert state["structured_evidence"][0]["cards"][0]["primary_link"] == "https://video.test/lesson/0"
    assert state["structured_evidence"][0]["cards"][0]["result_index"] is None


def test_array_extraction_uses_same_ai_classification_as_probe():
    state = _state("Return search results", [])
    state["last_page"]["url"] = "https://www.google.com/search?q=calculator"
    _record(state, [
        {"title": "Calculator", "href": "https://example.test/1"},
        {"title": "AI 模式针对‘计算器’的回复", "href": "https://example.test/2"},
        {"title": "Another calculator", "href": "https://example.test/3"},
    ])
    assert state["observed_result_count"] == 2
    assert [card["title"] for card in state["structured_evidence"][0]["cards"]] == [
        "Calculator", "Another calculator",
    ]


def test_redirect_is_proved_by_this_successful_navigation_not_another_tabs_cache():
    state = _state("Open the first search result and return title", ["title"])
    state["last_page"] = {"url": "https://search.test/search?q=university", "title": "Results"}
    args = {"url": "https://search.test/link?token=123"}
    result = {"result": "### Page\n- Page URL: https://university.test/\n- Page Title: University"}
    BrowserRuntimeRail._record_structured_evidence(state, result, tool_name="browser_navigate", tool_args=args)
    assert state["evidence_slots"][0]["value"] == "University"
    metadata = state["structured_evidence"][0]
    assert metadata["destination_verified"]
    assert metadata["navigation"]["requested_url"] == args["url"]
    unrelated = {**result, "page_state": {"url": "https://other.test/", "title": "Unrelated"}}
    assert not BrowserRuntimeRail._page_metadata_evidence(state, unrelated, "browser_navigate", args)


@pytest.mark.parametrize("url,same", [
    ("https://example.test/item?b=2&a=1&utm_source=campaign", True),
    ("https://example.test/item?a=1&b=3", False),
    ("https://example.test/item?a=1&b=2#other-view", False),
])
def test_page_identity_preserves_content_parameters_and_fragments(url, same):
    original = "https://example.test/item?a=1&b=2"
    assert same_page_url(original, url) is same
    state = {"last_page": {"url": original, "title": "Item"}}
    BrowserWorkingContextStore._merge_semantic_observation(state, {"semantic_state": {"url": url}})
    assert state["last_page"]["title"] == ("Item" if same else "")
    runtime = _make_bare_runtime()
    runtime._observe_page_url(original)
    runtime._ensure_page_state().observe(title="Item")
    original_generation = runtime.generation_id
    runtime._observe_page_url(url)
    assert runtime._ensure_page_state().title == ("Item" if same else "")
    assert (runtime.generation_id == original_generation) is same


@pytest.mark.parametrize("mode,ok", [
    ("old-only", False), ("same-page", True), ("popup", True), ("two-popups", False), ("foreign-popup", False),
    ("related-noopener-popup", True), ("foreign-noopener-popup", False),
])
def test_generated_wait_never_uses_preexisting_matching_tabs(tmp_path, mode, ok):
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node is unavailable")
    script = _build_batch_interact_script({
        "steps": [{"op": "click", "selector": "#search"}, {"op": "wait_for_url", "url_contains": "/results"}],
        "condition_timeout_ms": 250,
    })
    runner = tmp_path / "wait.js"
    runner.write_text(f"""
const fn = ({script});
const mode = {json.dumps(mode)};
const pages = [];
const context = {{ pages: () => pages }};
let clicks = 0;
let activeListeners = 0;
const makePage = (url, opener = null) => ({{
  url: () => url, title: async () => 'Page', context: () => context,
  opener: async () => opener, bringToFront: async () => {{}},
  listeners: new Map(),
  on(name, listener) {{ this.listeners.set(name, listener); activeListeners++; }},
  off(name, listener) {{
    if (this.listeners.get(name) === listener) {{ this.listeners.delete(name); activeListeners--; }}
  }},
  waitForTimeout: async (ms) => new Promise(resolve => setTimeout(resolve, ms)),
}});
const old = makePage('https://example.test/results?q=old');
const source = makePage('https://example.test/');
pages.push(old, source);
source.locator = () => ({{first() {{return this;}}, count: async () => 1,
  waitFor: async () => {{}}, isVisible: async () => true, isEnabled: async () => true,
  click: async () => {{
    clicks++;
    if (mode === 'same-page') source.url = () => 'https://example.test/results?q=new';
    if (mode.includes('popup')) {{
      const popup = makePage('https://example.test/results?q=new',
        mode.includes('noopener') ? null : mode === 'foreign-popup' ? old : source);
      pages.push(popup);
      if (mode === 'related-noopener-popup') source.listeners.get('popup')?.(popup);
    }}
    if (mode === 'two-popups') pages.push(makePage('https://example.test/results?q=other', source));
  }}
}});
fn(source).then(result => console.log(JSON.stringify({{result, clicks, activeListeners}})));
""", encoding="utf-8")
    completed = subprocess.run([node, str(runner)], check=True, text=True, capture_output=True, timeout=10)
    output = json.loads(completed.stdout)
    assert output["result"]["ok"] is ok
    assert output["clicks"] == 1
    assert output["activeListeners"] == 0
    assert output["result"]["url"] != "https://example.test/results?q=old"
    if mode == "popup":
        assert output["result"]["page_binding"]["source_tab_index"] == 1
        assert output["result"]["page_binding"]["relation"] == "action_popup"


def _probe(page, code):
    return page.evaluate("async code => await eval('(' + code + ')')({evaluate: (fn,arg) => fn(arg)})", code)


def test_offscreen_control_is_distinct_from_disabled_and_occluded(dom_page):
    dom_page.set_content('''<button id="covered" style="position:absolute;top:20px;left:20px">Book covered</button>
      <div style="position:absolute;top:0;left:0;width:400px;height:150px;background:white;z-index:5"></div>
      <button id="disabled" disabled style="position:absolute;top:200px">Book disabled</button>
      <button id="far" style="position:absolute;top:2200px">Book room</button>''')
    result = _probe(dom_page, build_interactive_probe_js(query="Book"))
    controls = {item["text"]: item for item in result["elements"]}
    assert controls["Book room"]["actionable"] and controls["Book room"]["requires_scroll"]
    assert controls["Book covered"]["actionability_reason"] == "occluded"
    assert controls["Book disabled"]["actionability_reason"] == "disabled"
    dom_page.locator(controls["Book room"]["selector_hint"]).click(timeout=2500)


@pytest.mark.parametrize("markup,price", [
    ('<span>$54.90</span><span>31% Off</span>', "$54.90"),
    ('<span>$8.67</span><span class="discount">44% Off</span>', "$8.67"),
    ('<span>$</span><span>10</span><span>.</span><span>27</span>', "$ 10.27"),
])
def test_card_price_uses_dom_boundaries_not_adjacent_discount(dom_page, markup, price):
    dom_page.set_content(f'''<main><article class="product-card" style="width:500px;height:160px">
      <h2><a href="https://shop.test/product/1">Useful product</a></h2>
      <div class="price">{markup}</div><p>A useful product with a normal description.</p>
      </article></main>''')
    result = _probe(dom_page, build_card_probe_js(max_cards=8))
    product = next(card for card in result["cards"] if card["title"] == "Useful product")
    assert product["price"] == price


def test_controls_and_navigation_do_not_take_result_slots(dom_page):
    dom_page.set_content('''<nav><article><a href="https://shop.test/app">Download the App</a></article></nav>
      <fieldset><legend>Price filters</legend><label><input type="checkbox">Five stars $100</label></fieldset>
      <main><section id="results">
      <article class="result-card"><h2><a href="https://shop.test/item/1">First hotel</a></h2>
      <p>Hotel with breakfast.</p></article>
      <article class="result-card"><h2><a href="https://shop.test/item/2">Second hotel</a></h2>
      <p>Hotel near transport.</p></article>
      </section></main><style>article {width:450px;height:120px;margin:10px}</style>''')
    result = _probe(dom_page, build_card_probe_js(max_cards=8))
    assert [card["title"] for card in result["cards"]] == ["First hotel", "Second hotel"]
    assert [card["result_index"] for card in result["cards"]] == [1, 2]


def test_ai_summary_is_readable_but_not_ranked_as_an_article():
    result = normalize_card_probe_payload({"url": "https://www.zhihu.com/search?q=topic", "cards": [
        {"title": "Key ideas", "summary": "AI 智能总结Some useful answer", "order_known": True},
        {"title": "An article about AI 智能总结", "href": "https://zhuanlan.zhihu.com/p/1", "order_known": True},
    ]})
    assert result["cards"][0]["kind"] == "ai_answer"
    assert result["cards"][0]["result_index"] is None
    assert result["cards"][1]["result_index"] == 1


def test_observed_count_is_not_erased_when_rank_is_unknown():
    result = normalize_card_probe_payload({"url": "https://example.test/search", "cards": [
        {"title": "A real result", "href": "https://example.test/article", "order_known": False},
    ]})
    assert result["observed_count"] == 1
    assert result["diagnostics"]["ranked_count"] == 0
    assert result["cards"][0]["result_index"] is None


def test_probe_provenance_keeps_observed_source_and_full_executable_selector():
    source = "https://example.test/results"
    selector = 'a[data-key="' + "x" * 800 + '"]'
    result = normalize_card_probe_payload({"url": source, "generation_id": "g7", "cards": [
        {"title": "Observed result", "title_selector_hint": selector},
    ]})
    provenance = result["cards"][0]["field_provenance"]["title"]
    assert provenance["source"] == source
    assert provenance["selector"] == selector
    assert provenance["generation_id"] == "g7"


def _finish(state, status="completed"):
    session = _FakeSession()
    session.update_state({STATE_KEY: state})
    BrowserRuntimeRail._apply_worker_progress_to_task_state(
        session, {"status": status}, "Answer from the observed page.",
    )
    return BrowserRuntimeRail._authoritative_terminal_payload(state)


def test_inferred_fields_count_and_variants_are_not_a_second_completion_judge():
    state = _state("Compare five results", ["novel_field", "title"])
    state["requested_result_count"] = 5
    state["required_evidence_slots"] = [{"entity": "page", "variant": "first", "field": "title"}]
    _record(state, {"pageText": "The actual requested records and comparison are available here."})
    result = _finish(state)
    assert result["status"] == "completed"
    assert result["missing_fields"] == [] and result["missing_slots"] == []
    assert "novel_field" in result["unverified_fields"]
    assert not result["retryable"]
    assert result["observations"]


def test_explicit_requirements_are_still_checked():
    state = _state("Return the configured structured output", ["author"])
    state["requirements_source"] = "explicit"
    _record(state, "A relevant page was read, but the required structured author is absent.")
    assert _finish(state)["status"] == "partial"


def test_no_observation_or_real_unfinished_goal_cannot_be_completed():
    state = _state("Read the page", [])
    result = _finish(state)
    assert result["status"] == "partial" and result["retryable"]
    state["resume_count"] = 1
    assert not _finish(state)["retryable"]
    state = _state("Read two pages", [])
    _record(state, "Only the first page has been read so far.")
    assert _finish(state, "partial")["status"] == "partial"


@pytest.mark.parametrize("reason", ["user_cancelled", "task_deadline_exhausted", "permission_denied"])
def test_inferred_completion_cannot_override_terminal_execution_boundaries(reason):
    state = _state("Read a page", [])
    _record(state, "There is some useful information already on this page.")
    state.update(status="blocked", terminal_reason=reason, blockers=[reason])
    result = _finish(state)
    assert result["status"] == "blocked"
    assert result["terminal_reason"] == reason


@pytest.mark.parametrize("mode", ["selected", "mismatch", "exception"])
def test_popup_binding_synchronizes_native_current_tab_without_replaying_steps(mode):
    runtime = _make_bare_runtime()
    runtime.ensure_runtime_ready = AsyncMock()
    runtime._code_executor = None
    runtime._controller = MagicMock()
    runtime._controller.run_action = AsyncMock(return_value={
        "ok": True, "status": "success", "executed": True,
        "steps": [{"op": "click", "ok": True, "executed": True}],
        "_runtime_page": {"url": "https://example.test/popup", "title": "Popup", "binding": {
            "tab_switched": True, "tab_index": 2, "source_tab_index": 1, "relation": "action_popup",
        }},
    })
    selected_url = "https://example.test/popup" if mode == "selected" else "https://example.test/old"
    runtime._call_playwright_tool = AsyncMock(
        return_value=f"### Result\n- 2: (current) [Popup]({selected_url})",
        side_effect=RuntimeError("selection failed") if mode == "exception" else None,
    )
    result = _run(runtime.batch_interact(steps=[
        {"op": "wait_for_url", "url_contains": "/popup"},
        {"op": "wait_for_url", "url_contains": "/popup"},
    ], generation_id=runtime.generation_id))
    runtime._controller.run_action.assert_awaited_once()
    runtime._call_playwright_tool.assert_awaited_once_with("browser_tabs", {"action": "select", "index": 2})
    assert result["ok"] is (mode == "selected")
    assert result["steps"][0]["executed"]
    assert result["page_binding"]["mcp_selected"] is (mode == "selected")
    assert result["page_binding"]["url"] == "https://example.test/popup"
    if mode != "selected":
        assert result["error"] == "browser_tab_binding_mismatch"
        assert "do not repeat" in result["recovery_hint"]


@pytest.mark.parametrize("storage", ["available", "failed", "disabled"])
def test_card_projection_keeps_query_window_and_recoverable_raw_result(storage):
    runtime = MagicMock(spec=BrowserAgentRuntime)
    source = "https://example.test/search"
    runtime.export_page_state.return_value = {
        "url": source, "generation_id": "g3", "cards": [{"title": "stale cached card"}],
    }
    raw = {"url": source, "cards": [{"title": "fresh", "text_preview": "RAW DETAIL " * 2000}]}
    compact = {"title": "fresh", "text_preview": "RAW DETAIL", "primary_link": "https://example.test/item"}
    result = {"ok": True, "url": source, "cards": [compact], "_raw_observation": raw}
    message = ToolMessage(content=json.dumps(result), tool_call_id="probe")
    session = _FakeSession()
    session.update_state({STATE_KEY: BrowserRuntimeRail._build_phase_state("Read the result")})
    recall = MagicMock() if storage != "disabled" else None
    if recall is not None:
        recall.persist_observation = AsyncMock(
            return_value="a" * 32, side_effect=OSError("disk full") if storage == "failed" else None,
        )
    rail = BrowserRuntimeRail(runtime, recall_tool=recall)
    ctx = AgentCallbackContext(agent=MagicMock(), session=session, inputs=ToolCallInputs(
        tool_name="browser_probe_cards", tool_args={}, tool_result=result, tool_msg=message,
    ))
    _run(rail.after_tool_call(ctx))
    projected = json.loads(message.content)
    assert projected["cards"] == [compact]
    assert "_raw_observation" not in projected
    if storage == "available":
        stored = recall.persist_observation.await_args.args[1]
        assert json.loads(stored) == raw
        assert projected["recall_handle"] == "a" * 32
        assert "raw_observation" not in projected
    else:
        assert projected["raw_observation"] == raw
