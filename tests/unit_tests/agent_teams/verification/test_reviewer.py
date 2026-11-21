# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Tests for VerificationReviewer."""

import json

import pytest

from openjiuwen.agent_teams.verification.result import (
    VerificationInput,
    VerificationStatus,
)
from openjiuwen.agent_teams.verification.reviewer import VerificationReviewer


class MockModelClient:
    """Mock model client for testing."""

    def __init__(self, response_data: dict | None = None):
        self.response_data = response_data or {
            "status": "pass",
            "overall_score": 78,
            "dimensions": [
                {"dimension": "correctness", "score": 80, "reasoning": "OK", "findings": []},
                {"dimension": "completeness", "score": 75, "reasoning": "OK", "findings": []},
            ],
            "summary": "Good output",
            "rework_instructions": "",
        }
        self.model_name = "mock-model"

    async def complete(self, **kwargs):
        return json.dumps(self.response_data, ensure_ascii=False)


class TestVerificationReviewer:
    @pytest.mark.asyncio
    async def test_review_with_mock_model(self):
        reviewer = VerificationReviewer(
            model_client=MockModelClient(),
            language="en",
        )

        result = await reviewer.review(
            VerificationInput(
                task_id="task-1",
                task_title="Implement feature X",
                task_content="Add feature X to the codebase",
                assignee="teammate-a",
                output="Here is the implementation...",
                team_context="Team is working on v2.0",
            )
        )

        assert result.task_id == "task-1"
        assert result.assignee == "teammate-a"
        assert result.status == VerificationStatus.PASS
        assert result.overall_score == 78
        assert len(result.dimensions) == 2
        assert result.reviewer_model == "mock-model"

    @pytest.mark.asyncio
    async def test_review_without_model_client(self):
        """Test that reviewer works in mock mode when no model client is provided."""
        reviewer = VerificationReviewer(model_client=None, language="en")

        result = await reviewer.review(
            VerificationInput(
                task_id="task-2",
                task_title="Simple task",
                task_content="Do something",
                assignee="teammate-b",
                output="Done",
            )
        )

        assert result.status == VerificationStatus.PASS
        assert result.overall_score == 75
        assert result.summary != ""

    @pytest.mark.asyncio
    async def test_review_status_normalization(self):
        """Test that status is normalized based on thresholds."""
        # Model returns PASS but score is below threshold
        mock_client = MockModelClient({
            "status": "pass",
            "overall_score": 50,
            "dimensions": [],
            "summary": "Mediocre",
            "rework_instructions": "Improve this",
        })

        reviewer = VerificationReviewer(
            model_client=mock_client,
            pass_threshold=70,
            rework_threshold=40,
        )

        result = await reviewer.review(
            VerificationInput(
                task_id="task-3",
                task_title="Task",
                task_content="Content",
                assignee="teammate-c",
                output="Output",
            )
        )

        # Score 50 < pass_threshold 70, so should be NEEDS_REWORK
        assert result.status == VerificationStatus.NEEDS_REWORK

    @pytest.mark.asyncio
    async def test_review_json_extraction_from_markdown(self):
        """Test parsing JSON wrapped in markdown code blocks."""
        class MarkdownMockClient:
            async def complete(self, **kwargs):
                return (
                    "```json\n"
                    + json.dumps({
                        "status": "fail",
                        "overall_score": 30,
                        "dimensions": [],
                        "summary": "Bad output",
                        "rework_instructions": "Start over",
                    }, ensure_ascii=False)
                    + "\n```"
                )

        reviewer = VerificationReviewer(
            model_client=MarkdownMockClient(),
            rework_threshold=40,
        )

        result = await reviewer.review(
            VerificationInput(
                task_id="task-4",
                task_title="Task",
                task_content="Content",
                assignee="teammate-d",
                output="Output",
            )
        )

        assert result.status == VerificationStatus.FAIL
        assert result.overall_score == 30

    @pytest.mark.asyncio
    async def test_review_graceful_failure(self):
        """Test graceful degradation when model call fails."""
        class FailingMockClient:
            async def complete(self, **kwargs):
                raise RuntimeError("Model unavailable")

        reviewer = VerificationReviewer(model_client=FailingMockClient())

        result = await reviewer.review(
            VerificationInput(
                task_id="task-5",
                task_title="Task",
                task_content="Content",
                assignee="teammate-e",
                output="Output",
            )
        )

        assert result.status == VerificationStatus.SKIPPED
        assert "error" in result.summary.lower()
