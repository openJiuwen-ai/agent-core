# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Regression cases for observed page evidence and runtime admission contracts."""
# pylint: disable=protected-access

import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from openjiuwen.core.foundation.llm import ToolCall
from openjiuwen.core.foundation.llm.schema.message import ToolMessage
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext, ModelCallInputs, ToolCallInputs
from openjiuwen.harness.deep_agent import DeepAgent
from openjiuwen.harness.tools.browser_move.playwright_runtime.runtime import BrowserAgentRuntime, BrowserRuntimeRail
from tests.unit_tests.harness.tools.browser_move.test_browser_runtime_rail import _FakeSession, _run


STATE_KEY = "__browser_phase_budget_state__"
WEATHER_URL = "https://www.bing.com/search?q=Singapore+weather"
WEATHER_TEXT = "Singapore: partly sunny, 31 C; high 33 C, low 26 C; humidity 65%; wind 14 km/h."


def _observed_state(task="Report Singapore's high_temperature and low_temperature"):
    state = BrowserRuntimeRail._build_phase_state(task)
    state["last_page"] = {"url": WEATHER_URL, "generation_id": "g3"}
    BrowserRuntimeRail._record_tool_evidence(
        state,
        {"result": {"sel": "#weather", "text": WEATHER_TEXT}, "generation_id": "g3"},
        tool_name="mcp_playwright-official_browser_evaluate",
        tool_args={"function": "() => document.querySelector('#weather').textContent"},
    )
    return state


def test_ax_links_are_not_current_page_metadata():
    ax = '- link "weather" [ref=e1]:\n  - /url: https://weather.example/detail\n'
    assert BrowserAgentRuntime.extract_result_url(ax) == ""
    assert BrowserAgentRuntime.extract_result_url({"cards": [{"url": "https://weather.example/detail"}]}) == ""
    assert BrowserAgentRuntime.extract_result_url(f"### Page\n- Page URL: {WEATHER_URL}\n{ax}") == WEATHER_URL
    assert BrowserAgentRuntime.extract_result_url({"page_state": {"url": WEATHER_URL}}) == WEATHER_URL
    assert BrowserAgentRuntime.extract_result_url({"content": [
        {"type": "text", "text": f"### Page\n- Page URL: {WEATHER_URL}\n{ax}"}
    ]}) == WEATHER_URL


def test_search_results_word_does_not_invent_title_requirement():
    assert BrowserRuntimeRail._infer_required_fields("打开Bing搜索新加坡天气，读取搜索结果中的最低温") == [
        "low_temperature"
    ]
    assert BrowserRuntimeRail._infer_required_fields("Report high_temperature and low_temperature") == [
        "high_temperature", "low_temperature"
    ]


def test_sourced_weather_answer_does_not_require_another_field_mapping_lookup():
    state = _observed_state()
    assert not state["field_coverage"]  # Do not invent typed temperature evidence from arbitrary text.
    session = _FakeSession()
    session.update_state({STATE_KEY: state})
    BrowserRuntimeRail._apply_worker_progress_to_task_state(session, {"status": "completed"}, WEATHER_TEXT)
    output, payload = BrowserRuntimeRail._render_authoritative_terminal_output(state, WEATHER_TEXT)
    assert payload["status"] == "completed"
    assert payload["missing_fields"] == []
    assert payload["unverified_fields"] == ["high_temperature", "low_temperature"]
    assert payload["observations"][0]["source"] == WEATHER_URL
    assert payload["observations"][0]["generation_id"] == "g3"
    assert WEATHER_TEXT in payload["observations"][0]["raw_text"]
    assert json.loads(output)["browser_result"]["summary"] == WEATHER_TEXT


@pytest.mark.parametrize("status", ["partial", "blocked", "failed", "completed"])
def test_terminal_renderer_preserves_useful_summary_without_changing_status(status):
    state = _observed_state()
    state.update(status=status, blockers=["captcha"] if status == "blocked" else [])
    output, payload = BrowserRuntimeRail._render_authoritative_terminal_output(state, WEATHER_TEXT)
    second_output, second_payload = BrowserRuntimeRail._render_authoritative_terminal_output(state, output)
    assert second_payload["status"] == status
    assert second_payload["summary"] == WEATHER_TEXT
    assert second_payload["blockers"] == payload["blockers"]
    assert second_output == output


