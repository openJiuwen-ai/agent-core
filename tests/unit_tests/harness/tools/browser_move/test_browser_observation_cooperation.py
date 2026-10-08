# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Native/Probe/completion regressions from the September 18 traces."""
# pylint: disable=protected-access

import asyncio
import json
import os
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from openjiuwen.core.foundation.llm import ToolMessage
from openjiuwen.core.foundation.tool import ToolCard
from openjiuwen.core.session.agent import Session
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext, ModelCallInputs
from openjiuwen.core.single_agent.schema.agent_card import AgentCard
from openjiuwen.harness.tools.browser_move.offload_recall import BrowserOffloadRecallTool
from openjiuwen.harness.tools.browser_move.playwright_runtime.browser_state_context_processor import (
    BrowserStateContextProcessor,
    BrowserStateContextProcessorConfig,
)
from openjiuwen.harness.tools.browser_move.playwright_runtime.evidence import merge_evidence_slot
from openjiuwen.harness.tools.browser_move.playwright_runtime.page_state import BrowserPageState
from openjiuwen.harness.tools.browser_move.playwright_runtime.probe_semantics import normalize_card_probe_payload
from openjiuwen.harness.tools.browser_move.playwright_runtime.probes import build_card_probe_js
from openjiuwen.harness.tools.browser_move.playwright_runtime.runtime import BrowserRuntimeRail
from openjiuwen.harness.tools.subagent.task_tool import TaskTool
from tests.unit_tests.harness.tools.browser_move.test_browser_page_state import _interactive, _make_bare_runtime
from tests.unit_tests.harness.tools.browser_move.test_browser_runtime_rail import _FakeSession, _run
from tests.unit_tests.harness.tools.browser_move.test_browser_september17_contracts import dom_page as shared_dom_page

STATE_KEY = "__browser_phase_budget_state__"
dom_page = shared_dom_page


def _card_result(page):
    return page.evaluate(
        "async code => await eval('(' + code + ')')({evaluate: (fn, arg) => fn(arg)})",
        build_card_probe_js(max_cards=3, viewport_only=True),
    )


def test_generated_cards_keep_rendered_order_not_quality_order(dom_page):
    dom_page.set_content('''<style>article {width:440px;height:140px;margin:8px}</style><main>
      <article class="result-card"><h2><a href="https://example.test/first">First result</a></h2>
      <p>A short summary for the first result.</p></article>
      <article class="result-card"><h2><a href="https://example.test/second">Rich second result</a></h2>
      <span class="author">Writer</span><span class="price">$19</span>
      <span class="like-count">20 likes</span><p>Second result has more fields and quality.</p></article>
      <article class="result-card"><h2><a href="https://example.test/third">Third result</a></h2>
      <p>A third result in the same list.</p></article></main>''')
    result = _card_result(dom_page)
    natural = [card for card in result["cards"] if card["result_index"]]
    assert natural[0]["primary_link"] == "https://example.test/first"
    assert natural[0]["order_source"] == "rendered_list"


def test_card_wrappers_and_ambiguous_lists_do_not_invent_rank(dom_page):
    cards = '''<div class="wrapper"><article class="result-card">
      <h2><a href="https://example.test/{key}">{key}</a></h2><p>A useful result summary.</p>
      </article></div>'''
    style = '<style>article {width:440px;height:110px}</style>'
    dom_page.set_content(style + '<main>' + ''.join(cards.format(key=key) for key in ("first", "second")) + '</main>')
    result = _card_result(dom_page)
    assert [card["result_index"] for card in result["cards"]] == [1, 2]
    dom_page.set_content(style + '<main><section>' + ''.join(cards.format(key=key) for key in ("a", "b")) +
                         '</section><section>' + ''.join(cards.format(key=key) for key in ("c", "d")) +
                         '</section></main>')
    assert all(card["result_index"] is None for card in _card_result(dom_page)["cards"])


