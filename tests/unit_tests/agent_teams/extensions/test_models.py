# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Extension event models: frozen shapes and documented invariants."""
from __future__ import annotations

import dataclasses

import pytest

from openjiuwen.agent_teams.extensions.models import (
    TeamMonitorEventInput,
    TeamMonitorEventOutput,
    TeamPolicyBuildInput,
    TeamPolicyBuildOutput,
    TeamPolicyContext,
)
from openjiuwen.agent_teams.monitor.models import MonitorEvent, MonitorEventType
from openjiuwen.agent_teams.schema.team import TeamRole
from openjiuwen.core.single_agent.prompts.builder import PromptSection


def _monitor_event() -> MonitorEvent:
    return MonitorEvent(
        event_type=MonitorEventType.TASK_CREATED,
        team_name="research-team",
        task_id="t1",
        timestamp=1,
    )


@pytest.mark.level0
def test_models_are_frozen() -> None:
    """Every extension model rejects attribute mutation."""
    monitor_input = TeamMonitorEventInput(
        team_name="research-team",
        member_name="researcher",
        monitor_event=_monitor_event(),
    )
    policy_input = TeamPolicyBuildInput(team_name="research-team", role=TeamRole.TEAMMATE)
    policy_output = TeamPolicyBuildOutput(sections=())
    context = TeamPolicyContext(language="cn", hitt_enabled=True)
    for model, attr, value in (
        (monitor_input, "team_name", "other"),
        (policy_input, "role", TeamRole.LEADER),
        (policy_output, "sections", ()),
        (context, "hitt_enabled", False),
    ):
        with pytest.raises(dataclasses.FrozenInstanceError):
            setattr(model, attr, value)


@pytest.mark.level0
def test_monitor_input_member_name_mirrors_event() -> None:
    """The input carries the notification's own member identity."""
    event = _monitor_event()
    monitor_input = TeamMonitorEventInput(
        team_name="research-team",
        member_name=event.member_name,
        monitor_event=event,
    )
    assert monitor_input.member_name is None
    assert monitor_input.monitor_event.event_type == MonitorEventType.TASK_CREATED


@pytest.mark.level0
def test_policy_output_holds_named_sections() -> None:
    """The build output exposes its sections as a stable tuple."""
    sections = (
        PromptSection(name="team_role", content={"cn": "policy"}, priority=1),
    )
    output = TeamPolicyBuildOutput(sections=sections)
    assert output.sections is sections
    assert TeamMonitorEventOutput() == TeamMonitorEventOutput()
