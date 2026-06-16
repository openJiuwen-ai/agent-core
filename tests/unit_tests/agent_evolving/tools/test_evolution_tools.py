# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

from unittest.mock import AsyncMock, Mock

import pytest

from openjiuwen.harness.rails.evolution.review.runtime import EvolutionReviewRuntime
from openjiuwen.agent_evolving.tools import (
    EvolveReviewTaskTool,
    MAIN_EVOLUTION_TOOL_NAMES,
    REVIEW_EVOLUTION_TOOL_NAMES,
    create_main_evolution_tools,
)


def _create_main_tools(
    *,
    query_service=None,
    submission_service=None,
    prepare_scope=None,
    review_runtime=None,
):
    runtime = EvolutionReviewRuntime() if review_runtime is None else review_runtime
    return {
        tool.card.name: tool
        for tool in create_main_evolution_tools(
            query_service=Mock() if query_service is None else query_service,
            submission_service=Mock() if submission_service is None else submission_service,
            prepare_scope=runtime.create_scope if prepare_scope is None else prepare_scope,
            review_runtime=runtime,
        )
    }


def test_create_main_evolution_tools_requires_explicit_services():
    with pytest.raises(ValueError, match="query_service, submission_service, and prepare_scope are required"):
        create_main_evolution_tools(query_service=Mock(), prepare_scope=Mock())
    with pytest.raises(ValueError, match="query_service, submission_service, and prepare_scope are required"):
        create_main_evolution_tools(query_service=Mock(), submission_service=Mock())


def test_create_main_evolution_tools_rejects_manager_keyword():
    with pytest.raises(TypeError, match="manager"):
        create_main_evolution_tools(manager=Mock())


def test_main_and_review_evolution_tool_names_are_distinct_surfaces():
    assert MAIN_EVOLUTION_TOOL_NAMES == (
        "prepare_skill_evolution",
        "evolve_review_task",
        "list_skill_experiences",
        "read_skill_experiences",
        "evolve_skill_experiences",
        "simplify_skill_experiences",
    )
    assert tuple(_create_main_tools().keys()) == MAIN_EVOLUTION_TOOL_NAMES
    assert REVIEW_EVOLUTION_TOOL_NAMES == (
        "list_skill_experiences",
        "read_skill_experiences",
        "list_trajectory_steps",
        "read_trajectory_steps",
        "submit_evolution_review",
    )
    assert "list_trajectory_steps" not in MAIN_EVOLUTION_TOOL_NAMES
    assert "read_trajectory_steps" not in MAIN_EVOLUTION_TOOL_NAMES
    assert "submit_evolution_review" not in MAIN_EVOLUTION_TOOL_NAMES
    assert "evolve_skill_experiences" not in REVIEW_EVOLUTION_TOOL_NAMES


def test_evolve_review_task_description_hardens_intent_and_evidence_rules():
    description = EvolveReviewTaskTool._build_task_description(
        {
            "subject": {"kind": "skill", "name": "skill-a"},
            "user_intent": "capture parser failure",
        },
        "review-ref-1",
    )

    assert "user_intent: capture parser failure" in description
    assert "If no task evidence is available but user_intent is present" in description
    assert "evidence_refs=[]" in description
    assert "If no task evidence is available and user_intent is empty" in description
    assert "outcome=no_evolution" in description
    assert "Do not invent execution evidence" in description
    assert "If task evidence is available, first read relevant task and experience evidence" in description
    assert "refs you actually read" in description


@pytest.mark.asyncio
async def test_prepare_tool_is_thin_wrapper_over_review_runtime():
    runtime = EvolutionReviewRuntime()

    def prepare_scope(**kwargs):
        return runtime.create_scope(
            **kwargs,
            scoped_materials={
                "trajectory_steps": [
                    {
                        "ref": "step-1",
                        "index": 0,
                        "kind": "tool",
                        "summary": "tool=bash result_preview=from rail",
                        "has_error": False,
                    }
                ],
                "trajectory_step_details": {
                    "step-1": {
                        "ref": "step-1",
                        "index": 0,
                        "kind": "tool",
                        "detail": {"tool_name": "bash", "call_result": "from rail"},
                    }
                },
            },
        )

    tools = _create_main_tools(prepare_scope=prepare_scope)

    result = await tools["prepare_skill_evolution"].invoke(
        {
            "subject": {"kind": "skill", "name": "skill-a"},
            "user_confirmed": True,
            "user_intent": "capture parser feedback",
        },
        conversation_id="session-1",
    )

    assert result.success is True
    assert result.data["operation"] == "prepare_skill_evolution"
    assert result.data["evolution_review_ref"].startswith("evrr_")
    assert "followup_prompt" not in result.data
    scope = runtime.resolve_scope(result.data["evolution_review_ref"], session_id="session-1")
    assert scope.subject == {"kind": "skill", "name": "skill-a"}
    assert scope.user_intent == "capture parser feedback"
    assert scope.scoped_materials == {
        "trajectory_steps": [
            {
                "ref": "step-1",
                "index": 0,
                "kind": "tool",
                "summary": "tool=bash result_preview=from rail",
                "has_error": False,
            }
        ],
        "trajectory_step_details": {
            "step-1": {
                "ref": "step-1",
                "index": 0,
                "kind": "tool",
                "detail": {"tool_name": "bash", "call_result": "from rail"},
            }
        },
    }


