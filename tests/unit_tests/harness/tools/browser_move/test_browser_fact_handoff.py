# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Section 26 interruption/handoff regressions; no browser or provider traffic."""

import json
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from openjiuwen.core.foundation.tool import ToolCard
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext
from openjiuwen.harness.tools.browser_move.playwright_runtime.runtime import BrowserRuntimeRail as Rail
from openjiuwen.harness.tools.subagent import task_tool as task_module
from openjiuwen.harness.tools.subagent.task_tool import TaskTool
from tests.unit_tests.harness.tools.browser_move.test_browser_observation_results import record
from tests.unit_tests.harness.tools.browser_move.test_browser_runtime_rail import _FakeSession

KEY = "__browser_phase_budget_state__"
DISCONNECT = ("[181001] model call failed, reason: openAI API async stream error: RemoteProtocolError: "
              "peer closed connection without sending complete message body (incomplete chunked read)")
REPEAT = ("[181001] model call failed, reason: LLM repeated stream output detected: "
          "field=reasoning_content, unit='mm, h', repeat_count=32")


def cart_session():
    session = _FakeSession()
    state = Rail._build_phase_state("Choose the specified item and add it to the cart")
    state.update(deadline_at=time.time() + 120, resume_count=0)
    state["execution_journal"] = [{
        "call_id": "add", "tool": "browser_click", "execution_state": "acknowledged",
        "executed": True, "impact": "business", "cart_mutation": True,
        "steps": [{"index": 0, "op": "click", "execution_state": "acknowledged", "executed": True}],
    }]
    record(state, "Cart: 3 items, including the selected monitor", "https://shop.test/cart")
    session.update_state({KEY: state})
    return session, state


@pytest.mark.asyncio
@pytest.mark.parametrize("message,code,retryable", [
    (DISCONNECT, "model_transport_interrupted", True),
    (REPEAT, "model_repeated_output", False),
    ("invalid api key", "model_error", False),
    ("stream frame timeout: stage=first_token", "model_first_chunk_timeout", True),
    ("stream frame timeout: stage=idle_token", "model_idle_chunk_timeout", True),
])
async def test_model_errors_preserve_ack_and_sources_independently_of_retryability(message, code, retryable):
    session, state = cart_session()
    ctx = AgentCallbackContext(agent=MagicMock(), session=session, retry_attempt=2,
                               exception=RuntimeError(message),
                               extra={"__browser_task_deadline__": time.monotonic() + 120})
    await Rail(MagicMock()).on_model_exception(ctx)
    assert ctx.consume_retry_request() is None
    result = ctx.consume_force_finish().result["authoritative_browser_result"]
    assert result["status"] == "partial"
    assert result["terminal_reason"] == code
    assert result["retryable"] is retryable
    assert result["current_page"]["url"] == "https://shop.test/cart"
    assert "3 items" in str(result["observations"])
    assert result["execution"]["business_effects"][0]["execution_state"] == "acknowledged"
    assert state["execution_journal"][0]["executed"] is True


@pytest.mark.asyncio
async def test_existing_repeat_retry_is_not_overridden_or_extended():
    session, _ = cart_session()
    rail = Rail(MagicMock())
    ctx = AgentCallbackContext(agent=MagicMock(), session=session, exception=RuntimeError(REPEAT),
                               extra={"__browser_task_deadline__": time.monotonic() + 120})
    ctx.request_retry(delay_seconds=0.3)
    await rail.on_model_exception(ctx)
    assert ctx.consume_retry_request().delay_seconds == 0.3
    assert ctx.consume_force_finish() is None
    ctx.retry_attempt = 2
    await rail.on_model_exception(ctx)
    assert ctx.consume_retry_request() is None
    assert not ctx.consume_force_finish().result["authoritative_browser_result"]["retryable"]


@pytest.mark.asyncio
async def test_runtime_does_not_override_no_retry_after_visible_output():
    from openjiuwen.core.single_agent.rail.base import MODEL_VISIBLE_OUTPUT_EMITTED_KEY

    session, _ = cart_session()
    ctx = AgentCallbackContext(agent=MagicMock(), session=session, exception=RuntimeError(DISCONNECT),
                               extra={"__browser_task_deadline__": time.monotonic() + 120,
                                      MODEL_VISIBLE_OUTPUT_EMITTED_KEY: True})
    await Rail(MagicMock()).on_model_exception(ctx)
    assert ctx.consume_retry_request() is None
    assert ctx.consume_force_finish().result["authoritative_browser_result"]["observations"]


