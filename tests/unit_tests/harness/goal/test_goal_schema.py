# coding: utf-8
"""Tests for the persistent Goal data model."""

from __future__ import annotations

from datetime import datetime, timezone, tzinfo

import pytest

import openjiuwen.harness.goal.schema as goal_schema
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


def _utc_timestamp(seconds: int) -> datetime:
    return datetime.fromtimestamp(seconds, tz=timezone.utc)


def _utc_iso(seconds: int) -> str:
    return _utc_timestamp(seconds).isoformat()


def _freeze_goal_clock(monkeypatch: pytest.MonkeyPatch, clock: dict[str, int]) -> None:
    class FrozenDateTime:
        @staticmethod
        def now(tz: tzinfo | None = None) -> datetime:
            current = _utc_timestamp(clock["now"])
            return current if tz is None else current.astimezone(tz)

        @staticmethod
        def fromisoformat(value: str) -> datetime:
            return datetime.fromisoformat(value)

    monkeypatch.setattr(goal_schema, "datetime", FrozenDateTime)


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


def test_goal_record_time_fields_round_trip(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = {"now": 1000}
    _freeze_goal_clock(monkeypatch, clock)
    record = GoalRecord.create(session_id="session-1", objective="Build a REST API")
    assert record.time_used_seconds == 0
    assert record.active_started_at == _utc_iso(1000)
    assert record.created_at == _utc_iso(1000)
    assert record.updated_at == _utc_iso(1000)

    clock["now"] = 1065
    record.settle_active_time(keep_active=False)
    record.status = GoalStatus.PAUSED

    restored = GoalRecord.from_dict(record.to_dict())

    assert restored.time_used_seconds == 65
    assert restored.active_started_at is None
    assert restored.created_at == _utc_iso(1000)
    assert restored.updated_at == _utc_iso(1065)


@pytest.mark.parametrize(
    "payload",
    [
        {"goal_id": "g", "session_id": "s", "objective": ""},
        {"goal_id": "g", "session_id": "s", "objective": "x", "status": "bad"},
        {
            "goal_id": "g",
            "session_id": "s",
            "objective": "x",
            "status": "active",
            "time_used_seconds": -1,
        },
    ],
)
def test_goal_record_rejects_invalid_persistence_payload(payload: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        GoalRecord.from_dict(payload)


def test_goal_record_tolerates_session_deleted_optional_timestamps() -> None:
    """Session merge deletes keys whose value is None; paused goals must still load."""
    paused = GoalRecord.from_dict(
        {
            "goal_id": "g1",
            "session_id": "s1",
            "objective": "write a report",
            "status": "paused",
            "time_used_seconds": 12,
            "created_at": _utc_iso(1000),
            "updated_at": _utc_iso(1012),
        }
    )
    assert paused.status is GoalStatus.PAUSED
    assert paused.active_started_at is None
    assert paused.time_used_seconds == 12

    active = GoalRecord.from_dict(
        {
            "goal_id": "g2",
            "session_id": "s1",
            "objective": "write a report",
            "status": "active",
            "time_used_seconds": 5,
            "created_at": _utc_iso(1000),
            "updated_at": _utc_iso(1005),
        }
    )
    assert active.status is GoalStatus.ACTIVE
    assert active.active_started_at == _utc_iso(1005)


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
