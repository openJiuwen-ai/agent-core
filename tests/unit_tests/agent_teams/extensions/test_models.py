# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Extension event models: frozen shapes and documented invariants."""
from __future__ import annotations

import dataclasses

import pytest

from openjiuwen.agent_teams.extensions.models import TeamMonitorEventInput, TeamMonitorEventOutput
from openjiuwen.agent_teams.monitor.models import MonitorEvent, MonitorEventType


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
    with pytest.raises(dataclasses.FrozenInstanceError):
        monitor_input.team_name = "other"


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