@pytest.mark.parametrize("case", ["rating", "comparison", "count", "unavailable", "empty", "blocker", "explicit"])
def test_raw_observations_do_not_override_hard_requirements(case):
    task = {
        "rating": "Return product_rating",
        "comparison": "对比B站综合和最新结果的标题",
    }.get(case, "Report low_temperature")
    state = _observed_state(task)
    if case == "count":
        state.update(requested_result_count=3, observed_result_count=1)
    elif case == "unavailable":
        state["evidence_slots"] = [{**state["required_evidence_slots"][0], "status": "unknown", "value": None}]
    elif case == "blocker":
        state["blockers"] = ["login_required"]
    elif case == "explicit":
        state["requirements_source"] = "explicit"
    session = _FakeSession()
    session.update_state({STATE_KEY: state})
    BrowserRuntimeRail._apply_worker_progress_to_task_state(
        session, {"status": "completed"}, "" if case == "empty" else WEATHER_TEXT
    )
    assert state["status"] != "completed"


def test_progress_alias_with_complete_typed_evidence_remains_completed():
    state = BrowserRuntimeRail._build_phase_state("Return title")
    BrowserRuntimeRail._record_tool_evidence(state, {"extracted": {"title": "Weather"}},
                                            tool_name="browser_batch_interact", tool_args={})
    session = _FakeSession()
    session.update_state({STATE_KEY: state})
    ctx = AgentCallbackContext(agent=MagicMock(), session=session, inputs=ToolCallInputs(
        tool_name="browser_progress", tool_args={"status": "completed"},
    ))
    _run(BrowserRuntimeRail(MagicMock(spec=BrowserAgentRuntime)).before_tool_call(ctx))
    result = ctx.consume_force_finish().result["authoritative_browser_result"]
    assert result["status"] == "completed"
    assert result["evidence"][0]["value"] == "Weather"


def test_generation_or_selector_change_does_not_turn_same_observation_into_progress():
    state = _observed_state()
    delta = BrowserRuntimeRail._record_tool_evidence(
        state,
        {"result": {"sel": ".weather", "text": WEATHER_TEXT}, "generation_id": "g9"},
        tool_name="mcp_playwright-official_browser_evaluate",
        tool_args={"function": "() => document.querySelector('.weather').textContent"},
    )
    assert not delta["evidence_added"]
    assert not delta["recovered"]
    assert len(BrowserRuntimeRail._task_observations(state)) == 1


def test_raw_snapshot_evidence_uses_live_page_generation():
    runtime = MagicMock(spec=BrowserAgentRuntime)
    runtime.export_page_state.return_value = {"url": WEATHER_URL, "generation_id": "g3"}
    state = BrowserRuntimeRail._build_phase_state("Report low_temperature")
    session = _FakeSession()
    session.update_state({STATE_KEY: state})
    ctx = AgentCallbackContext(agent=MagicMock(), session=session, inputs=ToolCallInputs(
        tool_name="mcp_playwright-official_browser_snapshot", tool_args={}, tool_result=WEATHER_TEXT,
        tool_msg=ToolMessage(content=WEATHER_TEXT, tool_call_id="snapshot"),
    ))
    _run(BrowserRuntimeRail(runtime).after_tool_call(ctx))
    observation = BrowserRuntimeRail._task_observations(state)[0]
    assert observation["source"] == WEATHER_URL
    assert observation["generation_id"] == "g3"
    assert "low 26 C" in observation["raw_text"]


def test_string_error_is_not_promoted_to_page_evidence_when_wrapped():
    runtime = MagicMock(spec=BrowserAgentRuntime)
    runtime.export_page_state.return_value = {"url": WEATHER_URL, "generation_id": "g3"}
    state = BrowserRuntimeRail._build_phase_state("Report low_temperature")
    session = _FakeSession()
    session.update_state({STATE_KEY: state})
    error = "### Error\nTimeoutError: waiting for weather panel"
    ctx = AgentCallbackContext(agent=MagicMock(), session=session, inputs=ToolCallInputs(
        tool_name="mcp_playwright-official_browser_snapshot", tool_args={}, tool_result=error,
        tool_msg=ToolMessage(content=error, tool_call_id="snapshot-error"),
    ))
    _run(BrowserRuntimeRail(runtime).after_tool_call(ctx))
    assert BrowserRuntimeRail._task_observations(state) == []
    assert state["recent_actions"][-1]["outcome_status"] == "failed"
    runtime.record_tool_reference_state.assert_not_called()


