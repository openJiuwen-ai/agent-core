# coding: utf-8
"""Team-level schemas."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from enum import Enum
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from openjiuwen.agent_teams.messager.base import MessagerTransportConfig
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
    is configured with ``by_model_name`` strategy.

    Forwarded to ``ModelAllocator.allocate`` at ``build_team`` time so
    this member draws an endpoint from the named group. Ignored by the
    ``round_robin`` strategy. ``None`` (default) means the member uses
    its per-agent model (or no allocation when the pool is empty).
    """


class ModelPoolEntry(BaseModel):
    """Single LLM endpoint in a team's allocation pool.

    Pool entries describe a usable model endpoint together with the
    credentials and provider needed to reach it. ``ModelAllocator`` draws
    entries from the pool and converts them into ``TeamModelConfig`` at
    allocation time so each team member can talk to a different endpoint
    and avoid single-endpoint rate-limit contention.

    Two identifiers play distinct roles:

    * ``model_id`` (auto-uuid): runtime client identity. Wired through to
      ``ModelClientConfig.client_id`` so the foundation layer's resource
      manager can dedupe / cache the underlying HTTP client across
      members that share the same endpoint. Never persisted to the DB
      and never crosses pool versions — regenerated each time the pool
      is reloaded from spec.
    * ``(model_name, group_index)``: semantic persistence identity.
      Stored in the DB as the member's pool reference; resolved
      positionally against the live session pool so credential
      refreshes propagate without re-spawning members.
    """

    model_config = ConfigDict(protected_namespaces=())

    model_name: str
    api_key: str
    api_base_url: str
    api_provider: str
    description: Optional[str] = None
    model_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    """Process-local client identity for foundation resource manager.

    Auto-generated as a uuid; surfaced as ``ModelClientConfig.client_id``
    when the entry is materialized. Not persisted to the DB.

    ``inherit_pool_ids`` carries this value across ``update_model_pool``
    only when the new entry is bit-exact (every other field equal) to
    an old one — any value change (api_key rotation included) yields a
    fresh id so a future foundation client cache cannot serve a stale
    client built against the old config.
    """
    metadata: dict = Field(default_factory=dict)
    """Optional extension payload merged into the materialized TeamModelConfig.

    Two reserved sub-keys feed ``to_team_model_config``:

    * ``client``: dict merged into ``ModelClientConfig`` (e.g. ``timeout``,
      ``verify_ssl``, ``ssl_cert``, ``max_retries``, ``custom_headers``,
      or any provider-specific extras allowed by the client schema).
    * ``request``: dict merged into ``ModelRequestConfig`` (e.g.
      ``temperature``, ``top_p``, ``max_tokens``, ``stop``).

    Explicit fields on the pool entry (``api_key``, ``api_base_url``,
    ``api_provider``, ``model_name``) always win over the same key under
    ``client`` / ``request`` — those keys belong on the pool entry
    itself rather than buried in metadata. Any other top-level keys are
    free-form and reserved for allocator policies (e.g. weights,
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
    model_pool_strategy: Literal["round_robin", "by_model_name"] = "round_robin"
    """Allocation strategy applied to ``model_pool`` entries.

    * ``round_robin`` (default): linear rotation across every entry in
      pool order, ignoring ``model_name``.
    * ``by_model_name``: rotation that first picks the next distinct
      ``model_name`` group and then advances the within-group rotation,
      so each declared model name receives an equal share of allocations
      regardless of how many endpoints back it. Use when the pool mixes
      models with different cost / capability tiers and you want fair
      distribution across tiers rather than across raw endpoints.
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


def _entry_signature(entry: ModelPoolEntry) -> str:
    """Canonical signature of an entry's full config, excluding ``model_id``.

    Two entries with the same signature describe the same logical
    endpoint plus the same auth, request knobs, and metadata — i.e.
    a future foundation client cache could safely serve one client
    for both. Any difference (api_key rotation included) yields a
    different signature and forces a fresh ``model_id``.
    """
    import json

    payload = entry.model_dump(exclude={"model_id"})
    return json.dumps(payload, sort_keys=True, default=str)


def inherit_pool_ids(
    current_pool: list[ModelPoolEntry],
    new_pool: list[ModelPoolEntry],
) -> list[ModelPoolEntry]:
    """Carry ``model_id`` from ``current_pool`` into bit-exact entries of ``new_pool``.

    ``ModelPoolEntry.model_id`` surfaces as ``ModelClientConfig.client_id``,
    which a future foundation client cache may use to dedupe HTTP
    clients. Preserving it across a pool refresh is only safe when the
    new entry's full config is identical to the old one — otherwise a
    cached client built with the old api_key would silently service
    requests intended to use the new credentials.

    Alignment is therefore by **bit-exact signature**: every field
    other than ``model_id`` must match. When several entries in either
    pool share the same signature (e.g., genuine duplicates), they are
    paired in pool order, one-to-one. New entries that don't have an
    exact counterpart keep their own auto-generated ``model_id``;
    removed entries' ids are dropped.

    Side effects:

    * Order doesn't matter — reordered-but-otherwise-identical pools
      align fully.
    * Any value change (api_key rotation, base_url migration, timeout
      tweak, ...) breaks the match for that entry, forcing a fresh id.
    * Caller-supplied explicit ``model_id`` values are preserved when
      no signature match exists (no overwrite happens for unmatched
      new entries).

    Args:
        current_pool: The pool currently in session.
        new_pool: The replacement pool.

    Returns:
        A list parallel to ``new_pool`` with ``model_id`` inherited
        for bit-exact matches.
    """
    old_by_sig: dict[str, list[ModelPoolEntry]] = {}
    for entry in current_pool:
        old_by_sig.setdefault(_entry_signature(entry), []).append(entry)

    result: list[ModelPoolEntry] = []
    for new_entry in new_pool:
        bucket = old_by_sig.get(_entry_signature(new_entry))
        if bucket:
            inherited_id = bucket.pop(0).model_id
            result.append(new_entry.model_copy(update={"model_id": inherited_id}))
        else:
            result.append(new_entry)
    return result


__all__ = [
    "ModelPoolEntry",
    "TeamLifecycle",
    "TeamMemberSpec",
    "TeamRole",
    "TeamRuntimeContext",
    "TeamSpec",
    "inherit_pool_ids",
]
