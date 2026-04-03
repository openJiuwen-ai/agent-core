# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Multi-Agent Teams Package"""
from openjiuwen.core.multi_agent.teams.utils import (
    make_team_session,
    standalone_invoke_context,
    standalone_stream_context,
)
from openjiuwen.core.multi_agent.teams.handoff import (
    HandoffTeam,
    HandoffTeamConfig,
    HandoffConfig,
    HandoffRoute,
    HandoffSignal,
    HandoffOrchestrator,
    TeamInterruptSignal,
)
from openjiuwen.core.multi_agent.teams.hierarchical_tools import (
    HierarchicalTeam as HierarchicalToolsTeam,
)
from openjiuwen.core.multi_agent.teams.hierarchical_msgbus import (
    HierarchicalTeam as HierarchicalMsgbusTeam,
    HierarchicalTeamConfig,
    SupervisorAgent,
)

__all__ = [
    # Standalone session lifecycle utils
    "make_team_session",
    "standalone_invoke_context",
    "standalone_stream_context",
    # Handoff team
    "HandoffTeam",
    "HandoffTeamConfig",
    "HandoffConfig",
    "HandoffRoute",
    "HandoffSignal",
    "HandoffOrchestrator",
    "TeamInterruptSignal",
    # Hierarchical team - Agents-as-Tools
    "HierarchicalToolsTeam",
    # Hierarchical team - P2P MessageBus
    "HierarchicalMsgbusTeam",
    "HierarchicalTeamConfig",
    "SupervisorAgent",
]