def test_filtered_cards_do_not_claim_global_result_index(dom_page):
    dom_page.set_content('''<article class="result-card" style="width:440px;height:110px">
      <h2><a href="https://example.test/item">Matched entry</a></h2><p>A useful result summary.</p></article>''')
    result = dom_page.evaluate(
        "async code => await eval('(' + code + ')')({evaluate: (fn, arg) => fn(arg)})",
        build_card_probe_js(query="Matched", max_cards=3),
    )
    assert result["cards"]
    assert all(card["result_index"] is None for card in result["cards"])


def test_generated_fields_reject_ui_author_and_date_count_and_join_decimal(dom_page):
    dom_page.set_content('''<style>article {width:500px;height:180px}</style><main>
      <article class="product-card"><h2><a href="https://example.test/item/1">Earphones</a></h2>
      <a class="author" href="/profile">查看主页</a><span class="comment-count">评论</span>
      <time>2023-05-06</time><span class="price"><b>¥ 10</b> <span>.27</span></span>
      <button aria-label="收藏">9</button><p>Product summary text.</p></article></main>''')
    card = next(card for card in _card_result(dom_page)["cards"] if "Earphones" in card["title"])
    assert not card["author"]
    assert not card["comments"]
    assert "10.27" in card["price"]
    assert "9" in card["favorites"]


def test_unknown_order_is_not_recertified_by_python_normalizer():
    payload = {"url": "https://example.test/search", "cards": [
        {"title": "Secondary", "region": "main_result", "kind": "result", "order_known": False},
    ]}
    assert normalize_card_probe_payload(payload)["cards"][0]["result_index"] is None


@pytest.mark.parametrize("wrap", [lambda text: text, lambda text: {"result": text},
                                 lambda text: {"content": [{"type": "text", "text": text}]}])
def test_wrapped_ax_keeps_search_controls_after_many_generic_nodes(wrap):
    state = BrowserPageState()
    ax = "\n".join(f'- generic [ref=e{i}]: x' for i in range(40))
    ax += '\n- textbox "Search" [ref=e50]\n- button "Search now" [ref=e51]'
    state.register_ax_snapshot(wrap(ax))
    state.register_interactives({"elements": [_interactive("#hot", "Hot list")]})
    controls = state.export()["interactives"]
    assert controls[0]["role"] == "textbox"
    assert controls[0]["text"] == "Search"
    assert state.resolve_target(generation_id="g0", ref="e50").name == "Search"


def test_native_snapshot_is_not_replaced_by_probe_cache():
    runtime = _make_bare_runtime()
    rail = BrowserRuntimeRail(runtime)
    text = '- textbox "Search" [ref=e1]\n- heading "Weather answer 32 C" [ref=e2]'
    result = {"result": text}
    runtime.record_tool_reference_state(tool_name="browser_snapshot", tool_args={}, tool_result=result)
    inputs = SimpleNamespace(tool_msg=ToolMessage(content=json.dumps(result), tool_call_id="native"))
    rail._attach_page_state(inputs, "browser_snapshot", result)
    assert "Weather answer 32 C" in inputs.tool_msg.content
    assert "compact_page_state" not in inputs.tool_msg.content


def test_evaluate_does_not_repeat_cached_cards():
    runtime = _make_bare_runtime()
    runtime.export_page_state = lambda: {"page_id": "p", "url": "https://example.test", "cards": [{"title": "OLD"}]}
    inputs = SimpleNamespace(tool_msg=ToolMessage(content='{"result": {"author":"New"}}', tool_call_id="eval"))
    BrowserRuntimeRail(runtime)._attach_page_state(inputs, "browser_evaluate", {"result": {"author": "New"}})
    assert "New" in inputs.tool_msg.content and "OLD" not in inputs.tool_msg.content


def test_changed_sort_invalidates_cards_without_invalidating_controls():
    runtime = _make_bare_runtime()
    page = runtime._ensure_page_state()
    payload = {"elements": [{**_interactive("#sales", "Sales"), "kind": "sort_tab"}]}
    page.register_interactives(payload)
    cards = {"cards": [{**_interactive("#old-item", "Old item"), "title": "Old item",
                        "primary_link": "https://example.test/item/1"}]}
    page.register_cards(cards)
    tracker = runtime._ensure_semantic_state_tracker()
    tracker.observe({"selected_filters": ["Comprehensive"]}, action_group_id="initial")
    runtime._invalidate_changed_listing({"semantic_state": {"selected_filters": ["Sales"]}})
    assert page.export()["cards"] == [] and page.export()["listing_stale"]
    assert page.resolve_target(generation_id=page.generation_id, target_id=payload["elements"][0]["target_id"])
    with pytest.raises(ValueError):
        page.resolve_target(generation_id=page.generation_id, target_id=cards["cards"][0]["target_id"])


