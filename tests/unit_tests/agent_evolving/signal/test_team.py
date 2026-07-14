# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Tests for team-domain signal helpers."""

from __future__ import annotations

from openjiuwen.agent_evolving.signal import (
    TeamSignalType,
    TrajectoryIssue,
    UserIntent,
    build_team_trajectory_summary,
    get_signal_source,
    get_team_signal_skill_content,
    get_team_trajectory_issues,
    make_team_trajectory_signal,
    make_team_user_intent_signal,
)
from openjiuwen.agent_evolving.trajectory.types import ToolCallDetail, TrajectoryStep, trajectory_from_steps


def test_team_signal_type_values_are_stable() -> None:
    assert TeamSignalType.USER_INTENT.value == "user_intent"
    assert TeamSignalType.USER_REQUEST.value == "user_request"
    assert TeamSignalType.TRAJECTORY_ISSUE.value == "trajectory_issue"


def test_user_intent_dataclass_fields() -> None:
    intent = UserIntent(is_improvement=True, intent="add reviewer role")

    assert intent.is_improvement is True
    assert intent.intent == "add reviewer role"


def test_trajectory_issue_defaults() -> None:
    issue = TrajectoryIssue(issue_type="handoff", description="missing summary")

    assert issue.affected_role == ""
    assert issue.severity == "medium"


def test_team_trajectory_signal_helpers_roundtrip_context() -> None:
    signal = make_team_trajectory_signal(
        skill_name="team-a",
        skill_content="# current skill",
        trajectory_issues=[
            {
                "issue_type": "handoff",
                "description": "missing summary",
                "affected_role": "researcher",
                "severity": "medium",
            }
        ],
    )

    assert signal.signal_type == "trajectory_issue"
    assert signal.skill_name == "team-a"
    assert get_team_signal_skill_content(signal) == "# current skill"
    assert get_team_trajectory_issues(signal) == [
        {
            "issue_type": "handoff",
            "description": "missing summary",
            "affected_role": "researcher",
            "severity": "medium",
        }
    ]


def test_team_user_intent_signal_helper() -> None:
    signal = make_team_user_intent_signal(skill_name="team-a", user_intent="add reviewer role")

    assert signal.signal_type == "user_intent"
    assert signal.section == "Instructions"
    assert signal.excerpt == "add reviewer role"
    assert signal.skill_name == "team-a"
    assert get_signal_source(signal) == "explicit_request"


def test_build_team_trajectory_summary_includes_tool_calls() -> None:
    trajectory = trajectory_from_steps(
        execution_id="exec-1",
        steps=[
            TrajectoryStep(
                kind="tool",
                detail=ToolCallDetail(
                    tool_name="send_message",
                    call_args={"to": "reviewer"},
                    call_result="sent",
                ),
            )
        ],
    )

    summary = build_team_trajectory_summary(trajectory)

    assert "Tool Calls (1)" in summary
    assert "[Tool:send_message]" in summary
    assert "reviewer" in summary
