# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Regressions from Sep 16 traces and concurrent-session issue #6006."""
# pylint: disable=protected-access

import asyncio
import json
import shutil
import subprocess
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from openjiuwen.core.foundation.tool import ToolCard
from openjiuwen.core.session.agent import Session
from openjiuwen.harness.rails.subagent.subagent_rail import SubagentRail
from openjiuwen.harness.tools.browser_move.clients.stdio_client import BrowserMoveStdioClient
from openjiuwen.harness.tools.browser_move.controllers.action import _build_batch_interact_script
from openjiuwen.harness.tools.browser_move.playwright_runtime.browser_state_context_processor import (
    BrowserStateContextProcessor,
    BrowserStateContextProcessorConfig,
)
from openjiuwen.harness.tools.browser_move.playwright_runtime.browser_working_context import BrowserWorkingContextStore
from openjiuwen.harness.tools.browser_move.playwright_runtime.evidence import (
    evidence_subject,
    merge_evidence_slot,
    today_temperature_fields,
)
from openjiuwen.harness.tools.browser_move.playwright_runtime.page_state import BrowserPageState
from openjiuwen.harness.tools.browser_move.playwright_runtime.probe_semantics import normalize_card_probe_payload
from openjiuwen.harness.tools.browser_move.playwright_runtime.probes import build_interactive_probe_js
from openjiuwen.harness.tools.browser_move.playwright_runtime.runtime import BrowserAgentRuntime, BrowserRuntimeRail
from openjiuwen.harness.tools.browser_move.playwright_runtime.service import BrowserService
from openjiuwen.harness.tools.browser_move.playwright_runtime.service_registry import BrowserServiceRegistry
from openjiuwen.harness.tools.subagent.task_tool import EXECUTION_DEADLINE_STATE_KEY, TaskTool


@pytest.mark.asyncio
async def test_same_browser_tasks_are_serial_but_different_keys_can_run():
    registry = BrowserServiceRegistry()
    shared = SimpleNamespace(browser_key="shared", server_id="stdio")
    other = SimpleNamespace(browser_key="other", server_id="stdio-other")
    entered = asyncio.Event()
    release = asyncio.Event()
    second_entered = asyncio.Event()

    async def first():
        async with registry.task_turn(shared):
            entered.set()
            await release.wait()

    async def second():
        async with registry.task_turn(shared):
            second_entered.set()

    owner = asyncio.create_task(first())
    await entered.wait()
    waiter = asyncio.create_task(second())
    await asyncio.sleep(0.04)
    assert not second_entered.is_set()
    async with registry.task_turn(other):
        assert not second_entered.is_set()
    release.set()
    await asyncio.wait_for(asyncio.gather(owner, waiter), 1)
    assert second_entered.is_set()
    assert not registry._task_turns


@pytest.mark.asyncio
async def test_cancelled_waiter_does_not_take_browser_or_leak_lock():
    registry = BrowserServiceRegistry()
    identity = SimpleNamespace(browser_key="shared", server_id="stdio")

    async def wait():
        async with registry.task_turn(identity):
            raise AssertionError("cancelled waiter acquired the browser")

    async with registry.task_turn(identity):
        waiter = asyncio.create_task(wait())
        await asyncio.sleep(0)
        waiter.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiter
    async with registry.task_turn(identity):
        pass
    assert not registry._task_turns


def _state(fields):
    state = BrowserRuntimeRail._build_phase_state("Read the first product")
    state["required_fields"] = fields
    state["required_evidence_slots"] = [
        {"entity": "product", "variant": "default", "field": field} for field in fields
    ]
    state["last_page"] = {"url": "https://shop.example/search?q=headphones"}
    return state