@pytest.mark.asyncio
async def test_prepare_tool_requires_user_confirmation():
    tools = _create_main_tools()

    result = await tools["prepare_skill_evolution"].invoke(
        {
            "subject": {"kind": "skill", "name": "skill-a"},
            "user_intent": "capture parser feedback",
        },
        conversation_id="session-1",
    )

    assert result.success is False
    assert "user_confirmed must be true" in result.error


@pytest.mark.asyncio
async def test_list_tool_uses_query_service_interface():
    query_service = Mock()
    query_service.list_experiences = AsyncMock(return_value={"total_count": 0, "items": []})
    tools = _create_main_tools(query_service=query_service)

    result = await tools["list_skill_experiences"].invoke({"subject": {"kind": "skill", "name": "skill-a"}})

    assert result.success is True
    query_service.list_experiences.assert_awaited_once()


@pytest.mark.asyncio
async def test_read_tool_uses_query_service_interface():
    query_service = Mock()
    query_service.read_experiences = AsyncMock(return_value={"items": [{"record_id": "ev_1"}]})
    tools = _create_main_tools(query_service=query_service)

    result = await tools["read_skill_experiences"].invoke(
        {"subject": {"kind": "skill", "name": "skill-a"}, "record_ids": ["ev_1"]}
    )

    assert result.success is True
    query_service.read_experiences.assert_awaited_once_with(
        {"kind": "skill", "name": "skill-a"},
        record_ids=["ev_1"],
        max_content_chars=2000,
    )


@pytest.mark.asyncio
async def test_evolve_tool_uses_submission_service_interface():
    review_runtime = EvolutionReviewRuntime()
    launch = review_runtime.create_scope(
        source="user",
        subject={"kind": "skill", "name": "skill-a"},
        session_id="session-1",
    )
    review_runtime.record_trajectory_read(launch.evolution_review_ref, session_id="session-1", refs=["step-1"])
    review_runtime.record_review_result(
        launch.evolution_review_ref,
        session_id="session-1",
        result={
            "subject": {"kind": "skill", "name": "skill-a"},
            "outcome": "recommend_evolve",
            "evidence_refs": ["step-1"],
            "proposals": [
                {
                    "proposal_id": "prop_1",
                    "experience": {"summary": "Use parser fields", "content": "Prefer parser fields."},
                    "reason": "reviewed",
                    "evidence_refs": ["step-1"],
                }
            ],
        },
    )
    submission_service = Mock()
    submission_service.apply_prepared_evolve_submission = AsyncMock(
        return_value={"success": True, "operation": "evolve"}
    )
    submission_service.prepare_evolve_submission = Mock(
        side_effect=lambda **kwargs: review_runtime.resolve_selected_proposals(
            kwargs["evolution_review_ref"],
            subject=kwargs["subject"],
            selected_proposal_ids=kwargs["selected_proposal_ids"],
            session_id=kwargs["session_id"],
        )
    )
    review_runtime.consume_prepared_submission = Mock(wraps=review_runtime.consume_prepared_submission)
    tools = _create_main_tools(submission_service=submission_service, review_runtime=review_runtime)

    result = await tools["evolve_skill_experiences"].invoke(
        {
            "subject": {"kind": "skill", "name": "skill-a"},
            "evolution_review_ref": launch.evolution_review_ref,
            "selected_proposal_ids": ["prop_1"],
        },
        conversation_id="session-1",
    )

    assert result.success is True
    submission_service.apply_prepared_evolve_submission.assert_awaited_once()
    assert submission_service.apply_prepared_evolve_submission.await_args.kwargs == {}
    review_runtime.consume_prepared_submission.assert_called_once()
    scope = review_runtime.resolve_scope(launch.evolution_review_ref, session_id="session-1")
    assert scope.status == "submitted"


@pytest.mark.asyncio
async def test_simplify_tool_uses_submission_service_interface():
    submission_service = Mock()
    submission_service.apply_simplify_actions = AsyncMock(return_value={"success": True, "operation": "simplify"})
    tools = _create_main_tools(submission_service=submission_service)

    result = await tools["simplify_skill_experiences"].invoke(
        {
            "subject": {"kind": "skill", "name": "skill-a"},
            "actions": [{"action": "KEEP", "record_id": "ev_1"}],
        }
    )

    assert result.success is True
    submission_service.apply_simplify_actions.assert_awaited_once_with(
        {"kind": "skill", "name": "skill-a"},
        [{"action": "KEEP", "record_id": "ev_1"}],
    )
