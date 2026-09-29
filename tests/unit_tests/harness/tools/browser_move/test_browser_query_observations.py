# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Sanitized September 21 trace regressions, without network or user artifacts."""
# pylint: disable=protected-access

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from openjiuwen.core.foundation.llm import ToolMessage
from openjiuwen.core.foundation.tool import ToolCard
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext, InvokeInputs, ToolCallInputs
from openjiuwen.harness.tools.browser_move.playwright_runtime.browser_working_context import BrowserWorkingContextStore
from openjiuwen.harness.tools.browser_move.playwright_runtime.evidence import merge_evidence_slot
from openjiuwen.harness.tools.browser_move.playwright_runtime.page_state import BrowserPageState
from openjiuwen.harness.tools.browser_move.playwright_runtime.probe_semantics import normalize_card_probe_payload
from openjiuwen.harness.tools.browser_move.playwright_runtime.probes import build_card_probe_js
from openjiuwen.harness.tools.browser_move.playwright_runtime.runtime import BrowserAgentRuntime, BrowserRuntimeRail
from openjiuwen.harness.tools.browser_move.playwright_runtime.site_profiles import BUILTIN_SITE_PROFILES
from openjiuwen.harness.tools.subagent.task_tool import TaskTool
from tests.unit_tests.harness.tools.browser_move.test_browser_page_state import _interactive, _make_bare_runtime
from tests.unit_tests.harness.tools.browser_move.test_browser_runtime_rail import _FakeSession
from tests.unit_tests.harness.tools.browser_move.test_browser_september17_contracts import dom_page as shared_dom_page

STATE_KEY = "__browser_phase_budget_state__"
dom_page = shared_dom_page


def _state(goal, fields):
    return {
        "goal": goal, "task": goal, "task_id": "test", "requirements_source": "inferred",
        "status": "in_progress", "required_fields": fields,
        "required_evidence_slots": [
            {"entity": "page", "variant": "default", "field": field} for field in fields
        ],
        "last_page": {"url": "https://example.test/article/1", "title": "An article"},
        "recent_actions": [{"outcome": "success"}],
    }


def _record(state, value, tool="browser_evaluate"):
    result = {"result": value, "page_state": {**state["last_page"], "generation_id": "g3"}}
    BrowserRuntimeRail._record_structured_evidence(
        state, result, tool_name=tool, tool_args={"function": "() => document.body.innerText"},
    )


def test_targeted_probe_keeps_matches_not_global_ax_index():
    runtime = _make_bare_runtime()
    runtime.ensure_runtime_ready = AsyncMock()
    runtime._code_executor = object()
    runtime._execute_probe_json = AsyncMock(return_value=({
        "ok": True, "elements": [_interactive(f"#breakfast-{i}", f"Breakfast {i}") for i in range(15)],
        "total_candidates": 15,
    }, None, 0))
    page = runtime._ensure_page_state()
    page.register_ax_snapshot("\n".join(f'- textbox "Global menu {i}" [ref=e{i}]' for i in range(25)))
    result = asyncio.run(runtime.probe_interactives(query="Breakfast", max_items=30))
    assert [item["text"] for item in result["elements"]] == [f"Breakfast {i}" for i in range(15)]
    assert result["count"] == 15
    for target in result["elements"]:
        assert page.resolve_target(target_id=target["target_id"], generation_id=target["generation_id"])
    assert page.resolve_target(ref="e0", generation_id="g0")


def test_targeted_probe_without_actionable_matches_does_not_return_unrelated_controls():
    state = BrowserPageState()
    state.register_ax_snapshot('- textbox "Search" [ref=e1]')
    matches = state.register_interactives({"elements": [{
        **_interactive(".duplicate", "Breakfast"), "match_count": 2, "selector_hint_validated": False,
    }]})
    assert matches == []
    assert state.export()["interactives"][0]["text"] == "Search"