@pytest.mark.asyncio
async def test_outer_deadline_reaches_browser_and_is_not_reused_by_next_run():
    import time

    session = Session(session_id="deadline-test")
    deadline = time.time() + 120
    rail = SubagentRail()
    context = SimpleNamespace(
        session=session, inputs=SimpleNamespace(run_context={"extra": {"execution_deadline_at": deadline}})
    )
    await rail.before_invoke(context)
    task = TaskTool(ToolCard(name="task_tool"), MagicMock())
    query = task._prepare_browser_query(session, "deadline-test", "compare two products", "")
    assert query.record["deadline_at"] == deadline
    assert query.record["budget_s"] == 600
    context.inputs.run_context = None
    await rail.before_invoke(context)
    assert session.get_state(EXECUTION_DEADLINE_STATE_KEY) is None
    query = task._prepare_browser_query(session, "deadline-test", "compare two other products", "")
    assert query.record["deadline_at"] > deadline


def _record(state, value):
    BrowserRuntimeRail._record_structured_evidence(
        state, {"result": value, "generation_id": "g2"},
        tool_name="browser_evaluate", tool_args={"function": "() => ({})"},
    )


def test_reused_keyboard_page_does_not_confirm_headphone_sort():
    state = BrowserRuntimeRail._build_phase_state("搜索蓝牙耳机，返回销量排序")
    progress = {"semantic_state": {
        "url": "https://s.taobao.com/search?q=机械键盘", "generation_id": "g0",
        "selected_filters": [{"key": "sort", "value": "销量"}],
    }}
    BrowserWorkingContextStore._merge_semantic_evidence(state, progress)
    assert not state["evidence_slots"]
    assert not state["structured_evidence"]
    state["goal"] = "查看当前页面的销量排序"
    BrowserWorkingContextStore._merge_semantic_evidence(state, progress)
    assert state["evidence_slots"][0]["value"] == "销量"


def test_corrected_first_product_replaces_other_products_price_and_shop():
    state = _state(["title", "price", "shop", "product_rating"])
    _record(state, {"href": "https://item.taobao.com/item.htm?id=986521475652", "price": "10.27", "shop": "Old"})
    _record(state, [
        {"href": "https://item.taobao.com/item.htm?id=998795471047", "title": "FitClip Ultra", "price": "408.15"},
        {"href": "https://item.taobao.com/item.htm?id=986521475652", "shop": "Wrong shop", "product_rating": "4.8"},
    ])
    slots = {slot["field"]: slot for slot in state["evidence_slots"]}
    assert slots["price"]["value"] == "408.15"
    assert slots["title"]["value"] == "FitClip Ultra"
    assert "shop" not in slots and "product_rating" not in slots
    assert len({slot["entity_source"] for slot in slots.values()}) == 1
    _record(state, {"href": "https://item.taobao.com/item.htm?id=998795471047", "shop_rating": "4.8"})
    assert "product_rating" not in state["field_coverage"]


def test_same_source_can_correct_value_but_different_price_scopes_remain_distinct():
    state = _state(["price"])
    _record(state, {"price": "100", "href": "https://shop.example/item/1"})
    _record(state, {"price": "99", "href": "https://shop.example/item/1"})
    assert state["evidence_slots"][0]["value"] == "99"
    slot = {**state["evidence_slots"][0], "value": "120", "qualifier": "current_sku"}
    merge_evidence_slot(state, slot)
    assert state["evidence_slots"][0]["value"] == "99"
    assert state["evidence_slots"][0]["alternatives"][0]["value"] == "120"


def test_entity_correction_does_not_require_a_requested_title_field():
    state = _state(["price", "shop"])
    _record(state, {"href": "https://example.test/item/2", "price": "10", "shop": "Old"})
    _record(state, {"href": "https://example.test/item/1", "title": "First", "price": "99"})
    assert [(slot["field"], slot["value"]) for slot in state["evidence_slots"]] == [("price", "99")]
    _record(state, {"href": "https://example.test/item/1", "title": "First", "price": "120",
                    "qualifier": "current_sku", "date": "2026-09-16"})
    projected = BrowserWorkingContextStore._project_evidence_slot(state["evidence_slots"][0], include_value=True)
    assert projected["alternatives"][0]["qualifier"] == "current_sku"
    assert projected["alternatives"][0]["date"] == "2026-09-16"


def test_final_renderer_prefers_latest_resume_summary():
    state = _state([])
    state["last_worker_final"] = "Corrected result"
    old = json.dumps({"browser_result": {"summary": "Old result"}})
    _, payload = BrowserRuntimeRail._render_authoritative_terminal_output(state, old)
    assert payload["summary"] == "Corrected result"


