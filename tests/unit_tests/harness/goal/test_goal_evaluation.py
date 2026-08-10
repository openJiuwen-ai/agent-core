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


def test_parse_assessment_json_handles_nested_code_blocks_in_evidence() -> None:
    """A fenced JSON whose evidence field embeds ```python blocks must still
    parse: the non-greedy fence regex truncates at the inner ```, so the
    outermost { ... } fallback must recover the full object."""
    raw = (
        '```json\n'
        '{\n'
        '  "status": "complete",\n'
        '  "evidence": "done\\n```python\\nfrom pptx import Presentation\\n'
        "prs = Presentation()\\nprs.save('x.pptx')\\n```\\nfile ok\",\n"
        '  "remaining_work": "",\n'
        '  "next_instruction": ""\n'
        '}\n```'
    )
    parsed = _parse_assessment_json(raw)
    assert parsed is not None
    assert parsed.status is GoalAssessmentStatus.COMPLETE
    assert "```python" in parsed.evidence
    assert parsed.evidence.endswith("file ok")


def test_parse_assessment_json_reads_blocking_same_as_previous() -> None:
    same = _parse_assessment_json(
        json.dumps(
            {
                "status": "blocked",
                "evidence": "no token",
                "blocking_same_as_previous": True,
            }
        )
    )
    assert same is not None
    assert same.status is GoalAssessmentStatus.BLOCKED
    assert same.blocking_same_as_previous is True

    different = _parse_assessment_json(
        json.dumps(
            {
                "status": "blocked",
                "evidence": "disk full",
                "blocking_same_as_previous": False,
            }
        )
    )
    assert different is not None
    assert different.blocking_same_as_previous is False

    # Missing / non-boolean signal stays None.
    missing = _parse_assessment_json('{"status": "blocked", "evidence": "x"}')
    assert missing is not None
    assert missing.blocking_same_as_previous is None


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
