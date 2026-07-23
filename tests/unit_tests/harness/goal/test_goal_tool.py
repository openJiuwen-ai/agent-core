# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Unit tests for submit_goal_report tool and GoalReportSink."""
from __future__ import annotations

import pytest

from openjiuwen.harness.goal.schema import GoalAssessmentStatus
from openjiuwen.harness.tools.goal import GoalReportSink, SubmitGoalReportTool


class TestGoalReportSink:
    def test_begin_and_submit(self) -> None:
        sink = GoalReportSink()
        sink.begin_attempt("s1", "g1", 0, 1)

        assert sink.report is None
        assert sink.goal_id == "g1"

        from openjiuwen.harness.goal.schema import GoalAssessment
        report = GoalAssessment(
            status=GoalAssessmentStatus.CONTINUE,
            evidence="progress",
        )
        sink.submit(report)

        assert sink.report is not None
        assert sink.report.status == GoalAssessmentStatus.CONTINUE

    def test_consume(self) -> None:
        sink = GoalReportSink()
        sink.begin_attempt("s1", "g1", 0, 1)

        from openjiuwen.harness.goal.schema import GoalAssessment
        report = GoalAssessment(
            status=GoalAssessmentStatus.COMPLETE,
            evidence="done",
        )
        sink.submit(report)

        consumed = sink.consume()
        assert consumed is not None
        assert consumed.status == GoalAssessmentStatus.COMPLETE

        # Second consume returns None
        assert sink.consume() is None

    def test_begin_resets(self) -> None:
        sink = GoalReportSink()
        sink.begin_attempt("s1", "g1", 0, 1)

        from openjiuwen.harness.goal.schema import GoalAssessment
        sink.submit(GoalAssessment(
            status=GoalAssessmentStatus.CONTINUE,
            evidence="old",
        ))

        sink.begin_attempt("s1", "g1", 1, 2)
        assert sink.report is None
        assert sink.attempt_index == 2


@pytest.mark.asyncio
async def test_submit_goal_report_tool() -> None:
    sink = GoalReportSink()
    sink.begin_attempt("s1", "g1", 0, 1)

    tool = SubmitGoalReportTool(sink, language="cn")
    result = await tool.invoke({
        "status": "continue",
        "evidence": "made progress on endpoints",
        "remaining_work": "add auth",
        "next_instruction": "implement JWT auth",
    })

    assert result["result"] == "report_accepted"
    assert result["status"] == "continue"
    assert "final_response_instruction" not in result

    report = sink.consume()
    assert report is not None
    assert report.status == GoalAssessmentStatus.CONTINUE
    assert report.evidence == "made progress on endpoints"
    assert report.next_instruction == "implement JWT auth"


@pytest.mark.asyncio
async def test_submit_goal_report_invalid_status() -> None:
    sink = GoalReportSink()
    sink.begin_attempt("s1", "g1", 0, 1)

    tool = SubmitGoalReportTool(sink, language="cn")
    result = await tool.invoke({
        "status": "invalid_status",
        "evidence": "test",
    })

    assert result["status"] == "continue"
    report = sink.consume()
    assert report is not None
    assert report.status == GoalAssessmentStatus.CONTINUE