def test_weather_today_range_closes_fields_without_another_model_round():
    state = BrowserRuntimeRail._build_phase_state("查看西安天气，返回今天最高温和最低温")
    result = normalize_card_probe_payload({
        "ok": True, "url": "https://www.baidu.com/s?wd=西安天气", "generation_id": "g2",
        "cards": [
            {"title": "天气网", "result_index": 1},
            {"title": "今日天气", "summary": "西安今天\u200c16~24℃，阴转多云。未来几天 明天17~25℃",
             "selector_hint": "#today", "result_index": 2},
        ],
    })
    BrowserRuntimeRail._record_structured_evidence(state, result, tool_name="browser_probe_cards", tool_args={})
    slots = {slot["field"]: slot for slot in state["evidence_slots"]}
    assert slots["high_temperature"]["value"] == "24"
    assert slots["low_temperature"]["value"] == "16"
    assert "今天" in slots["high_temperature"]["raw_text"] or "今日" in slots["high_temperature"]["raw_text"]
    assert today_temperature_fields("明天17~25℃")[0] == {}
    assert today_temperature_fields("今日新闻，明天17~25℃")[0] == {}


def test_full_link_survives_page_state_and_evidence():
    url = "https://www.bing.com/ck/a?u=" + "a" * 700
    result = {"url": "https://bing.com/search", "cards": [{"title": "Result", "primary_link": url}]}
    page = BrowserPageState()
    page.register_cards(result)
    assert page.export()["cards"][0]["primary_link"] == url
    compact = BrowserRuntimeRail._card_probe_evidence(result)
    assert compact["cards"][0]["primary_link"] == url
    assert evidence_subject(url).endswith("a" * 700)
    state = _state(["url"])
    _record(state, {"url": url})
    slot = state["evidence_slots"][0]
    assert slot["value"] == url
    assert BrowserWorkingContextStore._project_evidence_slot(slot, include_value=True)["value"] == url
    selector = "#" + "s" * 900
    BrowserRuntimeRail._record_structured_evidence(
        state, {"result": {"url": url}, "generation_id": "g3"},
        tool_name="browser_evaluate", tool_args={"target": selector},
    )
    slot = state["evidence_slots"][0]
    assert slot["selector"] == selector
    assert BrowserWorkingContextStore._project_evidence_slot(slot, include_value=True)["selector"] == selector
    processor = BrowserStateContextProcessor(BrowserStateContextProcessorConfig(provider=None, max_dom_chars=12_000))
    header = processor._fit_state_header({
        "url": url, "page_state": {"url": url}, "error": "x" * 15_000,
    })
    assert header["url"] == url and header["page_state"]["url"] == url


@pytest.mark.asyncio
async def test_task_handover_waits_for_inflight_cleanup(monkeypatch):
    registry = BrowserServiceRegistry()
    from openjiuwen.harness.tools.browser_move.playwright_runtime import service_registry

    monkeypatch.setattr(service_registry, "BROWSER_SERVICE_REGISTRY", registry)
    identity = SimpleNamespace(browser_key="shared", server_id="stdio")
    cleanup_started = asyncio.Event()
    cleanup_allowed = asyncio.Event()
    inflight_started = asyncio.Event()

    async def inflight():
        inflight_started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cleanup_started.set()
            await cleanup_allowed.wait()

    producer = asyncio.create_task(inflight())
    await inflight_started.wait()
    service = BrowserService.__new__(BrowserService)
    service._inflight_tasks = {"task": {producer}}
    for key in ("_locks", "_sessions", "_failure_context_by_session", "_progress_by_session"):
        setattr(service, key, {})

    async def release_binding():
        await service._clear_task_scoped_state()
        return False

    def make_runtime(release):
        runtime = BrowserAgentRuntime.__new__(BrowserAgentRuntime)
        runtime._service = SimpleNamespace(
            lifecycle_identity=identity, acquire_task_binding=MagicMock(), release_task_binding=release,
        )
        return runtime

    first = make_runtime(release_binding)
    second = make_runtime(AsyncMock(return_value=False))
    await first.acquire_task_resources()
    release = asyncio.create_task(first.release_task_resources())
    await cleanup_started.wait()
    waiter = asyncio.create_task(second.acquire_task_resources())
    await asyncio.sleep(0.04)
    assert not waiter.done()
    cleanup_allowed.set()
    await asyncio.wait_for(asyncio.gather(release, waiter), 1)
    assert producer.done()
    await second.release_task_resources()
    assert not registry._task_turns