def test_current_tab_metadata_is_atomic_and_ignores_other_tab_titles():
    runtime = _make_bare_runtime()
    runtime._ensure_page_state().observe(url="https://search.test/", title="Search homepage")
    result = {"result": "### Result\n- 0: [Other](https://search.test/?q=university)\n"
                        "- 1: (current) [University](https://university.test/)"}
    runtime.record_tool_reference_state(tool_name="browser_tabs", tool_args={"action": "select"}, tool_result=result)
    page = runtime.export_page_state()
    assert (page["url"], page["title"]) == ("https://university.test/", "University")
    state = {"last_page": {"url": "https://search.test/", "title": "Search homepage"}}
    BrowserRuntimeRail._update_last_page(state, result)
    assert state["last_page"] == {"url": "https://university.test/", "title": "University"}


def test_changed_url_without_title_never_reuses_old_title():
    page = BrowserPageState()
    page.observe(url="https://example.test/a", title="A")
    page.observe(url="https://example.test/b")
    assert page.title == ""


@pytest.mark.parametrize("goal", [
    "找四星级酒店，地点不限，返回名称和价格，评分若有再提供",
    "Find a four-star hotel, any location. Return title and price; rating if available.",
])
def test_optional_rating_and_unrestricted_location_are_not_required_fields(goal):
    fields = BrowserRuntimeRail._infer_required_fields(goal)
    assert "rating" not in fields
    assert "address" not in fields
    assert "price" in fields


def test_later_local_zero_comment_evidence_replaces_empty_lookup():
    state = _state("返回评论数", ["comments"])
    _record(state, {"commentCountText": []})
    _record(state, {"commentHeader": "理性发言，友善互动 还没有评论，发表第一个评论吧", "commentCountText": []})
    slot = state["evidence_slots"][0]
    assert slot["status"] == "present"
    assert slot["value"] == "0"
    assert "还没有评论" in slot["raw_text"]
    assert slot["source"] == "https://example.test/article/1"


def test_empty_alias_does_not_downgrade_positive_value_in_same_result():
    result = BrowserRuntimeRail._evaluate_evidence(
        {"result": {"comments": 0, "commentCountText": []}}, {}, required_fields=["comments"],
    )
    assert result["field_status"]["comments"] == "present"
    assert result["values"]["comments"] == "0"


def test_ordered_evaluate_rows_count_actual_distinct_records_not_claimed_count():
    state = _state("Return ten result titles and links", ["title", "url"])
    state["requested_result_count"] = 10
    _record(state, {"count": 99, "results": [
        {"title": f"Result {i}", "href": f"https://result.test/{i}"} for i in range(10)
    ]})
    assert state["observed_result_count"] == 10
    missing = BrowserRuntimeRail._missing_completion_requirements(state)
    assert not any(item.startswith("result_count:") for item in missing)
    assert state["evidence_slots"]


def test_replan_advice_does_not_turn_sufficient_answer_into_website_blocker():
    state = _state("Read an answer", [])
    state["replan_required"] = True
    state["status"] = "replan_required"
    _record(state, "A sufficiently detailed answer read from the current page.")
    session = _FakeSession()
    session.update_state({STATE_KEY: state})
    BrowserRuntimeRail._apply_worker_progress_to_task_state(session, {"status": "completed"}, "Here is the answer.")
    assert session.get_state(STATE_KEY)["status"] == "completed"
    assert not session.get_state(STATE_KEY).get("blockers")


def test_taobao_ad_redirect_is_not_natural_first_result():
    result = normalize_card_probe_payload({"url": "https://s.taobao.com/search?q=keyboard", "cards": [{
        "title": "Keyboard", "primary_link": "https://click.simba.taobao.com/cc?xxc=ad_ztc&id=123",
        "region": "main_result", "kind": "product", "order_known": True,
    }]})
    card = result["cards"][0]
    assert card["is_ad"] is True
    assert card["ad_status"] == "ad"
    assert card["result_index"] is None


