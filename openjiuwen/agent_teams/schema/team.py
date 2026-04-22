# coding: utf-8
"""Team-level schemas."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from openjiuwen.agent_teams.messager.base import MessagerTransportConfig
from openjiuwen.agent_teams.schema.deep_agent_spec import TeamModelConfig
from openjiuwen.agent_teams.tools.database import DatabaseConfig


@dataclass(frozen=True, slots=True)
class MemberOpResult:
    """Outcome of a team-member mutation with the failure reason preserved.

    TeamBackend mutation methods (spawn_member, shutdown_member, …) return
    this so tool wrappers can surface the real cause back to the LLM rather
    than dropping it into the log sink. ``__bool__`` falls through to
    ``ok`` so legacy truthy call sites keep working.
    """

    ok: bool
    reason: str = ""

    def __bool__(self) -> bool:
        return self.ok

    @classmethod
    def success(cls) -> "MemberOpResult":
        return cls(ok=True)

    @classmethod
    def fail(cls, reason: str) -> "MemberOpResult":
        return cls(ok=False, reason=reason)


class TeamLifecycle(str, Enum):
    """Team lifecycle mode."""

    TEMPORARY = "temporary"
    PERSISTENT = "persistent"


class TeamRole(str, Enum):
    """Supported team roles.

    ``HUMAN_AGENT`` is a first-class member representing a human
    collaborator. It shares equal standing with leader and teammate in
    the model's mental model, but its runtime footprint differs:
    it owns no DeepAgent process, only the ``send_message`` tool,
    and stays in ``READY`` until the team is cleaned.
    """

    LEADER = "leader"
    TEAMMATE = "teammate"
    HUMAN_AGENT = "human_agent"


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
    language: Optional[str] = None
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
