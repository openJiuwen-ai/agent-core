# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Pattern matching rules of the team monitor event."""
from __future__ import annotations

import pytest

from openjiuwen.agent_teams.extensions.matching import (
    match_team_monitor_event,
)
from openjiuwen.agent_teams.extensions.models import TeamMonitorEventInput
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