def test_snapshot_filename_keeps_native_target_and_inline_content_contract():
    rail = BrowserRuntimeRail(_make_bare_runtime())
    args = {"target": "e1", "filename": "regional-ax.txt"}
    assert rail._normalize_playwright_ref_args("playwright-official_browser_snapshot", args) == {"target": "e1"}
    assert args["filename"] == "regional-ax.txt"
    assert rail._normalize_playwright_ref_args("other_snapshot", args) == args


def test_find_result_links_are_not_current_page_metadata():
    runtime = _make_bare_runtime()
    page = runtime._ensure_page_state()
    page.observe(url="https://search.test/?q=university", title="Search")
    runtime.record_tool_reference_state(tool_name="browser_find", tool_args={}, tool_result={
        "result": '- link "University" [ref=e1]:\n  - /url: https://university.test/',
    })
    assert page.url == "https://search.test/?q=university"
    assert page.title == "Search"


def test_destination_requires_selection_and_correct_page_replaces_homepage_anchor():
    state = _state("打开搜索结果第一条，返回标题和网址", ["title", "url"])
    state["last_page"] = {}
    homepage = {"page_state": {"url": "https://search.test/", "title": "Search", "generation_id": "g1"}}
    BrowserRuntimeRail._record_structured_evidence(
        state, homepage, tool_name="browser_navigate", tool_args={"url": "https://search.test/"},
    )
    assert not state.get("evidence_slots")
    BrowserRuntimeRail._update_last_page(state, homepage)
    assert "destination_page" in BrowserRuntimeRail._missing_completion_requirements(state)
    state["evidence_slots"] = [{
        "entity": "page", "variant": "default", "field": "title", "value": "Stale homepage",
        "status": "present", "entity_source": "https://search.test/",
    }]
    state["last_page"] = {"url": "https://search.test/search?q=university", "title": "Search results"}
    state["structured_evidence"] = [{"source": state["last_page"]["url"], "cards": [{
        "title": "University", "primary_link": "https://university.test/", "region": "main_result", "is_ad": False,
    }]}]
    destination = {"result": "### Result\n- 0: [Search](https://search.test/)\n"
                             "- 1: (current) [University](https://university.test/)",
                   "page_state": {"url": "https://university.test/", "title": "University", "generation_id": "g3"}}
    BrowserRuntimeRail._record_structured_evidence(
        state, destination, tool_name="browser_tabs", tool_args={"action": "select", "index": 1},
    )
    BrowserRuntimeRail._update_last_page(state, destination)
    assert {slot["value"] for slot in state["evidence_slots"]} == {"University", "https://university.test/"}
    assert not BrowserRuntimeRail._missing_completion_requirements(state)


@pytest.mark.parametrize("region,kind", [
    ("navigation", "navigation_link"), ("ai_answer", "ai_answer"), ("sidebar", "result"),
])
def test_card_regions_survive_normalization_without_becoming_natural_results(region, kind):
    result = normalize_card_probe_payload({"url": "https://example.test/search", "cards": [{
        "title": "A module", "summary": "Readable content", "region": region, "kind": kind,
        "primary_link": "https://example.test/detail/1", "order_known": True,
    }]})
    card = result["cards"][0]
    assert card["region"] == region
    assert card["result_index"] is None
    assert not BrowserRuntimeRail._is_natural_evidence_card(card)


def test_card_only_answer_uses_same_sourced_observation_completion_path():
    state = _state("查询汇率", ["exchange_rate"])
    result = normalize_card_probe_payload({"url": state["last_page"]["url"], "generation_id": "g3", "cards": [{
        "title": "Currency converter", "summary": "1 SGD = 5.45 CNY. Updated today.",
        "kind": "ai_answer", "region": "ai_answer",
    }]})
    BrowserRuntimeRail._record_structured_evidence(state, result, tool_name="browser_probe_cards", tool_args={})
    session = _FakeSession()
    session.update_state({STATE_KEY: state})
    BrowserRuntimeRail._apply_worker_progress_to_task_state(session, {"status": "completed"}, "1 SGD = 5.45 CNY.")
    payload = BrowserRuntimeRail._authoritative_terminal_payload(state)
    assert payload["status"] == "completed"
    assert payload["unverified_fields"] == ["exchange_rate"]
    assert payload["observations"][0]["source"] == state["last_page"]["url"]
    assert "5.45" in payload["observations"][0]["raw_text"]
    assert not payload["evidence"]  # Observations are not invented typed fields.