@pytest.mark.asyncio
async def test_actual_anomaly_rail_and_browser_rail_share_existing_retry_budget():
    from openjiuwen.harness.rails.model_anomaly_detection_rail import ModelAnomalyDetectionRail

    session, _ = cart_session()
    anomaly = ModelAnomalyDetectionRail(max_retries=2)
    runtime = Rail(MagicMock())
    ctx = AgentCallbackContext(agent=MagicMock(), session=session, exception=RuntimeError(REPEAT),
                               extra={"__browser_task_deadline__": time.monotonic() + 120})
    for attempt in range(3):
        ctx.retry_attempt = attempt
        await anomaly.on_model_exception(ctx)
        await runtime.on_model_exception(ctx)
        if attempt < 2:
            assert ctx.consume_retry_request() is not None
            assert ctx.consume_force_finish() is None
        else:
            assert ctx.consume_retry_request() is None
            result = ctx.consume_force_finish().result["authoritative_browser_result"]
            assert result["terminal_reason"] == "model_repeated_output"
            assert result["observations"] and not result["retryable"]


@pytest.mark.asyncio
async def test_task_tool_generic_exception_returns_facts_and_one_focused_resume(monkeypatch):
    child, state = cart_session()
    child.pre_run = AsyncMock()
    child.post_run = AsyncMock()
    parent = _FakeSession("parent")
    tool = TaskTool(ToolCard(name="task_tool"), MagicMock())
    query = tool._prepare_browser_query(parent, "parent", state["task"], "")
    query.record["sub_session_id"] = "child"
    deadline = query.record["deadline_at"]
    subagent = SimpleNamespace(card=SimpleNamespace(id="browser", name="browser"), invoke=AsyncMock())
    monkeypatch.setattr(tool, "_accepts_runtime_session", lambda _: True)
    monkeypatch.setattr(task_module, "create_agent_session", lambda **_: child)
    monkeypatch.setattr(task_module, "prepare_subagent_task_resources", AsyncMock())
    monkeypatch.setattr(task_module, "cleanup_subagent_task_resources", AsyncMock())
    invoke = AsyncMock(side_effect=RuntimeError(DISCONNECT))
    monkeypatch.setattr(tool, "_invoke_with_usage_delegation", invoke)
    output = await tool._invoke_created_subagent(
        subagent, normalized_type="browser_agent", task_description=state["task"], sub_session_id="child",
        parent_session_id="parent", parent_cache_id="parent", parent_session=parent,
        browser_query=query, affinity_enabled=False,
    )
    result = output.data["browser_result"]
    assert result["status"] == "partial" and result["retryable"]
    assert "3 items" in str(result["observations"])
    assert result["execution"]["business_effects"][0]["executed"] is True
    invoke.assert_awaited_once()  # This boundary returns facts; it does not retry browser work.
    resumed = tool._prepare_browser_query(parent, "parent", "Read the current cart only", "child")
    assert resumed.early_output is None
    assert resumed.record["deadline_at"] == deadline and resumed.record["resume_count"] == 1
    assert "never blindly replay" in resumed.task_description
    child.post_run.assert_awaited_once()


@pytest.mark.parametrize("boundary", ["deadline", "repeat", "fatal", "resume_limit"])
def test_retained_facts_do_not_override_recovery_limits(boundary):
    session, state = cart_session()
    if boundary == "deadline":
        state["deadline_at"] = time.time() - 1
    if boundary == "resume_limit":
        state["resume_count"] = 1
    failure = Rail._model_failure_details(RuntimeError(
        REPEAT if boundary == "repeat" else "bad configuration" if boundary == "fatal" else DISCONNECT))
    result = Rail.interrupted_task_result(session, failure["code"], failure=failure)["authoritative_browser_result"]
    assert not result["retryable"]
    assert result["observations"] and result["execution"]["business_effects"]


def test_outer_failure_keeps_previous_facts_and_explains_query_denial():
    child, _ = cart_session()
    parent = _FakeSession("parent")
    tool = TaskTool(ToolCard(name="task_tool"), MagicMock())
    query = tool._prepare_browser_query(parent, "parent", "Check cart", "")
    original = Rail.interrupted_task_result(child, "model_transport_interrupted")["authoritative_browser_result"]
    tool._save_browser_result(parent, query, original)
    tool._set_browser_query_failure(parent, query.records, query.record, "browser_subagent_execution_failed")
    saved = query.record["browser_result"]
    assert saved["observations"] == original["observations"]
    assert saved["execution"] == original["execution"]
    query.record["deadline_at"] = time.time() - 1
    denial = tool._existing_browser_query_output(query.record, code="browser_query_resume_not_allowed").data
    assert denial["resume_denial_reason"] == "task_deadline_exhausted"
    assert denial["browser_result"]["observations"]