def test_sort_wait_repair_uses_only_a_known_preceding_sort_control():
    runtime = _make_bare_runtime()
    page = runtime._ensure_page_state()
    payload = {"elements": [{**_interactive("#sales", "Sales"), "kind": "sort_tab"}]}
    page.register_interactives(payload)
    target = payload["elements"][0]["target_id"]
    steps = [{"op": "click", "target_id": target}, {"op": "wait_for_dom_text_change", "previous_text": "Default"}]
    repaired = runtime.normalize_model_batch_steps(steps)
    assert repaired[1]["op"] == "wait_for_sort_state" and repaired[1]["target_id"] == target
    assert "previous_text" not in repaired[1]
    assert steps[1]["op"] == "wait_for_dom_text_change"
    assert runtime.normalize_model_batch_steps([steps[1]]) == [steps[1]]


@pytest.mark.parametrize("word", ["第一篇结果", "第一条结果", "该结果页面", "第一个结果"])
def test_destination_requirement_handles_result_classifiers(word):
    assert BrowserRuntimeRail._requires_destination_page({"goal": f"搜索 FastAPI，进入{word}，返回标题和作者"})


def test_sequential_comparison_has_two_variants_but_button_enumeration_does_not():
    slots = BrowserRuntimeRail._infer_required_evidence_slots(
        "先记录综合排序下第一条视频标题；再切换最新发布，返回新的第一条标题",
    )
    assert {item["variant"] for item in slots} == {"comprehensive", "latest"}
    slots = BrowserRuntimeRail._infer_required_evidence_slots("找到综合、最新、最多播放按钮，切换最多播放并返回标题")
    assert {item["variant"] for item in slots} == {"default"}


def test_inferred_coverage_does_not_disable_tools():
    runtime = _make_bare_runtime()
    rail = BrowserRuntimeRail(runtime)
    state = rail._build_phase_state("Return article title")
    phases = state["phases"]
    for details in phases.values():
        details["status"] = "completed"
    rail._complete_phase(state, phases, "extraction", phases["extraction"], "one result")
    assert state["status"] == "in_progress" and state["next_action_class"] == "may_finish"
    session = _FakeSession()
    ctx = AgentCallbackContext(agent=MagicMock(), inputs=ModelCallInputs(tools=[ToolCard(name="click")]))
    assert not rail._prepare_terminal_synthesis(ctx, session, state)
    assert ctx.inputs.tools


def test_explicit_fields_preserve_local_provenance_and_zero_comments():
    result = {"result": {"fields": {
        "favorites": {"value": 9, "selector": "#fav", "raw_text": "收藏 9"},
        "comments": {"value": "还没有评论", "selector": "#comments", "raw_text": "还没有评论"},
        "price": {"value": "10.27", "selector": "#price", "raw_text": "￥10.27", "qualifier": "current_sku"},
        "author": {"value": "查看主页", "selector": "#author"},
    }}, "page_state": {"url": "https://example.test/detail/1", "generation_id": "g3"}}
    evidence = BrowserRuntimeRail._evaluate_evidence(
        result, {}, required_fields=["favorites", "comments", "price", "author"],
    )
    assert evidence["values"] == {"favorites": "9", "comments": "0", "price": "10.27"}
    assert evidence["field_status"]["author"] == "unknown"
    assert evidence["provenance"]["price"]["selector"] == "#price"
    assert evidence["provenance"]["comments"]["raw_text"] == "还没有评论"
    assert evidence["qualifier"] == "current_sku"


