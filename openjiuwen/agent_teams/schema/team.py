# coding: utf-8
"""Team-level schemas."""

from __future__ import annotations

import uuid
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


class ModelPoolEntry(BaseModel):
    """Single LLM endpoint in a team's allocation pool.

    Pool entries describe a usable model endpoint together with the
    credentials and provider needed to reach it. ``ModelAllocator`` draws
    entries from the pool and converts them into ``TeamModelConfig`` at
    allocation time so each team member can talk to a different endpoint
    and avoid single-endpoint rate-limit contention.
    """

    model_config = ConfigDict(protected_namespaces=())

    model_name: str
    api_key: str
    api_base_url: str
    api_provider: str
    description: Optional[str] = None
    model_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    metadata: dict = Field(default_factory=dict)
    """Optional extension payload merged into the materialized TeamModelConfig.

    Two reserved sub-keys feed ``to_team_model_config``:

    * ``client``: dict merged into ``ModelClientConfig`` (e.g. ``timeout``,
      ``verify_ssl``, ``ssl_cert``, ``max_retries``, ``custom_headers``,
      or any provider-specific extras allowed by the client schema).
    * ``request``: dict merged into ``ModelRequestConfig`` (e.g.
      ``temperature``, ``top_p``, ``max_tokens``, ``stop``).

    Explicit fields on the pool entry (``api_key``, ``api_base_url``,
    ``api_provider``, ``model_name``, ``model_id``) always win over the
    same key under ``client`` / ``request`` — those keys belong on the
    pool entry itself rather than buried in metadata. Any other top-level
    keys are free-form and reserved for allocator policies (e.g. weights,
    affinity hints) and are not consumed during materialization.
    """

    def to_team_model_config(self) -> TeamModelConfig:
        """Materialize a TeamModelConfig from this pool entry.

        Reserved ``metadata.client`` and ``metadata.request`` sub-dicts
        are merged into the corresponding sub-config. Pool-entry fields
        always override same-named keys in metadata so the explicit
        column wins over the optional bag.
        """
        from openjiuwen.core.foundation.llm import (
            ModelClientConfig,
            ModelRequestConfig,
        )

        client_extra = dict(self.metadata.get("client") or {})
        request_extra = dict(self.metadata.get("request") or {})

        client_kwargs = {
            **client_extra,
            "client_id": self.model_id,
            "client_provider": self.api_provider,
            "api_key": self.api_key,
            "api_base": self.api_base_url,
        }
        request_kwargs = {
            **request_extra,
            "model": self.model_name,
        }

        return TeamModelConfig(
            model_client_config=ModelClientConfig(**client_kwargs),
            model_request_config=ModelRequestConfig(**request_kwargs),
        )


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
    "ModelPoolEntry",
    "TeamLifecycle",
    "TeamMemberSpec",
    "TeamRole",
    "TeamRuntimeContext",
    "TeamSpec",
]