def test_navigation_card_cannot_supply_hotel_evidence_or_answer_observation():
    state = _state("查询酒店价格", ["title", "price"])
    result = normalize_card_probe_payload({"url": "https://hotels.ctrip.com/", "cards": [{
        "title": "Flights", "primary_link": "https://flights.ctrip.com/", "price": "100",
        "region": "main_result", "kind": "result",
    }]})
    state["last_page"]["url"] = result["url"]
    BrowserRuntimeRail._record_structured_evidence(state, result, tool_name="browser_probe_cards", tool_args={})
    assert not state.get("evidence_slots")
    assert not BrowserRuntimeRail._task_observations(state)


def test_early_empty_lookup_is_not_confirmed_absence_and_real_rating_absence_stays_partial():
    state = _state("返回商品评分", ["product_rating"])
    _record(state, {"product_rating": None})
    assert state["evidence_slots"][0]["observation_status"] == "not_observed"
    projection = BrowserWorkingContextStore._project_task_state(state)
    assert projection["requirements"]["missing"]
    assert not projection["requirements"]["unavailable"]
    _record(state, {"fields": {"product_rating": {
        "status": "unknown", "raw_text": "Product reviews: no rating shown", "selector": "#product-reviews",
    }}})
    assert state["evidence_slots"][0]["observation_status"] == "explicit_absence"
    session = _FakeSession()
    session.update_state({STATE_KEY: state})
    BrowserRuntimeRail._apply_worker_progress_to_task_state(
        session, {"status": "completed"}, "No product rating shown.",
    )
    assert state["status"] == "partial"
    assert BrowserRuntimeRail._authoritative_terminal_payload(state)["unavailable_slots"]


def test_ordered_results_ignore_duplicates_ads_invalid_links_and_asserted_count():
    state = _state("Return search results", [])
    _record(state, {"count": 100, "results": [
        {"title": "One", "href": "https://result.test/1"},
        {"title": "One duplicated", "href": "https://result.test/1"},
        {"title": "No href"}, {"title": "Script", "href": "javascript:click()"},
        {"title": "Ad", "href": "https://ad.test/", "is_ad": True, "region": "sponsored_result"},
    ]})
    assert state["observed_result_count"] == 1


@pytest.mark.parametrize("expression", [
    "() => ({durationSec: window.__INITIAL_STATE__.videoData.duration})",
    "() => { const st = window.__INITIAL_STATE__; return {durationSec: st.videoData.duration}; }",
])
def test_current_part_and_collection_duration_keep_both_scopes_with_provenance(expression):
    state = _state("Return video duration", ["duration"])
    state["last_page"] = {"url": "https://www.bilibili.com/video/BVexample/", "title": "A course"}
    result = {"result": {"durationSec": 100533, "formatted": "27:55:33"},
              "page_state": {**state["last_page"], "generation_id": "g3"}}
    BrowserRuntimeRail._record_structured_evidence(state, result, tool_name="browser_evaluate", tool_args={
        "function": expression,
    })
    assert state["evidence_slots"][0]["qualifier"] == "collection"
    _record(state, {"fields": {"duration": {
        "value": "17:14", "scope": "current_part", "selector": "#active-part .duration", "raw_text": "Intro 17:14",
    }}})
    slot = state["evidence_slots"][0]
    assert (slot["value"], slot["qualifier"]) == ("17:14", "current_part")
    assert any(item["value"] == "27:55:33" and item["qualifier"] == "collection" for item in slot["alternatives"])


