# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Pattern matching rules of the two team events."""
from __future__ import annotations

import pytest

from openjiuwen.agent_teams.extensions.matching import (
    match_team_build_policy,
    match_team_monitor_event,
)
from openjiuwen.agent_teams.extensions.models import TeamMonitorEventInput, TeamPolicyBuildInput
from openjiuwen.agent_teams.monitor.models import MonitorEvent, MonitorEventType
from openjiuwen.agent_teams.schema.team import TeamRole


def _monitor_input(member_name: str | None = None) -> TeamMonitorEventInput:
    return TeamMonitorEventInput(
        team_name="research-team",
        member_name=member_name,
        monitor_event=MonitorEvent(
            event_type=MonitorEventType.TASK_CREATED,
            team_name="research-team",
            member_name=member_name,
            task_id="t1",
            timestamp=1,
        ),
    )


# ---------------------------------------------------------------------------
# team.build_policy
# ---------------------------------------------------------------------------


@pytest.mark.level0
def test_policy_example_table() -> None:
    """The documented example: role-scoped build with no target member."""
    build_input = TeamPolicyBuildInput(team_name="research-team", role=TeamRole.TEAMMATE)
    assert match_team_build_policy(build_input, {}) is True
    assert (
        match_team_build_policy(
            TeamPolicyBuildInput(team_name="research-team", role=TeamRole.TEAMMATE),
            {"role": "teammate"},
        )
        is True
    )
    assert (
        match_team_build_policy(
            TeamPolicyBuildInput(team_name="research-team", role=TeamRole.TEAMMATE),
            {"member_name": "researcher"},
        )
        is False
    )
    assert (
        match_team_build_policy(
            TeamPolicyBuildInput(team_name="research-team", role=TeamRole.TEAMMATE),
            {"team_name": "other-team"},
        )
        is False
    )


@pytest.mark.level0
def test_policy_all_conditions_must_hold() -> None:
    """AND semantics: every specified field must match exactly."""
    build_input = TeamPolicyBuildInput(
        team_name="research-team",
        role=TeamRole.TEAMMATE,
        member_name="researcher",
    )
    assert match_team_build_policy(
        build_input,
        {"team_name": "research-team", "role": "teammate", "member_name": "researcher"},
    )
    assert not match_team_build_policy(
        build_input,
        {"team_name": "research-team", "role": "teammate", "member_name": "writer"},
    )


@pytest.mark.level1
def test_policy_illegal_conditions_match_nothing() -> None:
    """Unknown keys, bad values and wrong input types all fail closed."""
    build_input = TeamPolicyBuildInput(team_name="research-team", role=TeamRole.TEAMMATE)
    assert not match_team_build_policy(build_input, {"member_name": None})
    assert not match_team_build_policy(build_input, {"unknown_field": "x"})
    assert not match_team_build_policy(build_input, {"role": ""})
    assert not match_team_build_policy(build_input, {"role": 123})
    assert not match_team_build_policy(build_input, {"role": "not_a_role"})
    assert not match_team_build_policy("not-an-input", {})


# ---------------------------------------------------------------------------
# team.monitor_event
# ---------------------------------------------------------------------------


@pytest.mark.level0
def test_monitor_matches_type_team_and_member() -> None:
    """All three dimensions combine with AND semantics."""
    actual = _monitor_input(member_name="researcher")
    assert match_team_monitor_event(actual, {})
    assert match_team_monitor_event(actual, {"monitor_event_type": "task_created"})
    assert match_team_monitor_event(actual, {"member_name": "researcher"})
    assert not match_team_monitor_event(actual, {"monitor_event_type": "task_completed"})
    assert not match_team_monitor_event(actual, {"member_name": "writer"})
    assert not match_team_monitor_event(actual, {"monitor_event_type": "bogus_type"})
    assert not match_team_monitor_event(actual, {"notification_type": "task_created"})


@pytest.mark.level0
def test_monitor_without_member_never_matches_member_condition() -> None:
    """A member-less notification cannot match a member-scoped pattern."""
    actual = _monitor_input()
    assert not match_team_monitor_event(actual, {"member_name": "researcher"})
    assert match_team_monitor_event(actual, {"monitor_event_type": "task_created"})


@pytest.mark.level1
def test_monitor_wrong_input_type_fails_closed() -> None:
    """A non-team input never matches, without raising."""
    assert not match_team_monitor_event("not-an-input", {})
    assert not match_team_monitor_event(None, {})
