# coding: utf-8
"""Tests for GoalEvaluator strategies and hard limits."""
from __future__ import annotations

import json

from openjiuwen.harness.goal.evaluation import GoalEvaluator, _parse_assessment_json
from openjiuwen.harness.goal.schema import (
    GoalAssessment,
    GoalAssessmentStatus,
    GoalRecord,
    GoalStopConfig,
    GoalStopStrategy,
)


def _record(**kwargs: object) -> GoalRecord:
    record = GoalRecord.create(session_id="session-1", objective="Build a feature")
    for key, value in kwargs.items():
        setattr(record, key, value)
    return record


def _report(status: GoalAssessmentStatus) -> GoalAssessment:
    return GoalAssessment(status=status, evidence="agent evidence")


def test_parse_assessment_json_accepts_plain_and_fenced_json() -> None:
    plain = json.dumps({"status": "complete", "evidence": "tests passed"})
    fenced = "```json\n" + plain + "\n```"

    assert _parse_assessment_json(plain).status is GoalAssessmentStatus.COMPLETE
    assert _parse_assessment_json(fenced).status is GoalAssessmentStatus.COMPLETE
    assert _parse_assessment_json('{"status": "complete"}') is None
    assert _parse_assessment_json("not json") is None


def test_agent_report_strategy_uses_report_and_falls_back_when_absent() -> None:
    assessor = GoalEvaluator(GoalStopConfig(strategy=GoalStopStrategy.AGENT_REPORT))

    assert (
        assessor.assess(_record(), _report(GoalAssessmentStatus.COMPLETE)).status
        is GoalAssessmentStatus.COMPLETE
    )
    fallback = assessor.assess(_record(), None)
    assert fallback.status is GoalAssessmentStatus.CONTINUE
    assert "no_report" in fallback.evidence


def test_transcript_strategy_uses_verified_response() -> None:
    assessor = GoalEvaluator(GoalStopConfig(strategy=GoalStopStrategy.TRANSCRIPT))
    transcript = json.dumps({"status": "blocked", "evidence": "credential missing"})

    assert (
        assessor.assess(_record(), None, transcript_response=transcript).status
        is GoalAssessmentStatus.BLOCKED
    )
    assert assessor.assess(_record(), None).status is GoalAssessmentStatus.CONTINUE


def test_hybrid_requires_transcript_for_terminal_agent_report() -> None:
    assessor = GoalEvaluator()
    no_transcript = assessor.assess(_record(), _report(GoalAssessmentStatus.COMPLETE))
    transcript = json.dumps({"status": "continue", "evidence": "verification pending"})
    verified = assessor.assess(
        _record(),
        _report(GoalAssessmentStatus.COMPLETE),
        transcript_response=transcript,
    )

    assert no_transcript.status is GoalAssessmentStatus.CONTINUE
    assert "transcript_unavailable" in no_transcript.evidence
    assert verified.status is GoalAssessmentStatus.CONTINUE
    assert verified.evidence == "verification pending"


def test_hybrid_spot_check_can_override_continue_report() -> None:
    assessor = GoalEvaluator(GoalStopConfig(verification_interval=2))
    transcript = json.dumps({"status": "complete", "evidence": "verified"})

    result = assessor.assess(
        _record(attempt_count=2),
        _report(GoalAssessmentStatus.CONTINUE),
        transcript_response=transcript,
    )
    assert result.status is GoalAssessmentStatus.COMPLETE


def test_hard_limits_block_only_continue() -> None:
    maxed = _record(attempt_count=3, max_attempts=3)
    budgeted = _record(token_budget=10)
    budgeted.token_usage.total_tokens = 10
    assessor = GoalEvaluator()
    agent_report_assessor = GoalEvaluator(
        GoalStopConfig(strategy=GoalStopStrategy.AGENT_REPORT)
    )

    assert (
        assessor.assess(maxed, _report(GoalAssessmentStatus.CONTINUE)).status
        is GoalAssessmentStatus.BLOCKED
    )
    assert (
        assessor.assess(budgeted, _report(GoalAssessmentStatus.CONTINUE)).status
        is GoalAssessmentStatus.BLOCKED
    )
    assert (
        agent_report_assessor.assess(
            maxed, _report(GoalAssessmentStatus.COMPLETE)
        ).status
        is GoalAssessmentStatus.COMPLETE
    )
