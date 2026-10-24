# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Tests for VerificationMemory."""

import tempfile
from pathlib import Path

import pytest

from openjiuwen.agent_teams.verification.memory import VerificationMemory
from openjiuwen.agent_teams.verification.result import (
    DimensionScore,
    QualityDimension,
    VerificationResult,
    VerificationStatus,
)


class TestVerificationMemory:
    @pytest.fixture
    def temp_workspace(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            yield tmpdir

    def test_store_and_load(self, temp_workspace):
        memory = VerificationMemory(team_workspace_root=temp_workspace)

        result = VerificationResult(
            task_id="task-1",
            task_title="Test Task",
            assignee="teammate-a",
            status=VerificationStatus.PASS,
            overall_score=85,
            dimensions=[
                DimensionScore(
                    dimension=QualityDimension.CORRECTNESS,
                    score=90,
                    reasoning="Good",
                    findings=[],
                ),
            ],
            summary="Well done",
            verified_at="2026-07-11T12:00:00Z",
            reviewer_model="gpt-4",
        )

        assert memory.store(result) is True

        # Check file was created
        memory_path = Path(temp_workspace) / "TEAM_MEMORY.md"
        assert memory_path.exists()
        content = memory_path.read_text(encoding="utf-8")
        assert "Verification History" in content
        assert "task-1" in content
        assert "PASS" in content

    def test_store_without_workspace(self):
        memory = VerificationMemory(team_workspace_root=None)
        result = VerificationResult(
            task_id="task-1",
            task_title="T",
            assignee="a",
            status=VerificationStatus.PASS,
            overall_score=80,
        )
        assert memory.store(result) is False

    def test_get_quality_trends(self, temp_workspace):
        memory = VerificationMemory(team_workspace_root=temp_workspace)

        # Store multiple results
        for i in range(5):
            result = VerificationResult(
                task_id=f"task-{i}",
                task_title=f"Task {i}",
                assignee="teammate-a",
                status=VerificationStatus.PASS if i < 4 else VerificationStatus.FAIL,
                overall_score=80 if i < 4 else 30,
                dimensions=[
                    DimensionScore(
                        dimension=QualityDimension.CORRECTNESS,
                        score=85 if i < 4 else 20,
                        reasoning="",
                        findings=[],
                    ),
                ],
                verified_at="2026-07-11T12:00:00Z",
            )
            memory.store(result)

        trends = memory.get_quality_trends()
        assert trends["total"] == 5
        assert trends["pass_rate"] == 4 / 5
        assert trends["avg_score"] == (80 * 4 + 30) / 5

    def test_get_quality_trends_empty(self, temp_workspace):
        memory = VerificationMemory(team_workspace_root=temp_workspace)
        trends = memory.get_quality_trends()
        assert trends["pass_rate"] == 0.0
        assert trends["total"] == 0

    def test_multiple_stores_append(self, temp_workspace):
        memory = VerificationMemory(team_workspace_root=temp_workspace)

        for i in range(3):
            result = VerificationResult(
                task_id=f"task-{i}",
                task_title=f"Task {i}",
                assignee="teammate-a",
                status=VerificationStatus.PASS,
                overall_score=80,
                verified_at="2026-07-11T12:00:00Z",
            )
            memory.store(result)

        memory_path = Path(temp_workspace) / "TEAM_MEMORY.md"
        content = memory_path.read_text(encoding="utf-8")
        # Should have all 3 tasks
        assert content.count("task-") >= 3