@pytest.mark.asyncio
async def test_schema_failure_has_no_execution_or_observation():
    runtime = BrowserAgentRuntime.__new__(BrowserAgentRuntime)
    runtime._page_generation = 0
    runtime.ensure_runtime_ready = AsyncMock()
    result = await runtime.batch_interact(steps=[{"op": "fill"}], generation_id="g0")
    assert result["executed"] is False and result["state_changed"] is False
    runtime.ensure_runtime_ready.assert_not_awaited()


@pytest.mark.asyncio
async def test_optional_stale_close_does_not_abort_following_read():
    runtime = BrowserAgentRuntime.__new__(BrowserAgentRuntime)
    runtime._page_generation = 1
    runtime._refresh_runtime_owned_target = AsyncMock(side_effect=ValueError("target gone"))
    optional = {"op": "click", "target_id": "t_g0_missing", "optional": True}
    read = {"op": "extract_text", "selector": "#loaded", "field": "title"}
    steps, _ = await runtime._refresh_stale_batch_targets([optional, read], generation_id="g0")
    assert "target gone" in steps[0]["_target_error"]
    resolved = await runtime._resolve_batch_steps(steps, generation_id=runtime.generation_id)
    assert resolved[1]["selector"] == "#loaded"
    with pytest.raises(ValueError, match="target gone"):
        await runtime._refresh_stale_batch_targets([{**optional, "optional": False}, read], generation_id="g0")


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["browser_mouse_wheel", "browser_click", "browser_run_code_unsafe"])
async def test_timed_out_actions_are_not_automatically_replayed(monkeypatch, action):
    client = BrowserMoveStdioClient.__new__(BrowserMoveStdioClient)
    client._params = {"timeout_s": 300}
    client._session = SimpleNamespace(call_tool=AsyncMock(side_effect=asyncio.TimeoutError))
    client._reconnect = AsyncMock(return_value=True)
    timeouts = []
    original = asyncio.wait_for

    async def wait_for(task, timeout):  # noqa: ASYNC109 -- matches patched asyncio.wait_for
        timeouts.append(timeout)
        return await original(task, timeout)

    monkeypatch.setattr(asyncio, "wait_for", wait_for)
    with pytest.raises(RuntimeError, match="execution may have occurred"):
        await client.call_tool(action, {})
    client._session.call_tool.assert_awaited_once()
    client._reconnect.assert_not_awaited()
    assert timeouts == ([15.0] if action == "browser_mouse_wheel" else [300.0])