def test_resume_setup_failure_preserves_saved_facts_in_body_and_structured_result():
    child, _ = cart_session()
    parent = _FakeSession("parent")
    tool = TaskTool(ToolCard(name="task_tool"), MagicMock())
    query = tool._prepare_browser_query(parent, "parent", "Read cart", "")
    previous = Rail.interrupted_task_result(child, "model_transport_interrupted")["authoritative_browser_result"]
    tool._save_browser_result(parent, query, previous)
    failure = Rail._model_failure_details(RuntimeError("setup failure"))
    result = Rail.interrupted_task_result(None, "browser_execution_error", failure=failure)
    output = tool._build_task_output(
        result, normalized_type="browser_agent", sub_session_id="child", parent_session=parent,
        browser_query=query, subagent=SimpleNamespace(card=SimpleNamespace(id="browser")),
    )
    retained = output.data["browser_result"]
    assert retained["observations"] == previous["observations"]
    assert retained["execution"] == previous["execution"]
    assert retained["observation_excerpt"] == previous["observation_excerpt"]
    assert retained["status"] == "partial" and not retained["retryable"]
    assert "3 items" in output.data["output"]


def test_multisource_answer_survives_later_diagnostics_without_more_reads():
    state = Rail._build_phase_state("Compare both sorts and the two opened details")
    record(state, "Default sort: first result A", "https://search.test/?order=default")
    record(state, "Latest sort: first result B", "https://search.test/?order=latest")
    record(state, "Detail A: original restriction", "https://detail.test/a")
    record(state, "Detail B: original restriction", "https://detail.test/b")
    for index in range(25):
        record(state, f"Footer diagnostic {index}", "https://detail.test/b")
    state["status"] = "completed"
    result = Rail._authoritative_terminal_payload(state)
    sources = {item["source"] for item in result["observations"]}
    assert sources == {"https://search.test/?order=default", "https://search.test/?order=latest",
                       "https://detail.test/a", "https://detail.test/b"}
    assert "Default sort: first result A" in str(result["observations"])
    assert "Latest sort: first result B" in str(result["observations"])
    assert len(state["structured_evidence"]) <= 20 and len(result["observations"]) <= 20
    assert not result["observation_excerpt"]["selection_truncated"]
    assert result["observation_excerpt"]["exhaustive"] is False
    assert result["status"] == "completed" and not result["missing_fields"]


def test_source_excerpt_deduplicates_and_keeps_scope_and_raw_reference():
    state = Rail._build_phase_state("Read details")
    page = {"url": "https://detail.test/a", "generation_id": "g1", "page_id": "p1", "interaction_revision": 3}
    state["last_page"] = page
    Rail._record_structured_evidence(
        state, {"result": {"text": "Original conditions"}, "page_state": page, "recall_handle": "raw_1"},
        tool_name="browser_page_action", tool_args={"op": "read_text"},
    )
    original = next(item for item in state["structured_evidence"] if item.get("raw_text"))
    state["structured_evidence"].append(dict(original))
    facts = [item for item in Rail._authoritative_terminal_payload(state)["observations"]
             if item["raw_text"] == "Original conditions"]
    assert len(facts) == 1
    assert facts[0]["recall_handle"] == "raw_1"
    assert facts[0]["observation_scope"]["interaction_revision"] == 3
    assert facts[0]["generation_id"] == "g1"


def test_recommendations_remain_sourced_raw_content_not_cart_rows():
    state = Rail._build_phase_state("Read cart")
    state["last_page"] = {"url": "https://shop.test/cart"}
    result = {"ok": True, "cards": [{"title": "Recommended cable", "kind": "product",
                                   "region": "recommendation", "is_ad": True,
                                   "provenance": {"selector_source": "observed_container"},
                                   "primary_link": "https://shop.test/cable"}],
              "page_state": state["last_page"]}
    Rail._record_structured_evidence(state, result, tool_name="browser_probe_cards", tool_args={})
    raw = next(item for item in Rail._task_observations(state) if item["kind"] == "page_observation")
    assert '"region":"recommendation"' in raw["raw_text"] or '"region": "recommendation"' in raw["raw_text"]
    assert "is_ad" in raw["raw_text"]
    assert "observed_container" in raw["raw_text"]
    content_hash = raw["content_hash"]
    result["cards"][0]["provenance"]["capture_id"] = "another_capture"
    repeated = Rail._page_observation_evidence(state, result, "browser_probe_cards", {})
    assert repeated["content_hash"] == content_hash
    assert state.get("observed_result_count", 0) == 0
    assert not state["evidence_slots"]


