# coding: utf-8
"""Schemas for agent teams."""

from openjiuwen.agent_teams.schema.blueprint import (
    DeepAgentSpec,
    LeaderSpec,
    StorageSpec,
    TeamAgentSpec,
    TransportSpec,
    register_storage,
    register_transport,
)
from openjiuwen.agent_teams.schema.deep_agent_spec import (
    AudioModelSpec,
    ProgressiveToolSpec,
    RailSpec,
    StopConditionSpec,
    SubAgentSpec,
    SysOperationSpec,
    VisionModelSpec,
    WorkspaceSpec,
    register_rail_type,
)
from openjiuwen.agent_teams.schema.team import (
    TeamLifecycle,
    TeamMemberSpec,
    TeamRole,
    TeamRuntimeContext,
    TeamSpec,
)

__all__ = [
    "AudioModelSpec",
    "DeepAgentSpec",
    "LeaderSpec",
    "ProgressiveToolSpec",
    "RailSpec",
    "StopConditionSpec",
    "StorageSpec",
    "SubAgentSpec",
    "SysOperationSpec",
    "TeamAgentSpec",
    "TransportSpec",
    "VisionModelSpec",
    "WorkspaceSpec",
    "register_rail_type",
    "register_storage",
    "register_transport",
    "TeamLifecycle",
    "TeamMemberSpec",
    "TeamRole",
    "TeamRuntimeContext",
    "TeamSpec",
]