@pytest.mark.parametrize("mode", [
    "wait_then_read", "ambiguous", "optional", "required", "reload_bound", "reload_read", "optional_stale",
])
def test_batch_live_target_validation(mode):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node required to execute the generated RPC")
    steps = [{"op": "extract_text", "selector": "#late", "field": "title"}]
    if mode == "wait_then_read":
        steps.insert(0, {"op": "wait_for_selector", "selector": "#late"})
    elif mode == "optional":
        steps[0]["optional"] = True
        steps.append({"op": "extract_text", "selector": "#present", "field": "title"})
    elif mode == "optional_stale":
        steps[0].update(optional=True, _target_error="target gone")
        steps.append({"op": "extract_text", "selector": "#present", "field": "title"})
    elif mode.startswith("reload"):
        steps = [{"op": "click", "selector": "#present"},
                 {"op": "extract_text", "selector": "#present", "field": "title"}]
        if mode == "reload_bound":
            steps[1]["resolved_target_id"] = "t_g0_1"
    script = _build_batch_interact_script({"steps": steps})
    source = f"""
      const fn = ({script});
      let ready = false;
      let onNavigation = null;
      const frame = {{}};
      class Locator {{
        constructor(selector) {{this.selector = selector;}}
        first() {{return this;}}
        async count() {{return '{mode}' === 'ambiguous' ? 2 : (ready || this.selector === '#present' ? 1 : 0);}}
        async waitFor() {{ready = true;}}
        async isVisible() {{return true;}}
        async isEnabled() {{return true;}}
        async innerText() {{return 'Loaded title';}}
        async click() {{if (onNavigation) onNavigation(frame);}}
      }}
      const page = {{locator: s => new Locator(s), url: () => 'https://example.test', title: async () => 'Test',
        mainFrame: () => frame,
        on: (event, fn) => {{if (event === 'framenavigated') onNavigation = fn;}},
        off: (event) => {{if (event === 'framenavigated') onNavigation = null;}}}};
      fn(page).then(value => {{
        if (onNavigation !== null) throw new Error('navigation observer leaked');
        console.log(JSON.stringify(value));
      }});
    """
    output = subprocess.run([node, "-"], input=source, text=True, capture_output=True, check=True, timeout=10)
    result = json.loads(output.stdout)
    if mode in {"wait_then_read", "reload_read"}:
        assert result["ok"] is True and result["extracted"] == {"title": "Loaded title"}
    elif mode == "reload_bound":
        assert result["status"] == "partial" and result["executed"] is True
        assert result["steps"][1]["executed"] is False
        assert "stale target" in result["error"]
    elif mode in {"optional", "optional_stale"}:
        assert result["status"] == "partial" and result["extracted"] == {"title": "Loaded title"}
        assert result["steps"][0]["executed"] is False
    else:
        assert result["executed"] is False
        assert result["steps"][0]["state_changed"] is False


@pytest.mark.parametrize("mode", ["unique", "ambiguous", "disabled"])
def test_dynamic_sort_probe_emits_only_unique_actionable_targets(mode):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node required to execute the generated probe")
    script = build_interactive_probe_js(query="sales", generation_id="g9")
    source = """
      class Element {
        constructor(parent = null) {this.parentElement = parent; this.tagName = 'DIV'; this.nodeType = 1;}
        getAttribute(name) {return ({role:'tab', class:'next-tabs-tab-active'})[name] || null;}
        hasAttribute() {return false;}
        matches() {return false;}
        closest() {return null;}
        contains(node) {return node === this;}
        getBoundingClientRect() {return {x:0,y:0,left:0,right:100,top:0,bottom:20,width:100,height:20};}
        get innerText() {return 'sales';}
      }
      let parent = null;
      for (let i=0; i<7; i++) parent = new Element(parent);
      const target = new Element(parent);
      const twin = new Element(parent);
      target.disabled = MODE === 'disabled';
      global.Node = {ELEMENT_NODE:1};
      global.window = {location:{hostname:'example.test', href:'https://example.test'},
        innerWidth:800, innerHeight:600, scrollX:0, scrollY:0,
        getComputedStyle:() => ({display:'block',visibility:'visible',opacity:'1',pointerEvents:'auto'})};
      global.document = {title:'Sort', elementFromPoint:()=>target,
        querySelectorAll:(selector) => {
          if (selector.includes(',')) return [target];
          return MODE !== 'ambiguous' && selector.split(' > ').length >= 6 ? [target] : [target,twin];
        }};
      const page = {evaluate:async (fn, params) => fn(params)};
      PROBE(page).then(value => console.log(JSON.stringify(value)));
    """.replace("MODE", json.dumps(mode)).replace("PROBE", f"({script})")
    output = subprocess.run([node, "-"], input=source, text=True, capture_output=True, check=True, timeout=10)
    result = json.loads(output.stdout)
    element = result["elements"][0]
    assert element["selected"] is True and element["selected_source"] == "class"
    assert element["kind"] == "sort_tab"
    if mode == "unique":
        assert element["clickable"] is True
        assert element["match_count"] == 1
        assert len(element["selector_hint"].split(" > ")) == 6
    else:
        assert element["clickable"] is False and element["selector_hint"] == ""
