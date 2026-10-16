# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Observation/result regressions from the October 8 failure trajectories."""

import json
import time
from unittest.mock import MagicMock

import pytest

from openjiuwen.core.foundation.tool import ToolCard, ToolOutput
from openjiuwen.harness.tools.browser_move.controllers.action import validate_batch_steps
from openjiuwen.harness.tools.browser_move.playwright_runtime.browser_working_context import BrowserWorkingContextStore
from openjiuwen.harness.tools.browser_move.playwright_runtime.runtime import BrowserRuntimeRail as Rail
from openjiuwen.harness.tools.subagent.task_tool import TaskTool
from tests.unit_tests.harness.tools.browser_move.test_browser_runtime_rail import _declare_output, _FakeSession

KEY = "__browser_phase_budget_state__"


def record(state, text, url="https://example.test/detail/1"):
    state["last_page"] = {"url": url, "generation_id": "g3"}
    Rail._record_structured_evidence(
        state, {"result": {"text": text}, "page_state": dict(state["last_page"])},
        tool_name="browser_page_action", tool_args={"op": "read_text"},
    )


def finish(state, summary, status="completed", **reported):
    session = _FakeSession()
    session.update_state({KEY: state})
    Rail._apply_worker_progress_to_task_state(session, {"status": status, **reported}, summary)
    return Rail._render_authoritative_terminal_output(state, summary)[1]


@pytest.mark.parametrize("goal", [
    "返回标题、作者、评论数和收藏数", "对比综合与最新排序的第一条结果",
    "Return hotel stars and guest rating", "Return price and comments if available",
    "Read the titles of these two documentation pages", "返回当前分集时长，不要合集时长",
    "读取购物车中的实际商品和总价", "删除所选商品并调整规格",
])
def test_default_goal_does_not_compile_a_business_output_contract(goal):
    state = Rail._build_phase_state(goal)
    assert state["requirements_source"] == "observations"
    assert state["required_fields"] == state["required_evidence_slots"] == []
    assert state["requested_result_count"] == 0


@pytest.mark.parametrize("mode", ["llm", "hybrid"])
def test_unmapped_source_text_completes_without_field_projection_or_repair(monkeypatch, mode):
    state = Rail._build_phase_state("读取配置、限制和页面标题")
    state["decision_policy"] = {"mode": mode}
    monkeypatch.setattr(Rail, "_record_evidence_slots", MagicMock(side_effect=AssertionError("default slot work")))
    monkeypatch.setattr(Rail, "_evaluate_evidence", MagicMock(side_effect=AssertionError("default field mapping")))
    monkeypatch.setattr(BrowserWorkingContextStore, "refresh_field_coverage",
                        MagicMock(side_effect=AssertionError("default coverage work")))
    record(state, "Page heading: Device options. Variant A is non-cancellable; custom flag=enabled.")
    result = finish(state, "Variant A is non-cancellable; custom flag is enabled.")
    assert result["status"] == "completed" and not result["retryable"]
    assert result["missing_fields"] == result["missing_slots"] == []
    assert "unverified_fields" not in result and "acceptance" not in result
    assert "non-cancellable" in json.dumps(result["observations"])
    assert not state["evidence_slots"] and not state["field_coverage"]


def test_each_read_keeps_its_source_without_collapsing_page_titles_into_one_slot():
    state = Rail._build_phase_state("Read the titles of two documentation pages")
    record(state, "Page title: Language Reference", "https://docs.test/reference")
    record(state, "Page title: Standard Library", "https://docs.test/library")
    result = finish(state, "Language Reference; Standard Library")
    assert result["status"] == "completed"
    observed = {item["source"]: item["raw_text"] for item in result["observations"]}
    assert "Language Reference" in observed["https://docs.test/reference"]
    assert "Standard Library" in observed["https://docs.test/library"]
    assert not result["requested_slots"]


def test_returning_to_search_after_a_detail_read_does_not_revoke_worker_completion():
    state = Rail._build_phase_state("Open the first search result and read its cancellation policy")
    record(state, "Cancellation policy: non-cancellable.")
    state["last_page"] = {"url": "https://example.test/search?q=hotel", "title": "Search results"}
    result = finish(state, "The opened result says non-cancellable.")
    assert result["status"] == "completed"
    assert result["terminal_reason"] == "worker_completed_with_observations"
    assert not result.get("correction_reason")


@pytest.mark.parametrize("contract", [False, True])
def test_a_cached_page_cannot_replace_the_navigation_receipts_source(contract):
    state = Rail._build_phase_state("Read the opened page")
    if contract:
        _declare_output(state, ["title"])
    result = {"result": "### Page\n- Page URL: https://actual.test/\n- Page Title: Actual page",
              "page_state": {"url": "https://other.test/", "title": "Other tab"}}
    assert Rail._page_metadata_evidence(state, result, "browser_navigate", {"url": "https://actual.test/"}) == {}


@pytest.mark.parametrize("boundary", ["available", "expired", "resume_used"])
def test_only_explicit_correction_can_resume_worker_completion_within_existing_limits(boundary):
    parent = _FakeSession()
    tool = TaskTool(ToolCard(name="task_tool"), MagicMock())
    query = tool._prepare_browser_query(parent, "parent", "Read the selected item's conditions", "")
    sub_session_id = "parent_sub_browser_agent_one"
    query.record["sub_session_id"] = sub_session_id
    tool._save_browser_result(parent, query, {"status": "completed", "retryable": False})
    if boundary == "expired":
        query.record["deadline_at"] = time.time() - 1
    if boundary == "resume_used":
        query.record["resume_count"] = 1
    deadline = query.record["deadline_at"]
    assert tool._prepare_existing_browser_query(parent, query).early_output is not None
    corrected = tool._prepare_browser_query(parent, "parent", "Read the omitted restriction", sub_session_id)
    assert (corrected.early_output is None) is (boundary == "available")
    assert corrected.record["deadline_at"] == deadline
    if boundary == "available":
        assert corrected.record["resume_count"] == 1


