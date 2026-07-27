# coding: utf-8
"""Tests for the persistent Goal data model."""
from __future__ import annotations

import pytest

from openjiuwen.harness.goal.schema import (
    GoalAssessment,
    GoalAssessmentStatus,
    GoalOperationError,
    GoalRecord,
    GoalStatus,
    GoalStopConfig,
    GoalStopStrategy,
    TokenUsage,
)


def test_token_usage_accumulates_and_round_trips() -> None:
    usage = TokenUsage()
    usage.accumulate(input_tokens=100, output_tokens=50, cached_input_tokens=20)
    usage.accumulate(input_tokens=4, output_tokens=6)

    assert usage.to_dict() == {
        "input_tokens": 104,
        "output_tokens": 56,
        "cached_input_tokens": 20,
        "total_tokens": 160,
    }
    assert TokenUsage.from_dict(usage.to_dict()) == usage


def test_assessment_round_trip_and_invalid_status() -> None:
    assessment = GoalAssessment(
        status=GoalAssessmentStatus.CONTINUE,
        evidence="implemented the endpoint",
        remaining_work="add tests",
        next_instruction="run the suite",
    )

    assert GoalAssessment.from_dict(assessment.to_dict()) == assessment
    assert GoalAssessment.from_dict({"status": "unexpected", "evidence": "x"}).status is GoalAssessmentStatus.CONTINUE


def test_goal_record_round_trip_and_response_copy() -> None:
    record = GoalRecord.create(
        session_id="session-1",
        objective="Build a REST API",
        max_attempts=4,
        token_budget=8000,
    )
    record.attempt_count = 2
    record.last_assessment = GoalAssessment(
        status=GoalAssessmentStatus.CONTINUE,
        evidence="routes are ready",
    )
    record.touch(bump_revision=True)

    restored = GoalRecord.from_dict(record.to_dict())
    response_copy = record.copy_for_response()
    response_copy.objective = "changed only in the response"

    assert restored.goal_id == record.goal_id
    assert restored.status is GoalStatus.ACTIVE
    assert restored.revision == 1
    assert restored.last_assessment is not None
    assert restored.last_assessment.evidence == "routes are ready"
    assert record.objective == "Build a REST API"


@pytest.mark.parametrize(
    "payload",
    [
        {"goal_id": "g", "session_id": "s", "objective": ""},
        {"goal_id": "g", "session_id": "s", "objective": "x", "status": "bad"},
    ],
)
def test_goal_record_rejects_invalid_persistence_payload(payload: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        GoalRecord.from_dict(payload)


def test_goal_operation_error_keeps_an_isolated_goal_copy() -> None:
    record = GoalRecord.create(session_id="s", objective="original")
    error = GoalOperationError(
        operation="set",
        code="already_exists",
        message="a goal already exists",
        goal=record,
    )
    assert error.goal is not None
    error.goal.objective = "changed"

    assert record.objective == "original"
    assert error.code == "already_exists"


def test_stop_config_defaults() -> None:
    config = GoalStopConfig()

    assert config.strategy is GoalStopStrategy.HYBRID
    assert config.transcript_window_attempts == 8
    assert config.verification_interval is None