def _admission_context(calls):
    runtime = MagicMock(spec=BrowserAgentRuntime)
    runtime.semantic_progress = {}
    runtime.export_page_state.return_value = {"url": WEATHER_URL, "generation_id": "g3"}
    rail = BrowserRuntimeRail(runtime)
    state = BrowserRuntimeRail._build_phase_state("Read weather details")
    state.update(status="replan_required", replan_required=True, replan_count=0)
    session = _FakeSession()
    session.update_state({STATE_KEY: state})
    ctx = AgentCallbackContext(
        agent=MagicMock(), session=session,
        inputs=ModelCallInputs(response=SimpleNamespace(content="", tool_calls=calls)),
    )
    _run(rail.after_model_call(ctx))
    return rail, ctx, state


def _admit(rail, ctx, call):
    ctx.inputs = ToolCallInputs(tool_call=call, tool_name=call.name, tool_args=call.arguments)
    _run(rail.before_tool_call(ctx))
    return ctx.inputs.tool_result


def test_same_group_parallel_reads_use_one_replan_trial():
    calls = [ToolCall(id=str(index), type="function", name="mcp_playwright-official_browser_find",
                      arguments=json.dumps({"query": query})) for index, query in enumerate(("humidity", "wind"))]
    rail, ctx, state = _admission_context(calls)
    assert _admit(rail, ctx, calls[0]) is None
    assert _admit(rail, ctx, calls[1]) is None
    assert state["replan_count"] == 1
    assert state["replan_trial_pending"] is True


def test_read_trial_does_not_admit_a_mutating_action_in_same_group():
    calls = [
        ToolCall(id="find", type="function", name="mcp_playwright-official_browser_find", arguments='{"query":"wind"}'),
        ToolCall(id="key", type="function", name="mcp_playwright-official_browser_press_key",
                 arguments='{"key":"End"}'),
    ]
    rail, ctx, _ = _admission_context(calls)
    assert _admit(rail, ctx, calls[0]) is None
    assert _admit(rail, ctx, calls[1])["executed"] is False


def test_schema_error_does_not_reserve_or_consume_replan_trial():
    call = ToolCall(id="bad", type="function", name="mcp_playwright-official_browser_snapshot", arguments='{"typo":1}')
    rail, ctx, state = _admission_context([call])
    ctx.agent.ability_manager.get.return_value = SimpleNamespace(
        input_params={"type": "object", "properties": {}, "additionalProperties": False}
    )
    result = _admit(rail, ctx, call)
    assert result["executed"] is False and result["state_changed"] is False
    assert state["replan_count"] == 0
    assert not state.get("replan_trial_pending")
    assert all(not phase["attempts"] for phase in state["phases"].values())
    repaired = call.model_copy(update={"id": "fixed", "arguments": '{"element":"weather"}'})
    assert _admit(rail, ctx, repaired) is None
    assert json.loads(ctx.inputs.tool_args) == {}
    assert state["replan_count"] == 1


def test_server_prefixed_local_batch_is_canonicalized_to_registered_runtime_tool():
    rail = BrowserRuntimeRail(MagicMock(spec=BrowserAgentRuntime))
    assert rail._canonicalize_tool_name("mcp_playwright-official_browser_batch_interact") == "browser_batch_interact"
    assert rail._canonicalize_tool_name("playwright-official-browser_batch_interact") == "browser_batch_interact"


def test_unknown_tool_does_not_consume_semantic_budget():
    call = ToolCall(id="typo", type="function", name="browser_unknown_tool", arguments="{}")
    rail, ctx, state = _admission_context([call])
    ctx.agent.ability_manager.get.return_value = None
    assert _admit(rail, ctx, call)["executed"] is False
    assert state["replan_count"] == 0
    assert all(not phase["attempts"] for phase in state["phases"].values())


def test_typed_run_context_reaches_runtime_deadline_and_resume_contract():
    normalizer = DeepAgent.__new__(DeepAgent)
    inputs = normalizer._normalize_inputs({"query": "only missing fields", "run": {"context": {"extra": {
        "browser_resume": True, "browser_query_id": "q-1", "browser_query_budget_s": 600,
    }}}})
    ctx = AgentCallbackContext(agent=MagicMock(), inputs=inputs)
    assert BrowserRuntimeRail._browser_run_context(ctx)["browser_resume"] is True
    ctx.extra["run_context"] = inputs.run_context
    ctx.inputs = ModelCallInputs()
    assert BrowserRuntimeRail._browser_run_context(ctx)["browser_query_id"] == "q-1"
