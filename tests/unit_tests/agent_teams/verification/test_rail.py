# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Tests for TeamVerificationRail."""

from unittest.mock import MagicMock

import pytest

from openjiuwen.agent_teams.verification.rail import TeamVerificationRail
from openjiuwen.agent_teams.verification.result import (
    VerificationInput,
    VerificationStatus,
)


class TestTeamVerificationRail:
    @pytest.fixture
    def rail(self, tmp_path):
        return TeamVerificationRail(
            team_workspace_root=str(tmp_path),
            language="en",
            enabled=True,
            block_on_fail=False,
            auto_rework=False,
            pass_threshold=70,
            rework_threshold=40,
            skip_verification_for={"heartbeat", "ping"},
        )

    @pytest.mark.asyncio
    async def test_on_task_completed_disabled(self):
        rail = TeamVerificationRail(enabled=False)
        result = await rail.on_task_completed(
            VerificationInput(
                task_id="task-1",
                task_title="Test",
                task_content="Content",
                assignee="teammate-a",
                output="Output",
            )
        )
        assert result["status"] == "skipped"
        assert result["reason"] == "verification_disabled"

    @pytest.mark.asyncio
    async def test_on_task_completed_skipped_pattern(self, rail):
        result = await rail.on_task_completed(
            VerificationInput(
                task_id="task-1",
                task_title="Daily heartbeat check",
                task_content="Content",
                assignee="teammate-a",
                output="Output",
            )
        )
        assert result["status"] == "skipped"
        assert result["reason"] == "pattern_excluded"

    @pytest.mark.asyncio
    async def test_on_task_completed_mock_mode(self, rail):
        """Test verification in mock mode (no model client)."""
        result = await rail.on_task_completed(
            VerificationInput(
                task_id="task-2",
                task_title="Implement feature",
                task_content="Add new feature",
                assignee="teammate-b",
                output="Here is my implementation...",
                team_context="Working on v2.0",
            )
        )

        assert result["event_type"] == "team.verification.completed"
        assert result["task_id"] == "task-2"
        assert result["assignee"] == "teammate-b"
        assert "status" in result
        assert "overall_score" in result
        assert "dimensions" in result

    def test_should_skip(self, rail):
        assert rail._should_skip("heartbeat monitor") is True
        assert rail._should_skip("ping service") is True
        assert rail._should_skip("Implement feature X") is False
        assert rail._should_skip("HEARTBEAT check") is True  # case insensitive

    def test_get_quality_summary_empty(self, rail):
        summary = rail.get_quality_summary()
        assert summary["total"] == 0
        assert summary["pass_rate"] == 0.0

    def test_cleanup(self, rail):
        # Add a fake pending task
        fake_task = MagicMock()
        fake_task.done.return_value = False
        rail._pending_verifications["task-x"] = fake_task

        rail.cleanup()

        fake_task.cancel.assert_called_once()
        assert rail._pending_verifications == {}