def test_explicit_worker_correction_reuses_runtime_observations_journal_and_deadline():
    state = Rail._build_phase_state("Read the conditions")
    record(state, "A restriction was read, but the worker omitted part of it.")
    state.update(status="completed", deadline_at=time.time() + 60, last_worker_final="Incomplete interpretation",
                 execution_journal=[{"call_id": "already_done", "execution_state": "acknowledged"}])
    deadline = state["deadline_at"]
    session = _FakeSession()
    session.update_state({KEY: state})
    Rail(MagicMock())._ensure_task_state(session, "Read the omitted restriction", resume=True)
    assert state["status"] == "in_progress" and state["resume_count"] == 1
    assert state["deadline_at"] == deadline and state["execution_journal"][0]["call_id"] == "already_done"
    assert Rail._task_observations(state) and "last_worker_final" not in state


def test_legacy_inferred_absence_is_retired_without_erasing_facts_or_execution_state():
    state = Rail._build_phase_state("Read the current configuration")
    record(state, "The page does not show a rating; configuration is enabled.")
    state.update(requirements_source="inferred", required_fields=["rating"],
                 required_evidence_slots=[{"entity": "item", "variant": "default", "field": "rating"}],
                 requested_result_count=8, field_coverage=["title"], deadline_at=1234567890,
                 evidence_slots=[{"field": "rating", "status": "unknown", "observation_status": "explicit_absence",
                                  "source": "https://example.test/detail/1", "raw_text": "No rating shown"}],
                 execution_journal=[{"call_id": "unknown", "execution_state": "dispatched_unknown",
                                     "impact": "business"}])
    session = _FakeSession()
    session.update_state({KEY: state})
    Rail(MagicMock())._ensure_task_state(session, state["task"])
    assert state["requirements_source"] == "observations" and not state["required_fields"]
    assert state["deadline_at"] == 1234567890 and state["execution_journal"][0]["call_id"] == "unknown"
    assert Rail._task_observations(state) and state["evidence_slots"][0]["raw_text"] == "No rating shown"
    assert Rail._missing_completion_requirements(state) == Rail._unavailable_evidence_slots(state) == []
    assert Rail._business_missing_requirements(state) == ["unknown_browser_write"]


def test_explicit_output_contract_still_checks_declared_shape():
    state = _declare_output(Rail._build_phase_state("Return the configured table"), ["title", "price"])
    record(state, "The item description is readable but does not provide the requested numeric column.")
    result = finish(state, "Some information is available.")
    assert result["status"] == "partial" and "price" in result["missing_fields"]


@pytest.mark.parametrize("limit", ["permission_denied", "task_deadline_exhausted", "user_cancelled"])
def test_worker_cannot_override_a_terminal_execution_boundary(limit):
    state = Rail._build_phase_state("Read the page")
    record(state, "Some source text is already available.")
    state.update(status="blocked", terminal_reason=limit, blockers=[limit])
    assert finish(state, "Completed")["status"] == "blocked"


def test_unknown_effect_still_prevents_unqualified_completion():
    state = Rail._build_phase_state("Submit the selected item")
    record(state, "Item detail is visible; no confirmation is available.")
    state["execution_journal"] = [{"call_id": "write", "impact": "business",
                                   "execution_state": "dispatched_unknown"}]
    result = finish(state, "Completed")
    assert result["status"] == "partial" and result["missing_fields"] == ["unknown_browser_write"]


def test_parent_and_resume_share_facts_and_do_not_render_three_copies():
    state = Rail._build_phase_state("Add the selected keyboard")
    record(state, "UNIQUE_SOURCE_NOTE: Pre-Order. Cart still contains only the existing mouse.")
    result = finish(state, "The keyboard has not appeared in the cart; the cause is unknown.",
                    status="blocked", blockers=["Page requests manual verification"],
                    next_action="Read the current cart after manual verification")
    wrapped = {"authoritative_browser_result": result}
    output = json.dumps({"browser_result": result}, ensure_ascii=False)
    data = TaskTool._build_result_data(wrapped, output, agent_id="browser", subagent_type="browser_agent",
                                      sub_session_id="parent_sub_browser_agent_one")
    assert data["resume_context"] == data["browser_result"]
    assert data["resume_context"]["verification_scope"] == "execution_and_observations"
    tool = TaskTool(ToolCard(name="task_tool"), MagicMock())
    rendered = tool.render_for_llm(ToolOutput(success=True, data=data))
    assert rendered.count("UNIQUE_SOURCE_NOTE") == 1
    visible = json.loads(rendered)["browser_orchestration"]
    assert "resume_context" not in visible
    assert visible["browser_result"]["status"] == "blocked"
    assert visible["browser_result"]["unconfirmed_blockers"] == ["Page requests manual verification"]
    assert "not exhaustive" in visible["browser_result"]["reporting_guidance"]
    assert "cause is unknown" in visible["browser_result"]["summary"]


@pytest.mark.parametrize("op", ["wait_for_first_card_title", "wait_for_dom_text_change", "wait_for_selector"])
def test_wait_guidance_simplification_does_not_allow_missing_targets(op):
    errors = validate_batch_steps([{"op": op}])
    assert errors