def test_explicit_field_list_keeps_value_and_selector_from_same_entity():
    rows = [
        {"field": "price", "value": "10.27", "url": "https://example.test/item/1", "selector": "#one"},
        {"field": "price", "value": "999", "url": "https://example.test/item/2", "selector": "#two"},
    ]
    evidence = BrowserRuntimeRail._evaluate_evidence(
        {"result": rows, "page_state": {"url": rows[0]["url"], "generation_id": "g1"}}, {},
        required_fields=["price"],
    )
    assert evidence["values"]["price"] == "10.27"
    assert evidence["provenance"]["price"]["selector"] == "#one"
    assert evidence["entity_url"] == rows[0]["url"]


def test_same_entity_detail_corrects_weak_listing_but_not_other_entity():
    old = {"entity": "product", "variant": "default", "field": "price", "value": "10", "status": "present",
           "entity_source": "https://example.test/item/1", "source": "https://example.test/search",
           "evidence_scope": "listing"}
    state = {"evidence_slots": [old]}
    merge_evidence_slot(state, {**old, "value": "10.27", "source": old["entity_source"], "evidence_scope": "detail"})
    assert state["evidence_slots"][0]["value"] == "10.27"
    merge_evidence_slot(state, {**old, "value": "999", "entity_source": "https://example.test/item/2"})
    assert state["evidence_slots"][0]["value"] == "10.27"


def test_timeout_preserves_observations_and_cancel_is_nonretryable():
    session = _FakeSession()
    state = BrowserRuntimeRail._build_phase_state("Read weather")
    state["structured_evidence"] = [{"kind": "page_observation", "source": "https://example.test/weather",
                                     "generation_id": "g2", "raw_text": "Current temperature 32 C"}]
    session.update_state({STATE_KEY: state})
    result = BrowserRuntimeRail.interrupted_task_result(session, "task_deadline_exhausted")
    assert result["authoritative_browser_result"]["status"] == "partial"
    assert result["authoritative_browser_result"]["observations"]
    result = BrowserRuntimeRail.interrupted_task_result(session, "browser_subagent_cancelled")
    assert not result["authoritative_browser_result"]["retryable"]


def test_raw_observation_can_be_recalled_only_in_its_session(tmp_path):
    tool = BrowserOffloadRecallTool(tmp_path)
    session = _FakeSession("one")
    text = "prefix " * 2000 + "IMPORTANT MIDDLE CONTROL" + " suffix" * 2000
    handle = _run(tool.persist_observation(session, text, "browser_snapshot"))
    recalled = _run(tool.invoke({"handle": handle, "query": "IMPORTANT"}, session=session))
    assert recalled.success and "IMPORTANT MIDDLE CONTROL" in recalled.data["content"]
    other = _run(tool.invoke({"handle": handle}, session=_FakeSession("two")))
    assert not other.success


def test_native_projection_offloads_before_loss(tmp_path):
    tool = BrowserOffloadRecallTool(tmp_path)
    runtime = _make_bare_runtime()
    runtime.persist_observation = lambda text, name: tool.persist_observation(_FakeSession("native"), text, name)
    ax = "- generic: filler\n" * 1000 + '- textbox "Search" [ref=e1]'
    projected = _run(runtime.project_native_observation(ax, "browser_snapshot"))
    assert "Search" in projected and "persisted-output" in projected
    path = next(tmp_path.glob("context/native_context/offload/BrowserObservation_*.json"))
    assert json.loads(path.read_text(encoding="utf-8"))["messages"][0]["content"] == ax


def test_completed_contradiction_allows_one_correction_not_unlimited_reopens():
    session = _FakeSession()
    tool = TaskTool(ToolCard(name="task_tool"), MagicMock())
    query = tool._prepare_browser_query(session, "parent", "进入第一篇结果，返回作者", "")
    query.record["sub_session_id"] = "parent_sub_browser_agent_one"
    tool._save_browser_result(session, query, {
        "status": "completed", "retryable": False,
        "current_page": {"url": "https://example.test/search?q=fastapi"},
        "evidence": [{"field": "author", "value": "查看主页"}],
    })
    query.task_description = "已知作者是按钮，进入已找到的文章纠正作者"
    resumed = tool._prepare_existing_browser_query(session, query)
    assert resumed.early_output is None and resumed.record["resume_count"] == 1
    assert resumed.record["deadline_at"] == query.record["deadline_at"]
    assert tool._prepare_existing_browser_query(session, resumed).early_output is not None


