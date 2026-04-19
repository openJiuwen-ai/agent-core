# coding: utf-8
"""Team-level specifications for constructing and configuring AgentTeams.

DeepAgent-scoped specs live in ``deep_agent_spec``.  This module
re-exports them so existing ``from …blueprint import DeepAgentSpec``
keeps working.
"""
from __future__ import annotations

from typing import (
    Any,
    Callable,
    Optional,
)

from pydantic import (
    BaseModel,
    Field,
)

from openjiuwen.agent_teams.schema.deep_agent_spec import DeepAgentSpec
from openjiuwen.agent_teams.schema.team import (
    TeamLifecycle,
    TeamMemberSpec,
)
from openjiuwen.agent_teams.team_workspace.models import TeamWorkspaceConfig
from openjiuwen.agent_teams.worktree.models import WorktreeConfig
from openjiuwen.core.single_agent.schema.agent_card import AgentCard

# ---------------------------------------------------------------------------
# Transport / Storage registries (pluggable team infrastructure)
# ---------------------------------------------------------------------------

_TRANSPORT_REGISTRY: dict[str, type[BaseModel]] = {}
_STORAGE_REGISTRY: dict[str, type[BaseModel]] = {}


def register_transport(name: str, cls: type[BaseModel]) -> None:
    """Register a transport config class for use in TransportSpec."""
    _TRANSPORT_REGISTRY[name] = cls


def register_storage(name: str, cls: type[BaseModel]) -> None:
    """Register a storage config class for use in StorageSpec."""
    _STORAGE_REGISTRY[name] = cls


def _ensure_builtin_infra_registered() -> None:
    """Lazily populate transport/storage registries with built-in types."""
    if not _TRANSPORT_REGISTRY:
        from openjiuwen.agent_teams.messager.base import MessagerTransportConfig

        _TRANSPORT_REGISTRY["inprocess"] = MessagerTransportConfig
        _TRANSPORT_REGISTRY["pyzmq"] = MessagerTransportConfig

    if not _STORAGE_REGISTRY:
        from openjiuwen.agent_teams.tools.database import DatabaseConfig
        from openjiuwen.agent_teams.tools.memory_database import MemoryDatabaseConfig

        _STORAGE_REGISTRY["sqlite"] = DatabaseConfig
        _STORAGE_REGISTRY["memory"] = MemoryDatabaseConfig


class TransportSpec(BaseModel):
    """Pluggable transport layer specification.

    Resolved via registry: built-in types include "inprocess" and "pyzmq".
    Register custom transports with ``register_transport()``.
    """

    type: str
    params: dict[str, Any] = {}

    def build(self) -> BaseModel:
        _ensure_builtin_infra_registered()
        config_cls = _TRANSPORT_REGISTRY.get(self.type)
        if config_cls is None:
            raise ValueError(
                f"Unknown transport type '{self.type}'. "
                f"Registered types: {list(_TRANSPORT_REGISTRY)}"
            )
        merged = {"backend": self.type, **self.params}
        return config_cls.model_validate(merged)


class StorageSpec(BaseModel):
    """Pluggable storage layer specification.

    Resolved via registry: built-in type is "sqlite".
    Register custom storage backends with ``register_storage()``.
    """

    type: str
    params: dict[str, Any] = {}

    def build(self) -> BaseModel:
        _ensure_builtin_infra_registered()
        config_cls = _STORAGE_REGISTRY.get(self.type)
        if config_cls is None:
            raise ValueError(
                f"Unknown storage type '{self.type}'. "
                f"Registered types: {list(_STORAGE_REGISTRY)}"
            )
        merged = {"db_type": self.type, **self.params}
        return config_cls.model_validate(merged)


# ---------------------------------------------------------------------------
# TeamAgentSpec
# ---------------------------------------------------------------------------


class LeaderSpec(BaseModel):
    """Leader identity specification."""

    member_name: str = "team_leader"
    display_name: str = "Team Leader"
    persona: str = "天才项目管理专家"


class TeamAgentSpec(BaseModel):
    """Fully JSON-serializable specification for constructing a TeamAgent.

    Composes per-role DeepAgentSpecs with team-level configuration.
    The ``agents`` dict keys correspond to TeamRole values ("leader", "teammate").
    """

    agents: dict[str, DeepAgentSpec]
    team_name: str = "agent_team"
    lifecycle: str = TeamLifecycle.TEMPORARY
    teammate_mode: str = "build_mode"
    spawn_mode: str = "process"
    leader: LeaderSpec = LeaderSpec()
    predefined_members: list[TeamMemberSpec] = []
    transport: Optional[TransportSpec] = None
    storage: Optional[StorageSpec] = None
    worktree: Optional[WorktreeConfig] = None
    """Optional worktree isolation config for team members."""
    workspace: Optional[TeamWorkspaceConfig] = None
    """Optional shared workspace config for team members."""
    metadata: dict[str, Any] = {}

    agent_customizer: Optional[Callable[..., None]] = Field(
        default=None, exclude=True,
    )
    """Optional callback invoked on each member's DeepAgent after creation.

    Signature: ``(deep_agent: DeepAgent) -> None``.
    Used by platform adapters to inject additional rails / tools.
    Not serializable — only usable with in-process spawn mode.
    """

    def build(self) -> "TeamAgent":
        """Materialize a configured TeamAgent from this spec."""
        from openjiuwen.agent_teams.agent.team_agent import TeamAgent
        from openjiuwen.agent_teams.schema.team import TeamRuntimeContext
        from openjiuwen.agent_teams.schema.team import TeamRole, TeamSpec
        from openjiuwen.agent_teams.tools.database import DatabaseConfig as _DatabaseConfig

        leader_agent = self.agents.get("leader")
        if leader_agent is None:
            raise ValueError("agents dict must contain a 'leader' key")

        team_spec = TeamSpec(
            team_name=self.team_name,
            display_name=self.team_name,
            leader_member_name=self.leader.member_name,
        )

        messager_config = self.transport.build() if self.transport else None
        db_config = self.storage.build() if self.storage else _DatabaseConfig()
        if db_config.db_type == "sqlite" and not db_config.connection_string:
            from openjiuwen.agent_teams.paths import get_agent_teams_home
            db_config.connection_string = str(get_agent_teams_home() / "team.db")

        leader_card_id = f"{self.team_name}_{self.leader.member_name}"
        leader_card = leader_agent.card or AgentCard(
            id=leader_card_id,
            name=self.leader.display_name,
            description=f"Leader of team {self.team_name}",
        )

        context = TeamRuntimeContext(
            role=TeamRole.LEADER,
            member_name=self.leader.member_name,
            persona=self.leader.persona,
            team_spec=team_spec,
            messager_config=messager_config,
            db_config=db_config,
        )

        agent = TeamAgent(leader_card)
        agent.configure(self, context)
        return agent


__all__ = [
    "DeepAgentSpec",
    "LeaderSpec",
    "StorageSpec",
    "TeamAgentSpec",
    "TransportSpec",
    "register_storage",
    "register_transport",
]
