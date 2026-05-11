# coding: utf-8
"""Team-level schemas."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from openjiuwen.agent_teams.messager.base import MessagerTransportConfig
from openjiuwen.agent_teams.models.pool import ModelPoolEntry
from openjiuwen.agent_teams.schema.deep_agent_spec import TeamModelConfig
from openjiuwen.agent_teams.tools.database import DatabaseConfig
from openjiuwen.agent_teams.tools.memory_database import MemoryDatabaseConfig


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

    model_config = ConfigDict(protected_namespaces=())

    member_name: str
    display_name: str
    role_type: TeamRole = TeamRole.TEAMMATE
    persona: str
    prompt_hint: Optional[str] = None
    model_name: Optional[str] = None
    """Optional pool model_name to allocate from when ``TeamSpec.model_pool``
    is configured with ``by_model_name`` or ``router`` strategy.

    Forwarded to ``ModelAllocator.allocate`` at ``build_team`` time so
    this member draws an endpoint from the named group (``by_model_name``)
    or the named router entry (``router``). Ignored by the ``round_robin``
    strategy. ``None`` (default) means the member uses its per-agent model
    (or, under ``router``, the router's first declared model_name).
    """


class TeamSpec(BaseModel):
    """Definition of a team and its goal."""

    model_config = ConfigDict(protected_namespaces=())

    team_name: str
    display_name: str
    leader_member_name: Optional[str] = None
    language: Optional[str] = None
    metadata: dict = Field(default_factory=dict)
    model_pool: list[ModelPoolEntry] = Field(default_factory=list)
    """Optional pool of LLM endpoints shared by every team member.

    When non-empty, ``ModelAllocator`` distributes pool entries across
    leader and teammates (round-robin by default) so concurrent calls
    spread across endpoints instead of saturating a single one. When
    empty (default), members fall back to their per-agent model config
    declared in ``TeamAgentSpec.agents`` and behavior is unchanged.
    """
    model_pool_strategy: Literal["round_robin", "by_model_name", "router"] = "round_robin"
    """Allocation strategy applied to ``model_pool`` entries.

    * ``round_robin`` (default): linear rotation across every entry in
      pool order, ignoring ``model_name``.
    * ``by_model_name``: rotation that first picks the next distinct
      ``model_name`` group and then advances the within-group rotation,
      so each declared model name receives an equal share of allocations
      regardless of how many endpoints back it. Use when the pool mixes
      models with different cost / capability tiers and you want fair
      distribution across tiers rather than across raw endpoints.
    * ``router``: single-endpoint router (``RouterAllocator``) where one
      ``(api_key, api_base_url, api_provider)`` serves many model names
      and each name maps to exactly one entry. Set automatically when
      ``TeamAgentSpec.model_router`` is configured; the pool is then the
      flat expansion of that router. Lookup-by-name semantics; no hint
      yields the first declared name as the default.
    """


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
    db_config: DatabaseConfig | MemoryDatabaseConfig = Field(default_factory=DatabaseConfig)
    member_model: Optional[TeamModelConfig] = None
    """TeamModelConfig assigned to this member by the allocator."""


__all__ = [
    "TeamLifecycle",
    "TeamMemberSpec",
    "TeamRole",
    "TeamRuntimeContext",
    "TeamSpec",
]