@pytest.mark.parametrize("at_checkout", [False, True])
def test_user_payment_boundary_requires_current_page_evidence(at_checkout):
    state = _state("预订一个酒店房间", [])
    state["last_page"] = {"url": "https://example.test/payment8/" if at_checkout else "https://example.test/hotel/1",
                          "title": "安全支付" if at_checkout else "Hotel details"}
    _record(state, {"text": "订单金额 100.00 使用新卡支付 更多付款方式" if at_checkout else "支持在线付款或到店付款"})
    session = _FakeSession()
    session.update_state({STATE_KEY: state})
    BrowserRuntimeRail._apply_worker_progress_to_task_state(session, {"status": "completed"}, "Read the current page.")
    assert (state["status"] == "blocked") is at_checkout
    assert ("payment_required" in state.get("blockers", [])) is at_checkout
    assert "semantic_replan_required" not in state.get("blockers", [])


@pytest.mark.parametrize("goal,fields", [
    ("返回地址和评分", {"address", "rating"}),
    ("Return hotel stars and guest rating", {"hotel_stars", "rating"}),
    ("地点不限，找酒店", set()),
    ("Return price and comments if available", {"price"}),
    ("返回价格、评论数（若有）", {"price"}),
    ("返回价格，地址若页面显示再提供，评分若显示再提供", {"price"}),
])
def test_required_field_qualifiers_are_local_to_each_field(goal, fields):
    assert set(BrowserRuntimeRail._infer_required_fields(goal)) == fields


def test_generated_card_script_classifies_navigation_and_scopes_video_duration(dom_page):
    dom_page.set_content('''<style>article {width:450px;height:150px;margin:8px}</style>
      <nav><article class="result-card"><h2><a href="https://example.test/flights">Flights</a></h2>
      <p>Travel deals for every destination.</p></article></nav><main>
      <article class="result-card"><h2><a href="https://example.test/video">Introduction</a></h2>
      <span data-duration-scope="current_part"><span class="duration">17:14</span></span>
      <p>Introduction to the current video lesson.</p></article></main>''')
    result = dom_page.evaluate(
        "async code => await eval('(' + code + ')')({evaluate: (fn,arg) => fn(arg)})",
        build_card_probe_js(max_cards=8),
    )
    assert result["ok"]
    video = next(card for card in result["cards"] if card["title"] == "Introduction")
    assert video["duration"] == "17:14" and video["duration_scope"] == "current_part"
    assert not any(card["title"] == "Flights" and card.get("result_index") for card in result["cards"])


def test_hotel_classification_is_not_guest_rating_after_projection():
    result = normalize_card_probe_payload({"url": "https://hotels.ctrip.com/", "generation_id": "g2", "cards": [{
        "title": "Hotel", "rating": "Four stars", "rating_kind": "hotel_stars",
        "rating_raw_text": "四星级", "primary_link": "https://hotels.ctrip.com/hotels/123.html",
    }]})
    page = BrowserPageState()
    page.requested_fields = {"rating", "hotel_stars"}
    page.register_cards(result)
    card = page.export()["cards"][0]
    assert card["hotel_stars"] == "Four stars"
    assert not card.get("rating")
    assert not card.get("product_rating")


def test_generated_site_rules_classify_ad_redirect_before_ranking(dom_page):
    profile = next(item for item in BUILTIN_SITE_PROFILES if item["id"] == "taobao_marketplace")
    fixture_profile = {**profile, "domains": [""], "card_container_selectors": ["article"]}
    dom_page.set_content('''<style>article {width:450px;height:140px;margin:10px}</style><main>
      <article class="product-card"><h2>
      <a href="https://click.simba.taobao.com/cc?xxc=ad_ztc">Promoted keyboard</a></h2>
      <span class="price">$20</span><p>A useful keyboard in an ad slot.</p></article>
      <article class="product-card"><h2><a href="https://item.taobao.com/item.htm?id=123">Natural keyboard</a></h2>
      <span class="price">$30</span><p>The first organic keyboard in the list.</p></article></main>''')
    result = dom_page.evaluate(
        "async code => await eval('(' + code + ')')({evaluate: (fn,arg) => fn(arg)})",
        build_card_probe_js(max_cards=8, site_profiles=[fixture_profile]),
    )
    assert result["ok"]
    natural = [card for card in result["cards"] if card.get("result_index")]
    assert natural and natural[0]["title"] == "Natural keyboard"
    assert all(card["is_ad"] for card in result["cards"] if card["title"] == "Promoted keyboard")