def test_inspection_handoff_then_selected_control_reuses_existing_menu_contract():
    from openjiuwen.harness.tools.browser_move.decision.action_space import build_menu
    from openjiuwen.harness.tools.browser_move.playwright_runtime.phase_contract import constrain_actions, set_phase
    from tests.unit_tests.harness.tools.browser_move.test_browser_jev_phase_contract import control

    controls = [control("selected_add", "Add to Cart", 1), control("other_add", "Add to Cart", 2)]
    state = Rail._build_phase_state("Compare candidate specifications then add the selected item")
    set_phase(state, {"objective": "Inspect candidates; selection is not decided",
                      "allowed_operations": ["read_text", "find", "snapshot", "scroll", "navigate"]}, controls)
    menu = build_menu(controls, state["task"], limit=30)
    assert any(step["op"] == "click" for step in menu.steps.values())
    constrain_actions(menu, state, controls)
    assert not any(step["op"] == "click" for step in menu.steps.values())
    set_phase(state, {"objective": "Execute the selected item's observed add control",
                      "allowed_operations": ["click"], "target_ids": ["selected_add"]}, controls)
    menu = build_menu(controls, state["task"], limit=30)
    constrain_actions(menu, state, controls)
    assert {s["target_id"] for s in menu.steps.values() if s["op"] == "click"} == {"selected_add"}
    assert not state.get("phase_requirements")  # No business proof or per-step certification.


@pytest.mark.parametrize("text,expected", [
    ("打开（https://www.lazada.sg）把商品加入购物车", "https://www.lazada.sg"),
    ("访问 https://例子.测试/中文路径?关键词=显示器", "https://例子.测试/中文路径?关键词=显示器"),
    ("See [reference](https://example.test/wiki/Function_(math)).", "https://example.test/wiki/Function_(math)"),
    ("Read https://example.test/文档（中文）?q=第一项", "https://example.test/文档（中文）?q=第一项"),
    ("Use https://[::1]:8443/path?q=%EF%BC%89", "https://[::1]:8443/path?q=%EF%BC%89"),
    ("【https://example.test/path】然后读取", "https://example.test/path"),
])
def test_url_prose_delimiters_keep_literal_semantics(text, expected):
    from openjiuwen.harness.tools.browser_move.decision.intent import explicit_urls
    assert explicit_urls(text) == [expected]


@pytest.mark.asyncio
@pytest.mark.parametrize("ending", ["success", "disconnect", "cancel"])
async def test_stream_timing_distinguishes_framing_reasoning_content_and_failure(monkeypatch, ending):
    import asyncio

    from openjiuwen.core.foundation.llm.schema.message import ToolCall
    from openjiuwen.core.foundation.llm.schema.message_chunk import AssistantMessageChunk
    from openjiuwen.harness.tools.browser_move.decision import policy_model as module
    from tests.unit_tests.harness.tools.browser_move.test_browser_jev_policy import answer
    from tests.unit_tests.harness.tools.browser_move.test_browser_model_usage import collect, prepared

    policy, llm, client, messages, session, _, _ = await prepared()
    client.evaluate.return_value = answer("HANDOFF")
    logs = []
    monkeypatch.setattr(module, "browser_agent_log_info", lambda fmt, *args: logs.append(fmt % args))
    private_reasoning = "private reasoning text must never appear in timing logs"

    async def stream(**_):
        yield AssistantMessageChunk(content="")  # framing, not a token
        yield AssistantMessageChunk(content="", reasoning_content=private_reasoning)
        yield AssistantMessageChunk(content="answer")
        yield AssistantMessageChunk(content="", tool_calls=[
            ToolCall(id="t", type="function", name="reader", arguments="{}"),
        ])
        if ending == "disconnect":
            raise RuntimeError(DISCONNECT)
        if ending == "cancel":
            raise asyncio.CancelledError()

    llm.stream = stream
    if ending == "success":
        chunks = [chunk async for chunk in policy.stream(messages)]
        assert len(chunks) == 4
    else:
        with pytest.raises(RuntimeError if ending == "disconnect" else asyncio.CancelledError):
            async for _ in policy.stream(messages):
                pass
    timing = [json.loads(item.split("[BROWSER_TIMING] ")[1]) for item in logs if "[BROWSER_TIMING]" in item]
    metrics = next(item["stream"] for item in timing if item["component"] == "llm")
    assert metrics["chunk_count"] == 4 and metrics["output_chunk_count"] == 3
    assert metrics["content_chars"] == 6 and metrics["reasoning_chars"] == len(private_reasoning)
    assert metrics["tool_call_chunks"] == 1
    assert (metrics["first_chunk_ms"] <= metrics["first_output_ms"]
            <= metrics["last_output_ms"] <= metrics["last_chunk_ms"])
    assert metrics["status"] == {"success": "succeeded", "disconnect": "failed", "cancel": "cancelled"}[ending]
    assert private_reasoning not in str(timing)
    assert collect(session)["llm"]["usage_unknown_calls"] == 1
