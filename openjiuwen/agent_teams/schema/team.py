# coding: utf-8
"""Team-level schemas."""
from __future__ import annotations

import uuid
from enum import Enum
from typing import Any, List, Optional

from pydantic import BaseModel, ConfigDict, Field

from openjiuwen.agent_teams.messager.base import MessagerTransportConfig
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
    """Definition for one team member."""

    member_id: str = Field(default_factory=lambda: str(uuid.uuid4())[:8])
    name: str
    role_type: TeamRole = TeamRole.TEAMMATE
    persona: str
    domain: str
    prompt_hint: Optional[str] = None
    spawn_config: dict = Field(default_factory=dict)
    metadata: dict = Field(default_factory=dict)


class TeamSpec(BaseModel):
    """Definition of a team and its goal."""

    team_id: str
    name: str
    objective: str
    context: str = ""
    constraints: List[str] = Field(default_factory=list)
    success_criteria: List[str] = Field(default_factory=list)
    leader_member_id: Optional[str] = None
    members: List[TeamMemberSpec] = Field(default_factory=list)
    metadata: dict = Field(default_factory=dict)

    def add_member(self, member: TeamMemberSpec) -> None:
        """Attach a member to the team."""
        self.members.append(member)
        if member.role_type == TeamRole.LEADER:
            self.leader_member_id = member.member_id

    def get_member(self, member_id: str) -> Optional[TeamMemberSpec]:
        """Return a member by id."""
        for member in self.members:
            if member.member_id == member_id:
                return member
        return None

    def teammates_by_domain(self, domain: str) -> List[TeamMemberSpec]:
        """Return teammates whose specialty matches the given domain."""
        result: list[TeamMemberSpec] = []
        for member in self.members:
            if member.role_type == TeamRole.TEAMMATE and member.domain == domain:
                result.append(member)
        return result


class TeamRuntimeContext(BaseModel):
    """Lightweight runtime context for a single team member.

    Carries only the data NOT already present in TeamAgentSpec:
    role identity, runtime team info from DB, and resolved infra configs.
    Persona and domain are read from member_spec.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    role: TeamRole = TeamRole.LEADER
    member_spec: Optional[TeamMemberSpec] = None
    team_spec: Optional[TeamSpec] = None
    team_info: dict[str, Any] = Field(default_factory=dict)
    team_members: list[dict[str, str]] = Field(default_factory=list)
    messager_config: Optional[MessagerTransportConfig] = None
    db_config: DatabaseConfig = Field(default_factory=DatabaseConfig)

    @property
    def persona(self) -> str:
        """Return persona from member_spec."""
        return self.member_spec.persona if self.member_spec else ""

    @property
    def domain(self) -> str:
        """Return domain from member_spec."""
        return self.member_spec.domain if self.member_spec else ""

    @property
    def member_id(self) -> Optional[str]:
        """Return member_id from member_spec."""
        return self.member_spec.member_id if self.member_spec else None


__all__ = [
    "TeamLifecycle",
    "TeamMemberSpec",
    "TeamRole",
    "TeamRuntimeContext",
    "TeamSpec",
]
