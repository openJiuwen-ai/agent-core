# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Team-side contracts of the team extension mechanism.

Holds the typed event inputs and outputs, the read-only policy context,
the default policy section builder, the pattern matching functions, the
event registration entry, the monitor-event bridge, and the Ops-side
resource resolvers and call-scope bindings consumed at the team's
extension boundaries.
"""

from openjiuwen.agent_teams.extensions.events import (
    TEAM_BUILD_POLICY,
    TEAM_MONITOR_EVENT,
    build_monitor_event_dispatcher,
    build_team_policy_sections,
    register_team_event_types,
    team_monitor_event_core,
)
from openjiuwen.agent_teams.extensions.matching import (
    match_team_build_policy,
    match_team_monitor_event,
)
from openjiuwen.agent_teams.extensions.models import (
    TeamMonitorEventInput,
    TeamMonitorEventOutput,
    TeamPolicyBuildInput,
    TeamPolicyBuildOutput,
    TeamPolicyContext,
)
from openjiuwen.agent_teams.extensions.monitor_event_bridge import (
    DEFAULT_MONITOR_EVENT_QUEUE_CAPACITY,
    MonitorEventDispatcher,
    TeamMonitorEventBridge,
)
from openjiuwen.agent_teams.extensions.ops import (
    TaskDependencyInfo,
    TeamReadView,
    TeamResourceResolver,
    bind_policy_context_scope,
    bind_team_scope,
    current_policy_context,
    current_team_name,
)
from openjiuwen.agent_teams.extensions.policy import build_default_policy_sections
from openjiuwen.agent_teams.extensions.resolver import (
    BackendTeamReadView,
    RuntimePoolResolver,
)

__all__ = [
    "DEFAULT_MONITOR_EVENT_QUEUE_CAPACITY",
    "MonitorEventDispatcher",
    "RuntimePoolResolver",
    "BackendTeamReadView",
    "TaskDependencyInfo",
    "TEAM_BUILD_POLICY",
    "TEAM_MONITOR_EVENT",
    "TeamMonitorEventBridge",
    "TeamMonitorEventInput",
    "TeamMonitorEventOutput",
    "TeamPolicyBuildInput",
    "TeamPolicyBuildOutput",
    "TeamPolicyContext",
    "TeamReadView",
    "TeamResourceResolver",
    "bind_policy_context_scope",
    "bind_team_scope",
    "build_default_policy_sections",
    "build_monitor_event_dispatcher",
    "build_team_policy_sections",
    "current_policy_context",
    "current_team_name",
    "match_team_build_policy",
    "match_team_monitor_event",
    "register_team_event_types",
    "team_monitor_event_core",
]
