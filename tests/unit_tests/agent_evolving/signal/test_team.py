# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Tests for team-signal domain helpers and detectors."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from openjiuwen.agent_evolving.optimizer.llm_resilience import LLMInvokePolicy
from openjiuwen.agent_evolving.signal import (
    TeamSignalDetector,
    get_team_signal_skill_content,
    get_team_trajectory_issues,
    parse_team_model_json,
)
from openjiuwen.agent_evolving.trajectory.types import Trajectory
from openjiuwen.core.common.exception.errors import BaseError


def _build_detector(llm: object) -> TeamSignalDetector:
    return TeamSignalDetector(
        llm=llm,
        model="test-model",
        language="cn",
        llm_policy=LLMInvokePolicy(
            attempt_timeout_secs=5,
            total_budget_secs=15,
            max_attempts=2,
        ),
    )


def _build_trajectory() -> Trajectory:
    return Trajectory(
        execution_id="team-exec",
        session_id="team-session",
        source="online",
        steps=[],
    )

def test_parse_team_model_json_prefers_full_array_from_fenced_json():
    raw = """```json
[
  {"issue_type": "coordination", "severity": "high"},
  {"issue_type": "workflow", "severity": "medium"}
]
```"""

    parsed = parse_team_model_json(raw)

    assert isinstance(parsed, list)
    assert len(parsed) == 2
    assert parsed[0]["issue_type"] == "coordination"


class TestTeamSignalDetector:
    @pytest.mark.asyncio
    async def test_raises_on_llm_failure(self):
        detector = _build_detector(MagicMock(invoke=AsyncMock(side_effect=RuntimeError("connection lost"))))

        with pytest.raises(BaseError):
            await detector.detect_trajectory_issues(
                trajectory=_build_trajectory(),
                skill_content="skill content",
            )

    @pytest.mark.asyncio
    async def test_raises_on_non_list_json(self):
        detector = _build_detector(
            MagicMock(invoke=AsyncMock(return_value=MagicMock(content='{"not_a_list": true}')))
        )

        with pytest.raises(BaseError):
            await detector.detect_trajectory_issues(
                trajectory=_build_trajectory(),
                skill_content="skill content",
            )

    @pytest.mark.asyncio
    async def test_retries_when_first_response_is_invalid_json(self):
        import json

        llm = MagicMock(
            invoke=AsyncMock(
                side_effect=[
                    MagicMock(content="not json"),
                    MagicMock(content=json.dumps([
                        {
                            "issue_type": "coordination",
                            "description": "data not passed",
                            "affected_role": "reviewer",
                            "severity": "high",
                        }
                    ])),
                ]
            )
        )
        detector = _build_detector(llm)

        issues = await detector.detect_trajectory_issues(
            trajectory=_build_trajectory(),
            skill_content="skill content",
        )

        assert len(issues) == 1
        assert issues[0]["issue_type"] == "coordination"
        assert llm.invoke.await_count == 2

    @pytest.mark.asyncio
    async def test_filters_out_low_severity(self):
        import json

        detector = _build_detector(
            MagicMock(
                invoke=AsyncMock(
                    return_value=MagicMock(content=json.dumps([
                        {"issue_type": "minor", "description": "cosmetic issue", "affected_role": "a", "severity": "low"},
                        {"issue_type": "coordination", "description": "data not passed", "affected_role": "b", "severity": "high"},
                    ]))
                )
            )
        )

        issues = await detector.detect_trajectory_issues(
            trajectory=_build_trajectory(),
            skill_content="skill content",
        )

        assert len(issues) == 1
        assert issues[0]["issue_type"] == "coordination"

    @pytest.mark.asyncio
    async def test_defaults_invalid_severity_to_medium(self):
        import json

        detector = _build_detector(
            MagicMock(
                invoke=AsyncMock(
                    return_value=MagicMock(content=json.dumps([
                        {"issue_type": "test", "description": "bad severity value", "severity": "invalid"},
                    ]))
                )
            )
        )

        issues = await detector.detect_trajectory_issues(
            trajectory=_build_trajectory(),
            skill_content="skill content",
        )

        assert len(issues) == 1
        assert issues[0]["severity"] == "medium"

    @pytest.mark.asyncio
    async def test_detect_trajectory_signals_wraps_issues_as_standard_signal(self):
        import json

        detector = _build_detector(
            MagicMock(
                invoke=AsyncMock(
                    return_value=MagicMock(content=json.dumps([
                        {
                            "issue_type": "workflow",
                            "description": "handoff gap",
                            "affected_role": "leader",
                            "severity": "high",
                        }
                    ]))
                )
            )
        )

        signals = await detector.detect_trajectory_signals(
            trajectory=_build_trajectory(),
            skill_name="research-team",
            skill_content="# current skill",
        )

        assert len(signals) == 1
        assert signals[0].signal_type == "trajectory_issue"
        assert signals[0].skill_name == "research-team"
        assert get_team_trajectory_issues(signals[0]) == [
            {
                "issue_type": "workflow",
                "description": "handoff gap",
                "affected_role": "leader",
                "severity": "high",
            }
        ]
        assert get_team_signal_skill_content(signals[0]) == "# current skill"
        assert signals[0].context == {
            "source": "passive_trajectory",
            "trajectory_issues": [
                {
                    "issue_type": "workflow",
                    "description": "handoff gap",
                    "affected_role": "leader",
                    "severity": "high",
                }
            ],
            "skill_content": "# current skill",
        }
