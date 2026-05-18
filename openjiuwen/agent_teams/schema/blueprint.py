# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Team-level specifications for constructing and configuring AgentTeams.

DeepAgent-scoped specs live in ``deep_agent_spec``.  This module
re-exports them so existing ``from …blueprint import DeepAgentSpec``
keeps working.
"""

from __future__ import annotations

from typing import (
    TYPE_CHECKING,
    Any,
    Callable,
    Literal,
    Optional,
)

from pydantic import (
    BaseModel,
    Field,
    model_validator,
)

from openjiuwen.agent_teams.constants import (
    DEFAULT_LEADER_MEMBER_NAME,
    RESERVED_MEMBER_NAMES,
)
from openjiuwen.agent_teams.i18n import t
from openjiuwen.agent_teams.memory import TeamMemoryConfig
from openjiuwen.agent_teams.models.pool import ModelPoolEntry, ModelRouterConfig
from openjiuwen.agent_teams.schema.deep_agent_spec import DeepAgentSpec
from openjiuwen.agent_teams.schema.team import (
    TeamLifecycle,
    TeamMemberSpec,
    TeamRole,
    TeamRuntimeContext,
    TeamSpec,
)
from openjiuwen.agent_teams.team_workspace.models import TeamWorkspaceConfig
from openjiuwen.core.single_agent.schema.agent_card import AgentCard
from openjiuwen.harness.tools.worktree import WorktreeConfig

if TYPE_CHECKING:
    # Resolved for type-checkers only; the runtime import lives in ``build()``
    # below to sidestep the blueprint <-> team_agent module import cycle.
    from openjiuwen.agent_teams.agent.team_agent import TeamAgent

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
        _STORAGE_REGISTRY["postgresql"] = DatabaseConfig
        _STORAGE_REGISTRY["mysql"] = DatabaseConfig
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
            raise ValueError(f"Unknown transport type '{self.type}'. Registered types: {list(_TRANSPORT_REGISTRY)}")
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
            raise ValueError(f"Unknown storage type '{self.type}'. Registered types: {list(_STORAGE_REGISTRY)}")
        merged = {"db_type": self.type, **self.params}
        return config_cls.model_validate(merged)


# ---------------------------------------------------------------------------
# TeamAgentSpec
# ---------------------------------------------------------------------------


class LeaderSpec(BaseModel):
    """Leader identity specification."""

    model_config = {"protected_namespaces": ()}

    member_name: str = "team_leader"
    display_name: str = "Team Leader"
    persona: str = Field(default_factory=lambda: t("blueprint.default_persona"))
    model_name: Optional[str] = None
    """Optional pool model_name to allocate from when ``TeamSpec.model_pool``
    is configured with ``by_model_name`` or ``router`` strategy.

    Forwarded to ``ModelAllocator.allocate`` at ``build()`` time so the
    leader draws an endpoint from the named group (``by_model_name``) or
    the named router entry (``router``). Ignored by the ``round_robin``
    strategy (which always allocates regardless of name). ``None``
    (default) means the leader uses its per-agent model — except under
    ``router``, where it falls back to the router's first declared
    model_name.
    """


class TeamAgentSpec(BaseModel):
    """Fully JSON-serializable specification for constructing a TeamAgent.

    Composes per-role DeepAgentSpecs with team-level configuration.
    The ``agents`` dict keys correspond to TeamRole values ("leader", "teammate").
    """

    model_config = {"protected_namespaces": ()}

    agents: dict[str, DeepAgentSpec]
    team_name: str = "agent_team"
    lifecycle: str = TeamLifecycle.TEMPORARY
    teammate_mode: str = "build_mode"
    spawn_mode: str = "process"
    leader: LeaderSpec = LeaderSpec()
    predefined_members: list[TeamMemberSpec] = []
    model_pool: list[ModelPoolEntry] = []
    """Optional pool of LLM endpoints shared by every team member.

    When non-empty, ``ModelAllocator`` distributes pool entries across
    leader and teammates (round-robin by default) so concurrent calls
    spread across endpoints instead of saturating a single one. When
    empty (default), members fall back to the per-agent ``model`` declared
    in ``agents`` and behavior is unchanged. Propagated to ``TeamSpec``
    at ``build()`` time so allocators reachable from runtime context
    see the same pool.

    Mutually exclusive with ``model_router``: configure one or the other,
    never both.
    """
    model_router: Optional[ModelRouterConfig] = None
    """Optional single-endpoint router configuration.

    Convenience input for backends that serve many model names through
    one ``(api_key, api_base_url, api_provider)`` triple (OpenRouter,
    LiteLLM proxy, ...). At ``build()`` time the router is expanded into
    ``TeamSpec.model_pool`` (one entry per declared name) and
    ``model_pool_strategy`` is set to ``"router"``, so all downstream
    machinery (``resolve_member_model``, ``inherit_pool_ids``,
    ``update_model_pool``) keeps working against the flat pool view.

    Mutually exclusive with ``model_pool``. The first declared model
    name acts as the team's default — ``RouterAllocator.allocate()``
    with no hint returns it, so the leader can run without an explicit
    ``leader.model_name``.
    """
    model_pool_strategy: Literal["round_robin", "by_model_name", "router"] = "round_robin"
    """Allocation strategy applied to ``model_pool``.

    Mirrors ``TeamSpec.model_pool_strategy`` and propagates to it at
    ``build()`` time. See ``TeamSpec.model_pool_strategy`` for the
    semantics of each option. When ``model_router`` is set, ``build()``
    forces this to ``"router"`` regardless of the configured value.
    """
    team_mode: Literal["default", "predefined", "hybrid"] | None = None
    """Team operating mode.

    ``None`` (default) derives the mode automatically: "hybrid" when
    ``predefined_members`` has non-human members, "default" otherwise.
    "hybrid" keeps the predefined roster while still allowing
    ``spawn_member`` calls during execution. Set explicitly to
    "predefined" to lock the roster and drop the leader's
    ``spawn_member`` tool.
    """
    transport: Optional[TransportSpec] = None
    """Pluggable transport layer specification.

    When unset, the framework picks a sensible default based on
    ``spawn_mode``: ``"inprocess"`` spawn implies an in-process messager,
    so ``transport`` is materialized as ``TransportSpec(type="inprocess")``
    during validation; ``"process"`` spawn keeps ``None`` and forces the
    caller to configure a cross-process backend (e.g. ``"pyzmq"``) when
    teammates are involved.
    """
    storage: Optional[StorageSpec] = None
    worktree: Optional[WorktreeConfig] = None
    """Optional worktree isolation config for team members."""
    workspace: Optional[TeamWorkspaceConfig] = None
    """Optional shared workspace config for team members."""
    metadata: dict[str, Any] = {}
    enable_hitt: bool = False
    """Spec-level Human-in-the-Team capability ceiling.

    True opens the capability — the framework will register every
    HUMAN_AGENT member declared in ``predefined_members`` during
    ``build_team``, and the leader's ``spawn_member`` tool may
    additionally bring up new human members at runtime via
    ``role_type='human_agent'``. False forbids both paths.

    The framework does **not** inject any default ``human_agent``
    when this flag is True — callers must declare the human roster
    explicitly via ``predefined_members`` (or rely on dynamic
    ``spawn_member`` after build).

    Consistency check (``build()`` time):
    - ``enable_hitt=False`` with any HUMAN_AGENT in predefined → error.
    - ``enable_hitt=True`` with no HUMAN_AGENT predefined → allowed
      (dynamic spawn path).

    The ``build_team`` tool exposes its own ``enable_hitt`` parameter
    that gates the runtime instance: it may downgrade an open ceiling
    to disabled, but cannot exceed it.
    """
    expose_human_agents_to_teammates: bool = False
    """Whether to expose the concrete human_agent roster to teammate
    prompts.

    False (default, fail-safe): teammates receive a short HITT section
    that **does not list** any human_agent ``member_name`` and does
    not say "real humans". It only carries the role-neutral guidance
    relevant to working with possibly-asynchronous peers (always use
    ``send_message`` for cross-member contact, tolerate response
    latency, do not infer peer identity). This keeps peer role
    (teammate vs human_agent) hidden from other members' system
    prompts.

    True: teammates receive the legacy HITT section that lists every
    registered human_agent ``member_name`` inline with a "real humans"
    label. Use this only when the deploying team explicitly wants
    every teammate to know which peers are human-driven (e.g.
    internal collaboration where role transparency is desired).

    Has no effect on LEADER or HUMAN_AGENT prompts: leader always
    sees the full roster (it owns spawn / approval flows); a
    human_agent always sees the roster (it includes itself).
    """
    language: Optional[str] = None
    """Preferred language for prompts and tool descriptions ("cn" or "en").

    Propagated to every per-role ``DeepAgentSpec`` (when the role spec does
    not set its own language) and recorded on ``TeamSpec`` so team tools
    can pick up the same locale.  Resolved to the nearest supported language
    at ``build()`` time via ``resolve_language()``.
    """

    agent_customizer: Optional[Callable[..., None]] = Field(
        default=None,
        exclude=True,
    )
    """Optional callback invoked on each member's DeepAgent after creation."""

    memory: Optional[TeamMemoryConfig] = None
    """Optional team memory configuration. When enabled, TeamMemoryManager
    replaces default MemoryRail/CodingMemoryRail with per-member isolated instances."""

    """
    Signature: ``(deep_agent: DeepAgent) -> None``.
    Used by platform adapters to inject additional rails / tools.
    Not serializable — only usable with in-process spawn mode.
    """

    @model_validator(mode="after")
    def _validate_pool_router_exclusive(self) -> "TeamAgentSpec":
        """Reject configs that set both ``model_pool`` and ``model_router``.

        The two fields describe overlapping concerns — a flat list of
        endpoints versus a router-shaped declaration that expands into
        the same kind of list. Allowing both leaves the strategy and
        the materialized pool ambiguous, so we surface the conflict
        early instead of silently picking one.
        """
        if self.model_router is not None and self.model_pool:
            raise ValueError(
                "model_pool and model_router are mutually exclusive; configure one or the other",
            )
        return self

    @model_validator(mode="after")
    def _default_transport_for_spawn_mode(self) -> "TeamAgentSpec":
        """Fill an in-process transport default when spawn_mode='inprocess'.

        ``spawn_mode='inprocess'`` co-locates teammates in the leader's
        event loop, so the only transport that makes sense is the
        in-process messager. Materializing the default here (rather than
        inside ``build()``) keeps the spec self-describing: a dumped spec
        always carries the transport that will actually be used, which
        matters for cross-process spawn payloads and for callers that
        introspect the spec without building it.

        Cross-process spawn (``"process"``) intentionally keeps
        ``transport=None`` so the caller is forced to configure a real
        cross-process backend (e.g. ``"pyzmq"``) when teammates are
        involved.
        """
        if self.transport is None and self.spawn_mode == "inprocess":
            self.transport = TransportSpec(type="inprocess")
        return self

    def resolve_db_config(self):
        """Resolve the DatabaseConfig this spec would use at build time.

        Mirrors the materialisation step inside ``build()`` so callers that
        only need the storage handle (e.g. the runtime manager probing the
        static team table before deciding the run path) can obtain the same
        config without constructing a TeamAgent.
        """
        from openjiuwen.agent_teams.tools.database import DatabaseConfig as _DatabaseConfig

        db_config = self.storage.build() if self.storage else _DatabaseConfig()
        if db_config.db_type == "sqlite" and not db_config.connection_string:
            from openjiuwen.agent_teams.paths import get_agent_teams_home

            db_config.connection_string = str(get_agent_teams_home() / "team.db")
        return db_config

    def build(self) -> "TeamAgent":
        """Materialize a configured TeamAgent from this spec."""
        from openjiuwen.agent_teams.agent.team_agent import TeamAgent as _TeamAgent
        from openjiuwen.harness.prompts import resolve_language

        leader_agent = self.agents.get("leader")
        if leader_agent is None:
            raise ValueError("agents dict must contain a 'leader' key")

        self._validate_reserved_names()
        self._validate_hitt_consistency()

        resolved_language = resolve_language(self.language)
        for role_spec in self.agents.values():
            if role_spec.language is None:
                role_spec.language = resolved_language

        # ``model_router`` is a convenience input that expands into the
        # flat ``model_pool`` view at build time. Doing the expansion here
        # keeps every downstream component (resolver, pool refresh, DB
        # ref lookup) on a single code path — they only ever see entries.
        if self.model_router is not None:
            team_pool = self.model_router.to_pool_entries()
            team_strategy: Literal["round_robin", "by_model_name", "router"] = "router"
        else:
            team_pool = list(self.model_pool)
            team_strategy = self.model_pool_strategy

        team_spec = TeamSpec(
            team_name=self.team_name,
            display_name=self.team_name,
            leader_member_name=self.leader.member_name,
            language=resolved_language,
            model_pool=team_pool,
            model_pool_strategy=team_strategy,
        )

        messager_config = self.transport.build() if self.transport else None
        db_config = self.resolve_db_config()

        leader_card_id = f"{self.team_name}_{self.leader.member_name}"
        leader_card = leader_agent.card or AgentCard(
            id=leader_card_id,
            name=self.leader.display_name,
            description=f"Leader of team {self.team_name}",
        )

        # Build the allocator now (rather than inside ``_setup_infra``) so
        # the leader can draw from the same rotation as teammates: with a
        # configured pool we pre-allocate the leader's model here and inject
        # it into the runtime context. Without a pool the legacy
        # PerAgentModelAllocator returns ``None`` for the leader and the
        # downstream ``ctx.member_model or agent_spec.model`` fallback in
        # ``TeamAgent._setup_agent`` keeps behavior unchanged.
        from openjiuwen.agent_teams.models.allocator import build_model_allocator

        model_allocator = build_model_allocator(self, team_spec)
        leader_allocation = (
            model_allocator.allocate(model_name=self.leader.model_name) if model_allocator is not None else None
        )
        leader_member_model = leader_allocation.to_team_model_config() if leader_allocation else None
        self._validate_leader_model_resolved(leader_agent, leader_member_model, team_spec)

        context = TeamRuntimeContext(
            role=TeamRole.LEADER,
            member_name=self.leader.member_name,
            persona=self.leader.persona,
            team_spec=team_spec,
            messager_config=messager_config,
            db_config=db_config,
            member_model=leader_member_model,
        )

        agent = _TeamAgent(leader_card)
        # Hand the already-built allocator (and the leader's allocation)
        # to the agent before ``configure`` runs so ``_setup_infra``
        # reuses the same rotation state for subsequent teammate spawns
        # instead of spawning a fresh instance, and so the leader's DB
        # ref matches what was actually allocated at build time.
        agent.attach_model_allocator(model_allocator, leader_allocation=leader_allocation)
        agent.configure(self, context)
        return agent

    def _validate_leader_model_resolved(
        self,
        leader_agent: DeepAgentSpec,
        leader_member_model,
        team_spec: TeamSpec,
    ) -> None:
        """Fail fast when the leader has no model to drive its DeepAgent.

        With a configured pool, ``ByModelNameAllocator`` returns ``None``
        when ``leader.model_name`` is missing or unknown, leaving the
        leader to fall through to ``agents['leader'].model``. If that
        too is unset the underlying DeepAgent is built with no model
        and the failure surfaces only at the first LLM invocation as
        a confusing "model_client_config is required" error.

        ``RouterAllocator`` always falls back to the first declared
        ``model_name`` when no hint is given, so this path only trips
        when ``leader.model_name`` is set to a name that isn't in the
        router's list (or, under ``by_model_name``, in any pool group).

        Surface it at ``build()`` time with a clear remediation list
        so the inconsistency is caught while the user is still looking
        at the spec.
        """
        if leader_member_model is not None or leader_agent.model is not None:
            return
        if not team_spec.model_pool:
            return

        from openjiuwen.core.common.exception.codes import StatusCode
        from openjiuwen.core.common.exception.errors import raise_error

        available_names = sorted({entry.model_name for entry in team_spec.model_pool})
        strategy = team_spec.model_pool_strategy
        leader_name = self.leader.model_name
        if leader_name and leader_name not in available_names:
            scope = "router" if strategy == "router" else "pool"
            cause = (
                f"leader.model_name='{leader_name}' is not present in the {scope} (available names: {available_names})"
            )
        elif strategy == "by_model_name":
            cause = "model_pool_strategy='by_model_name' requires leader.model_name to be set to one of the pool names"
        else:
            cause = "the allocator did not produce a model for the leader"

        if strategy == "router":
            tail = (
                f"(1) leave leader.model_name unset to fall back on the router's first declared name, "
                f"(2) set leader.model_name to one of {available_names}, "
                f"(3) provide an explicit agents['leader'].model in the spec"
            )
        else:
            tail = (
                f"(1) set leader.model_name to one of {available_names}, "
                f"(2) provide an explicit agents['leader'].model in the spec, "
                f"(3) switch model_pool_strategy to 'round_robin' (always allocates)"
            )
        reason = f"{cause}; resolve by either: {tail}"
        raise_error(StatusCode.AGENT_TEAM_CONFIG_INVALID, reason=reason)

    def _validate_reserved_names(self) -> None:
        """Reject user-declared members that collide with reserved names.

        ``human_agent`` and ``user`` are owned by the runtime and must
        never collide with user-declared identities. ``team_leader`` is
        the default leader name so the leader itself is allowed to use
        it, but a teammate must not — otherwise two members would share
        a name in the roster.
        """
        # Leader may keep the default ``team_leader`` but cannot claim
        # the user/human_agent identities.
        leader_forbidden = RESERVED_MEMBER_NAMES - {DEFAULT_LEADER_MEMBER_NAME}
        if self.leader.member_name in leader_forbidden:
            raise ValueError(f"LeaderSpec.member_name '{self.leader.member_name}' is reserved; pick a different name")
        for member in self.predefined_members:
            # A HITT-auto-injected human_agent is legal here; anything
            # else under a reserved name is not.
            if member.role_type == TeamRole.HUMAN_AGENT:
                continue
            if member.member_name in RESERVED_MEMBER_NAMES:
                raise ValueError(
                    f"predefined member '{member.member_name}' uses a "
                    f"reserved name (reserved: "
                    f"{sorted(RESERVED_MEMBER_NAMES)})"
                )

    def _validate_hitt_consistency(self) -> None:
        """Reject configs where predefined HUMAN_AGENT members exist without HITT enabled.

        ``enable_hitt`` acts as the spec-level capability ceiling. Declaring
        a HUMAN_AGENT predefined member without opening that ceiling is a
        misconfiguration. The reverse (``enable_hitt=True`` with no
        predefined HUMAN_AGENT) is allowed: callers may rely on dynamic
        ``spawn_member(role_type='human_agent', ...)`` after build.
        """
        if self.enable_hitt:
            return
        if not any(m.role_type == TeamRole.HUMAN_AGENT for m in self.predefined_members):
            return

        from openjiuwen.core.common.exception.codes import StatusCode
        from openjiuwen.core.common.exception.errors import raise_error

        offenders = [m.member_name for m in self.predefined_members if m.role_type == TeamRole.HUMAN_AGENT]
        raise_error(
            StatusCode.AGENT_TEAM_CONFIG_INVALID,
            reason=(
                f"predefined_members contains HUMAN_AGENT role(s) {offenders} "
                f"but enable_hitt=False; set enable_hitt=True (capability ceiling) "
                f"or remove the human member(s)"
            ),
        )


__all__ = [
    "DeepAgentSpec",
    "LeaderSpec",
    "StorageSpec",
    "TeamAgentSpec",
    "TransportSpec",
    "register_storage",
    "register_transport",
]