def test_raw_observation_retention_expires_other_tasks_without_deleting_window_artifacts(tmp_path, monkeypatch):
    from openjiuwen.harness.tools.browser_move import offload_recall

    tool = BrowserOffloadRecallTool(tmp_path)
    _run(tool.persist_observation(_FakeSession("old-task"), "original", "browser_snapshot"))
    artifact = next(tmp_path.glob("context/old-task_context/offload/BrowserObservation_*.json"))
    old = time.time() - 90000
    os.utime(artifact, (old, old))
    window_artifact = artifact.with_name("ToolResultWindowProcessor_keep.json")
    window_artifact.touch()
    os.utime(window_artifact, (old, old))
    monkeypatch.delitem(offload_recall._LAST_EXPIRY_SCAN, tmp_path / "context")
    new_tool = BrowserOffloadRecallTool(tmp_path)
    _run(new_tool.persist_observation(_FakeSession("new-task"), "new", "browser_snapshot"))
    assert not artifact.exists()
    assert window_artifact.exists()


@pytest.mark.asyncio
async def test_concurrent_observations_respect_per_task_file_limit(tmp_path, monkeypatch):
    from openjiuwen.harness.tools.browser_move import offload_recall

    monkeypatch.setattr(offload_recall, "_MAX_OBSERVATIONS", 2)
    tool = BrowserOffloadRecallTool(tmp_path)
    session = _FakeSession("parallel-probes")
    handles = await asyncio.gather(*(
        tool.persist_observation(session, f"result {index}", "browser_probe_cards") for index in range(4)
    ))
    assert len(set(handles)) == 4
    assert len(list(tmp_path.glob("context/parallel-probes_context/offload/BrowserObservation_*.json"))) == 2


def test_storage_failure_keeps_native_observation_instead_of_irrecoverable_truncation():
    runtime = _make_bare_runtime()
    runtime.persist_observation = AsyncMock(side_effect=OSError("disk unavailable"))
    text = "- generic: filler\n" * 1000
    assert _run(runtime.project_native_observation(text, "browser_snapshot")) == text
    inputs = SimpleNamespace(tool_msg=ToolMessage(content=text, tool_call_id="snapshot"))
    BrowserRuntimeRail._compact_large_observation_message(inputs, "browser_snapshot", text)
    assert inputs.tool_msg.content == text


def test_projected_page_state_keeps_recall_handle_even_in_minimal_fallback(tmp_path):
    tool = BrowserOffloadRecallTool(tmp_path)
    session = _FakeSession("state-projection")
    provider = SimpleNamespace(persist_observation=lambda text, name: tool.persist_observation(session, text, name))
    processor = BrowserStateContextProcessor(BrowserStateContextProcessorConfig(provider=provider, max_dom_chars=2000))
    state = {"ok": True, "url": "https://example.test", "dom": "- generic: " + "important text " * 2000}
    _run(processor._preserve_projected_state(state))
    rendered = processor._format_state_text(state)
    assert state["projection_recall_handle"] in rendered
    recalled = _run(tool.invoke({"handle": state["projection_recall_handle"], "query": "important"}, session=session))
    assert recalled.success and "important text" in recalled.data["content"]


def test_deadline_does_not_certify_inferred_coverage_as_completed():
    session = _FakeSession()
    state = BrowserRuntimeRail._build_phase_state("Read weather")
    state["structured_evidence"] = [{"kind": "page_observation", "source": "https://example.test",
                                     "raw_text": "Temperature 32 C"}]
    state["deadline_at"] = time.time() - 1
    session.update_state({STATE_KEY: state})
    ctx = AgentCallbackContext(agent=MagicMock(), session=session, inputs=ModelCallInputs(), extra={})
    assert BrowserRuntimeRail._finish_if_task_deadline_exhausted(ctx, session, state)
    assert state["status"] == "partial" and state["terminal_reason"] == "task_deadline_exhausted"


