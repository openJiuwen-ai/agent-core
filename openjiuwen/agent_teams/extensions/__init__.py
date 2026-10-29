# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Team-side contracts of the team extension mechanism.

Holds the team monitor event's typed input/output and matcher, the event
registration entry, the monitor-event bridge, and the Ops-side resource
resolvers and call-scope bindings consumed at the team's extension
boundaries. The ``team.build_policy`` contracts arrive in a follow-up
change.
"""

from openjiuwen.agent_teams.extensions.events import (
    TEAM_MONITOR_EVENT,
    build_monitor_event_dispatcher,
    register_team_event_types,
    team_monitor_event_core,
)
from openjiuwen.agent_teams.extensions.matching import match_team_monitor_event
from openjiuwen.agent_teams.extensions.models import (
    TeamMonitorEventInput,
    TeamMonitorEventOutput,
)
from openjiuwen.agent_teams.extensions.monitor_event_bridge import (
    DEFAULT_MONITOR_EVENT_QUEUE_CAPACITY,
    MonitorEventDispatcher,
    TeamMonitorEventBridge,
)
from openjiuwen.agent_teams.extensions.ops import (
    MessagePage,
    TaskDependencyInfo,
    TeamReadView,
    TeamResourceResolver,
    bind_team_scope,
    current_team_name,
)
from openjiuwen.agent_teams.extensions.resolver import (
    BackendTeamReadView,
    GlobalRunnerPoolResolver,
    RuntimePoolResolver,
)

__all__ = [
    "DEFAULT_MONITOR_EVENT_QUEUE_CAPACITY",
    "GlobalRunnerPoolResolver",
    "MessagePage",
    "MonitorEventDispatcher",
    "RuntimePoolResolver",
    "BackendTeamReadView",
    "TaskDependencyInfo",
    "TEAM_MONITOR_EVENT",
    "TeamMonitorEventBridge",
    "TeamMonitorEventInput",
    "TeamMonitorEventOutput",
    "TeamReadView",
    "TeamResourceResolver",
    "bind_team_scope",
    "build_monitor_event_dispatcher",
    "current_team_name",
    "match_team_monitor_event",
    "register_team_event_types",
    "team_monitor_event_core",
]