def test_card_observation_survives_rail_finalization_and_tasktool_transport():
    runtime = MagicMock(spec=BrowserAgentRuntime)
    runtime.service = MagicMock()
    runtime.persist_observation = AsyncMock(return_value="")
    runtime.export_page_state.return_value = {"url": "https://example.test/search?q=fx", "generation_id": "g2"}
    session = _FakeSession()
    state = BrowserRuntimeRail._build_phase_state("查询汇率")
    state.update(status="replan_required", replan_required=True)
    session.update_state({STATE_KEY: state})
    card_result = {"ok": True, "url": "https://example.test/search?q=fx", "generation_id": "g2", "cards": [{
        "title": "Currency converter", "summary": "1 SGD = 5.45 CNY", "region": "ai_answer", "kind": "ai_answer",
    }]}
    rail = BrowserRuntimeRail(runtime)
    tool_ctx = AgentCallbackContext(agent=MagicMock(), session=session, inputs=ToolCallInputs(
        tool_name="browser_probe_cards", tool_args={}, tool_result=card_result,
        tool_msg=ToolMessage(content=json.dumps(card_result), tool_call_id="probe"),
    ))
    asyncio.run(rail.after_tool_call(tool_ctx))
    result = {"output": "1 SGD = 5.45 CNY, from the current answer card.", "result_type": "answer"}
    invoke_ctx = AgentCallbackContext(agent=MagicMock(), session=session,
                                      inputs=InvokeInputs(query="查询汇率", result=result))
    asyncio.run(rail.after_invoke(invoke_ctx))
    task = TaskTool(ToolCard(name="task_tool"), MagicMock())
    parent = _FakeSession("parent")
    query = task._prepare_browser_query(parent, "parent", "查询汇率", "")
    output = task._build_task_output(
        result, normalized_type="browser_agent", sub_session_id="browser-session", parent_session=parent,
        browser_query=query, subagent=SimpleNamespace(card=SimpleNamespace(id="openjiuwen.browser_agent")),
    )
    assert output.success
    assert output.data["browser_result"]["status"] == "completed"
    assert output.data["browser_result"]["observations"]
    assert not output.data["browser_result"]["blockers"]


def test_unknown_order_keeps_observation_count_without_certifying_rank():
    result = normalize_card_probe_payload({"url": "https://example.test/search", "cards": [{
        "title": "A result", "primary_link": "https://example.test/one", "order_known": False,
    }]})
    evidence = BrowserRuntimeRail._card_probe_evidence(result)
    assert evidence["observed_count"] == 1
    assert result["cards"][0]["result_index"] is None


def test_card_without_ad_markers_does_not_certify_non_advertising():
    result = normalize_card_probe_payload({"url": "https://example.test/search", "cards": [{
        "title": "A result", "primary_link": "https://example.test/one", "order_known": True,
    }]})
    page = BrowserPageState()
    page.register_cards(result)
    assert page.export()["cards"][0]["ad_status"] == "unknown"


