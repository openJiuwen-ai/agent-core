# coding: utf-8
"""Team-level specifications for constructing and configuring AgentTeams.

DeepAgent-scoped specs live in ``deep_agent_spec``.  This module
re-exports them so existing ``from …blueprint import DeepAgentSpec``
keeps working.
"""
from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel

from openjiuwen.agent_teams.schema.team import TeamLifecycle, TeamMemberSpec
from openjiuwen.core.single_agent.schema.agent_card import AgentCard

from openjiuwen.agent_teams.schema.deep_agent_spec import DeepAgentSpec

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

        _TRANSPORT_REGISTRY["team_runtime"] = MessagerTransportConfig
        _TRANSPORT_REGISTRY["pyzmq"] = MessagerTransportConfig

    if not _STORAGE_REGISTRY:
        from openjiuwen.agent_teams.tools.database import DatabaseConfig

        _STORAGE_REGISTRY["sqlite"] = DatabaseConfig


class TransportSpec(BaseModel):
    """Pluggable transport layer specification.

    Resolved via registry: built-in types include "team_runtime" and "pyzmq".
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

    member_id: str = "team_leader"
    name: str = "TeamLeader"
    persona: str = "天才项目管理专家"
    domain: str = "project_management"


class TeamAgentSpec(BaseModel):
    """Fully JSON-serializable specification for constructing a TeamAgent.

    Composes per-role DeepAgentSpecs with team-level configuration.
    The ``agents`` dict keys correspond to TeamRole values ("leader", "teammate").
    """

    agents: dict[str, DeepAgentSpec]
    team_name: str = "agent_team"
    lifecycle: str = TeamLifecycle.TEMPORARY
    teammate_mode: str = "plan_mode"
    leader: LeaderSpec = LeaderSpec()
    predefined_members: list[TeamMemberSpec] = []
    transport: Optional[TransportSpec] = None
    storage: Optional[StorageSpec] = None
    metadata: dict[str, Any] = {}

    def build(self) -> "TeamAgent":
        """Materialize a configured TeamAgent from this spec."""
        from openjiuwen.agent_teams.agent.team_agent import TeamAgent
        from openjiuwen.agent_teams.schema.team import TeamRuntimeContext
        from openjiuwen.agent_teams.schema.team import TeamRole, TeamSpec
        from openjiuwen.agent_teams.tools.database import DatabaseConfig as _DatabaseConfig

        leader_agent = self.agents.get("leader")
        if leader_agent is None:
            raise ValueError("agents dict must contain a 'leader' key")

        leader_member = TeamMemberSpec(
            member_id=self.leader.member_id,
            name=self.leader.name,
            role_type=TeamRole.LEADER,
            persona=self.leader.persona,
            domain=self.leader.domain,
        )
        team_spec = TeamSpec(
            team_id=self.team_name,
            name=self.team_name,
            leader_member_id=self.leader.member_id,
        )

        messager_config = self.transport.build() if self.transport else None
        db_config = self.storage.build() if self.storage else _DatabaseConfig()

        leader_card = leader_agent.card or AgentCard(
            id=self.leader.member_id,
            name=self.leader.name,
            description=f"Leader of team {self.team_name}",
        )

        context = TeamRuntimeContext(
            role=TeamRole.LEADER,
            member_spec=leader_member,
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
