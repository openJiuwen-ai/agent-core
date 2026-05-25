# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Team-level schemas."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Literal, Optional

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


@dataclass(frozen=True, slots=True)
class TeamCompletionSnapshot:
    """Counts captured the moment a team satisfies all completion conditions.

    Returned by ``TeamBackend.is_team_completed`` when every member is
    settled, every task is terminal, and no message is left unread. Purely
    informational — consumers re-query the backend if they need detail.
    """

    member_count: int
    task_count: int


class TeamLifecycle(str, Enum):
    """Team lifecycle mode."""

    TEMPORARY = "temporary"
    PERSISTENT = "persistent"


class TeamRole(str, Enum):
    """Supported team roles.

    ``HUMAN_AGENT`` and ``BRIDGE_AGENT`` are avatar-style roles driven
    by external delegates. ``HUMAN_AGENT`` represents a human user
    interacting via the SDK inbox; its DeepAgent stays idle until the
    user explicitly drives it. ``BRIDGE_AGENT`` is a full local teammate
    paired with an external independent agent reachable through a
    pure-text protocol — the local LLM acts as a scheduler while
    concrete work output is produced by the remote agent and surfaced
    through framework-managed mailbox auto-forwarding.
    """

    LEADER = "leader"
    TEAMMATE = "teammate"
    HUMAN_AGENT = "human_agent"
    BRIDGE_AGENT = "bridge_agent"


class BridgeMailboxInjectMode(str, Enum):
    """How a team-side mailbox message is wrapped before forwarding to
    the remote agent via ``BridgeProtocolAdapter.relay``.

    Note this controls the OUTBOUND payload to the remote, not how the
    message appears in the bridge avatar's local context. The bridge
    avatar always sees the original body plus the remote's reply,
    regardless of this mode.

    PASSTHROUGH — body forwarded verbatim with a minimal sender header.
        Suitable when the remote was briefed once via ``connect`` and
        does not need per-message context refresh.
    REPHRASE — full sender context (role + persona + optional task
        hint) wrapped around the body. Suitable for stateless wrapping
        CLIs where every relayed turn needs full context.
    """

    PASSTHROUGH = "passthrough"
    REPHRASE = "rephrase"


class TeamMemberSpec(BaseModel):
    """Declarative input for pre-defining a team member.

    Used only for ``predefined_members`` at team creation time.
    Not a runtime data carrier — spawn/restart paths read from DB directly.

    The ``role_type`` field is restricted to non-bridge roles here so a
    discriminated union (see ``BridgeMemberSpec``) can distinguish
    bridge entries cleanly. New role-specific fields belong on a
    subclass, not on this base.
    """

    model_config = ConfigDict(protected_namespaces=())

    member_name: str
    display_name: str
    role_type: Literal[
        TeamRole.LEADER,
        TeamRole.TEAMMATE,
        TeamRole.HUMAN_AGENT,
    ] = TeamRole.TEAMMATE
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


class BridgeMemberSpec(TeamMemberSpec):
    """Predefined-member spec for a bridge agent.

    A bridge agent is a full local teammate (its DeepAgent runs locally
    and owns the full teammate tool set) paired with a remote
    independent agent reachable via a pure-text protocol
    (``BridgeProtocolAdapter``). The remote produces concrete work
    output; the local LLM acts as a scheduler — choosing when to
    claim/complete tasks, when to reply, and to whom, while passing
    the remote's output through verbatim.

    The framework consults ``BridgeProtocolAdapter`` only on the mailbox
    path: when a team-side message arrives for this member it is
    auto-forwarded to the remote, and the remote's reply is composed
    with the original body before being delivered into the avatar's
    context. The bridge avatar itself never invokes the adapter; it
    is unaware of the protocol layer.
    """

    model_config = ConfigDict(protected_namespaces=())

    role_type: Literal[TeamRole.BRIDGE_AGENT] = TeamRole.BRIDGE_AGENT

    mailbox_inject_mode: BridgeMailboxInjectMode = BridgeMailboxInjectMode.PASSTHROUGH
    """How team-side mailbox messages are wrapped before being relayed
    to the remote. See ``BridgeMailboxInjectMode``."""

    protocol: str = ""
    """Protocol identifier (e.g. ``"a2a"`` / ``"acp"`` / ``"claudecode"``).

    Reserved for future adapter lookup. An empty string means
    "no adapter wired yet" — the bridge member is registered and acts
    as a normal teammate, with auto-forwarding falling back to the
    ``remote agent unavailable`` sentinel.
    """

    adapter_config: dict[str, Any] = Field(default_factory=dict)
    """Free-form adapter parameters (endpoint URL, auth handle,
    relay timeout, ...). Verbatim passthrough to
    ``BridgeProtocolAdapter.connect``; the runtime never inspects it.
    """


class ExternalCliAgentSpec(BaseModel):
    """Static launch config for one kind of external CLI agent.

    Pre-declared on ``TeamAgentSpec.external_cli_agents`` so the runtime
    ``spawn_member(role_type='external_cli', cli_agent=...)`` call only needs
    to name the CLI kind — all launch knowledge lives here, not in the spawn
    call. One entry configures every member spawned under its ``cli_agent``
    name (each member still gets its own subprocess and team-join identity).
    """

    model_config = ConfigDict(protected_namespaces=())

    cli_agent: str
    """Adapter kind identifier (``"claude"`` / ``"codex"`` / ``"openclaw"`` /
    ``"hermes"``). Selects the built-in adapter and is the value passed to
    ``spawn_member(cli_agent=...)``. See ``agent_teams/external/cli_agent``."""

    command: Optional[list[str]] = None
    """Full launch argv overriding the adapter's built-in command (e.g. an
    absolute binary path or extra flags). ``None`` uses the built-in command."""

    cwd: Optional[str] = None
    """Working directory for the CLI subprocess. ``None`` inherits the team
    process cwd. Set to the team workspace so the CLI's native file writes
    land in the shared workspace."""

    inject_mcp: bool = True
    """Whether the spawn path auto-registers the team MCP server with the CLI
    so it gets the team collaboration tools (read_inbox / claim_task / ...).
    Injection is CLI-specific (claude ``--mcp-config``, codex
    ``-c mcp_servers...``); adapters without an injection strategy ignore it."""

    mcp_server_command: list[str] = Field(default_factory=lambda: ["openjiuwen-team-mcp"])
    """Launch argv for the team MCP stdio server registered with the CLI.
    Defaults to the ``openjiuwen-team-mcp`` console-script entry."""

    env: dict[str, str] = Field(default_factory=dict)
    """Extra environment variables for the CLI subprocess, merged over the
    inherited process env (the team-join descriptor is injected separately)."""


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
    cli_agent: Optional[str] = None
    """When set, this teammate is driven by an external CLI agent (the named
    adapter, e.g. ``"claude"`` / ``"codex"``) instead of a local DeepAgent.

    The spawn path launches the CLI as a subprocess and the configurator
    builds an ``ExternalCliRuntime`` in place of ``TeamHarness``. ``None``
    (default) keeps the standard DeepAgent-backed member. See
    ``agent_teams/external/cli_agent``.
    """


__all__ = [
    "BridgeMailboxInjectMode",
    "BridgeMemberSpec",
    "ExternalCliAgentSpec",
    "MemberOpResult",
    "TeamCompletionSnapshot",
    "TeamLifecycle",
    "TeamMemberSpec",
    "TeamRole",
    "TeamRuntimeContext",
    "TeamSpec",
]
