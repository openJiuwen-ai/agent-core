# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Tests for verification result models."""

from openjiuwen.agent_teams.verification.result import (
    DimensionScore,
    QualityDimension,
    VerificationResult,
    VerificationStatus,
)


class TestVerificationResult:
    def test_to_dict_roundtrip(self):
        result = VerificationResult(
            task_id="task-123",
            task_title="Test Task",
            assignee="teammate-1",
            status=VerificationStatus.PASS,
            overall_score=85,
            dimensions=[
                DimensionScore(
                    dimension=QualityDimension.CORRECTNESS,
                    score=90,
                    reasoning="Correct",
                    findings=[],
                ),
                DimensionScore(
                    dimension=QualityDimension.COMPLETENESS,
                    score=80,
                    reasoning="Mostly complete",
                    findings=["Missing edge case docs"],
                ),
            ],
            summary="Good work overall",
            verified_at="2026-07-11T12:00:00Z",
            reviewer_model="gpt-4",
        )

        d = result.to_dict()
        restored = VerificationResult.from_dict(d)

        assert restored.task_id == "task-123"
        assert restored.status == VerificationStatus.PASS
        assert restored.overall_score == 85
        assert len(restored.dimensions) == 2
        assert restored.dimensions[0].dimension == QualityDimension.CORRECTNESS
        assert restored.dimensions[1].findings == ["Missing edge case docs"]

    def test_is_passing(self):
        passing = VerificationResult(
            task_id="t1",
            task_title="T1",
            assignee="a1",
            status=VerificationStatus.PASS,
            overall_score=75,
        )
        assert passing.is_passing(threshold=70) is True
        assert passing.is_passing(threshold=80) is False

        failing = VerificationResult(
            task_id="t2",
            task_title="T2",
            assignee="a2",
            status=VerificationStatus.FAIL,
            overall_score=85,
        )
        assert failing.is_passing(threshold=70) is False

    def test_from_dict_defaults(self):
        result = VerificationResult.from_dict({})
        assert result.status == VerificationStatus.SKIPPED
        assert result.overall_score == 0
        assert result.dimensions == []


class TestVerificationStatus:
    def test_enum_values(self):
        assert VerificationStatus.PASS.value == "pass"
        assert VerificationStatus.FAIL.value == "fail"
        assert VerificationStatus.NEEDS_REWORK.value == "needs_rework"
        assert VerificationStatus.SKIPPED.value == "skipped"


class TestQualityDimension:
    def test_enum_values(self):
        assert QualityDimension.CORRECTNESS.value == "correctness"
        assert QualityDimension.SECURITY.value == "security"