def test_landing_page_uses_action_source_and_related_cards_do_not_replace_its_title():
    state = _state("打开搜索结果第一条，返回标题和网址", ["title", "url"])
    state["structured_evidence"] = [{"source": "https://search.test/search?q=university", "cards": [{
        "title": "University", "primary_link": "https://university.test/", "region": "main_result", "is_ad": False,
    }]}]
    # The automatic observation can already have advanced last_page before the rail.
    state["last_page"] = {"url": "https://university.test/", "title": "University"}
    BrowserRuntimeRail._record_structured_evidence(
        state, {"page_state": {**state["last_page"], "generation_id": "g2"}},
        tool_name="browser_navigate", tool_args={
            "url": "https://university.test/", "_runtime_source_url": "https://search.test/search?q=university",
        },
    )
    related = normalize_card_probe_payload({"url": "https://university.test/", "cards": [{
        "title": "Related programme", "primary_link": "https://university.test/programme/2",
        "region": "main_result", "kind": "result", "order_known": True,
    }]})
    BrowserRuntimeRail._record_structured_evidence(
        state, related, tool_name="browser_probe_cards", tool_args={},
    )
    assert {slot["value"] for slot in state["evidence_slots"]} == {"University", "https://university.test/"}
    assert not BrowserRuntimeRail._missing_completion_requirements(state)


@pytest.mark.parametrize("item", [
    None, "", [], "unknown", "N/A",
    {"value": None}, {"value": []}, {"selector": "#rating"}, {"status": "unknown"},
    {"status": "missing", "selector": "#rating"},
    {"value": None, "raw_text": ""}, {"value": None, "raw_text": "Loading reviews"},
])
def test_nested_empty_field_is_not_confirmed_absence(item):
    state = _state("Return product rating", ["product_rating"])
    _record(state, {"fields": {"product_rating": item}})
    assert state["evidence_slots"][0]["observation_status"] == "not_observed"
    assert BrowserRuntimeRail._unavailable_evidence_slots(state) == []


def _price_slot(*, scope, status="present", observation_status="", value="10.27"):
    return {
        "entity": "product", "variant": "default", "field": "price", "status": status,
        "entity_source": "https://example.test/item/1", "evidence_scope": scope,
        "source": "https://example.test/search" if scope == "listing" else "https://example.test/item/1",
        "value": value, "raw_text": value, "observation_status": observation_status,
    }


def test_unobserved_detail_field_does_not_erase_observed_listing_value():
    state = {}
    merge_evidence_slot(state, _price_slot(scope="listing"))
    merge_evidence_slot(state, _price_slot(
        scope="detail", status="missing", observation_status="not_observed", value="",
    ))
    assert state["evidence_slots"][0]["value"] == "10.27"
    assert state["evidence_slots"][0]["evidence_scope"] == "listing"


def test_unobserved_detail_placeholder_cannot_block_later_real_evidence():
    state = {}
    merge_evidence_slot(state, _price_slot(
        scope="detail", status="missing", observation_status="not_observed", value="",
    ))
    merge_evidence_slot(state, _price_slot(scope="listing"))
    assert state["evidence_slots"][0]["value"] == "10.27"
    assert state["evidence_slots"][0]["status"] == "present"


def test_explicit_detail_absence_can_correct_weaker_listing():
    state = {}
    merge_evidence_slot(state, _price_slot(scope="listing"))
    merge_evidence_slot(state, _price_slot(
        scope="detail", status="unknown", observation_status="explicit_absence", value="",
    ))
    assert state["evidence_slots"][0]["status"] == "unknown"
    assert state["evidence_slots"][0]["observation_status"] == "explicit_absence"


@pytest.mark.parametrize("text", ["5.45", "0", "Open", "王老师"])
def test_short_sourced_read_is_not_rejected_by_length(text):
    state = _state("Read the current page label", [])
    _record(state, text)
    observations = BrowserRuntimeRail._task_observations(state)
    assert observations and observations[0]["raw_text"] == text
    session = _FakeSession()
    session.update_state({STATE_KEY: state})
    BrowserRuntimeRail._apply_worker_progress_to_task_state(session, {"status": "completed"}, text)
    assert state["status"] == "completed"


@pytest.mark.parametrize("value", [
    "", "   ", None, [], {}, False,
    {"fields": {"product_rating": {"value": None}}}, {"product_rating": {"value": None}},
])
def test_empty_lookup_cannot_supply_usable_task_evidence(value):
    state = _state("Return product rating", ["product_rating"])
    _record(state, value)
    assert not BrowserRuntimeRail._has_task_evidence(state)
