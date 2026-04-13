# coding: utf-8
"""Team-level schemas."""
from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from openjiuwen.agent_teams.messager.base import MessagerTransportConfig
from openjiuwen.agent_teams.schema.deep_agent_spec import TeamModelConfig
from openjiuwen.agent_teams.tools.database import DatabaseConfig


class TeamLifecycle(str, Enum):
    """Team lifecycle mode."""

    TEMPORARY = "temporary"
    PERSISTENT = "persistent"


class TeamRole(str, Enum):
    """Supported team roles."""

    LEADER = "leader"
    TEAMMATE = "teammate"


class TeamMemberSpec(BaseModel):
    """Declarative input for pre-defining a team member.

    Used only for ``predefined_members`` at team creation time.
    Not a runtime data carrier — spawn/restart paths read from DB directly.
    """

    member_name: str
    display_name: str
    role_type: TeamRole = TeamRole.TEAMMATE
    persona: str
    prompt_hint: Optional[str] = None


class TeamSpec(BaseModel):
    """Definition of a team and its goal."""

    team_name: str
    display_name: str
    leader_member_name: Optional[str] = None
    metadata: dict = Field(default_factory=dict)


class TeamRuntimeContext(BaseModel):
    """Lightweight runtime context for a single team member.

    Carries role identity, runtime team info, and resolved infra configs.
    All identity fields are stored directly — no nested spec object.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    role: TeamRole = TeamRole.LEADER
    member_name: Optional[str] = None
    persona: str = ""
    team_spec: Optional[TeamSpec] = None
    messager_config: Optional[MessagerTransportConfig] = None
    db_config: DatabaseConfig = Field(default_factory=DatabaseConfig)
    member_model: Optional[TeamModelConfig] = None
    """TeamModelConfig assigned to this member by the allocator."""


__all__ = [
    "TeamLifecycle",
    "TeamMemberSpec",
    "TeamRole",
    "TeamRuntimeContext",
    "TeamSpec",
]