def test_concrete_correction_does_not_bypass_cancel_or_deadline():
    state = BrowserRuntimeRail._build_phase_state("Enter first result and return author")
    state.update({
        "status": "completed", "deadline_at": time.time() - 1,
        "evidence_slots": [{"entity": "page", "variant": "default", "field": "author", "value": "查看主页"}],
    })
    assert not BrowserRuntimeRail._authoritative_terminal_payload(state)["retryable"]
    state["deadline_at"] = time.time() + 100
    state["blockers"] = ["browser_subagent_cancelled"]
    assert not BrowserRuntimeRail._authoritative_terminal_payload(state)["retryable"]


def test_ax_disabled_control_is_not_actionable():
    state = BrowserPageState()
    state.register_ax_snapshot('- button "Submit" [ref=e1] [disabled]\n- tab "Sales" [selected] [ref=e2]')
    targets = state.export()["interactives"]
    submit = next(target for target in targets if target["text"] == "Submit")
    assert not submit["enabled"] and not submit["actionable"]
    selected = next(target for target in targets if target["text"] == "Sales")
    assert selected["selected"] and selected["selected_source"] == "ax"


@pytest.mark.asyncio
@pytest.mark.parametrize("interruption", ["deadline", "provider", "cancel", "outer_deadline"])
async def test_task_tool_interruption_preserves_evidence_and_stops_producer(monkeypatch, interruption):
    from openjiuwen.harness.tools.subagent import task_tool as task_tool_module

    child = _FakeSession("child")
    child.pre_run = AsyncMock()
    child.post_run = AsyncMock()
    started, stopped = asyncio.Event(), asyncio.Event()

    class Browser:
        card = AgentCard(name="browser_agent", id="openjiuwen.browser_agent")
        cleanup_task_resources = AsyncMock()

        async def stream(self, inputs, *, session):
            state = BrowserRuntimeRail._build_phase_state(inputs["query"])
            state["structured_evidence"] = [{
                "kind": "page_observation", "source": "https://example.test/weather",
                "generation_id": "g1", "raw_text": "Current temperature 32 C",
            }]
            session.update_state({STATE_KEY: state})
            started.set()
            try:
                yield {"type": "llm_output", "payload": {"content": "Reading weather"}}
                if interruption == "provider":
                    raise TimeoutError("provider stalled")
                await asyncio.Event().wait()
            finally:
                stopped.set()

    browser = Browser()
    if interruption == "outer_deadline":
        async def slow_cleanup():
            await asyncio.sleep(1)
        browser.cleanup_task_resources.side_effect = slow_cleanup
    tool = TaskTool(ToolCard(name="task_tool"), SimpleNamespace(create_subagent=lambda *_a, **_kw: browser))
    monkeypatch.setattr(task_tool_module, "create_agent_session", lambda **_kw: child)
    if interruption == "deadline":
        monkeypatch.setattr(task_tool_module, "DEFAULT_SUBAGENT_TASK_TIMEOUT_S", 5.05)
    parent = Session(session_id=f"interruption-{interruption}")
    if interruption == "outer_deadline":
        parent.update_state({task_tool_module.EXECUTION_DEADLINE_STATE_KEY: time.time() + 0.2})
    pending = asyncio.create_task(tool.invoke(
        {"subagent_type": "browser_agent", "task_description": "Read the current weather"}, session=parent,
    ))
    await asyncio.wait_for(started.wait(), timeout=2)
    if interruption == "cancel":
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pending
        result = BrowserRuntimeRail.interrupted_task_result(child, "browser_subagent_cancelled")
        payload = result["authoritative_browser_result"]
        assert not payload["retryable"]
    else:
        output = await asyncio.wait_for(pending, timeout=2)
        payload = output.data["browser_result"]
        reason = (
            "task_deadline_exhausted" if interruption in {"deadline", "outer_deadline"}
            else "model_provider_unavailable"
        )
        assert payload["terminal_reason"] == reason
        if interruption == "outer_deadline":
            assert output.data["code"] == "browser_query_deadline_expired"
    assert payload["status"] == "partial" and payload["observations"]
    assert stopped.is_set()
    browser.cleanup_task_resources.assert_awaited_once()
    child.post_run.assert_awaited_once()
    assert not tool._active_browser_queries
