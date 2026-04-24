# coding: utf-8
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
)

from openjiuwen.agent_teams.constants import (
    DEFAULT_LEADER_MEMBER_NAME,
    HUMAN_AGENT_MEMBER_NAME,
    RESERVED_MEMBER_NAMES,
)
from openjiuwen.agent_teams.i18n import t
from openjiuwen.agent_teams.schema.deep_agent_spec import DeepAgentSpec
from openjiuwen.agent_teams.schema.team import (
    ModelPoolEntry,
    TeamLifecycle,
    TeamMemberSpec,
    TeamRole,
    TeamRuntimeContext,
    TeamSpec,
)
from openjiuwen.agent_teams.team_workspace.models import TeamWorkspaceConfig
from openjiuwen.agent_teams.worktree.models import WorktreeConfig
from openjiuwen.core.single_agent.schema.agent_card import AgentCard

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
    is configured with ``by_model_name`` strategy.

    Forwarded to ``ModelAllocator.allocate`` at ``build()`` time so the
    leader draws an endpoint from the named group. Ignored by the
    ``round_robin`` strategy (which always allocates regardless of name).
    ``None`` (default) means the leader uses its per-agent model.
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
    """
    model_pool_strategy: Literal["round_robin", "by_model_name"] = "round_robin"
    """Allocation strategy applied to ``model_pool``.

    Mirrors ``TeamSpec.model_pool_strategy`` and propagates to it at
    ``build()`` time. See ``TeamSpec.model_pool_strategy`` for the
    semantics of each option.
    """
    team_mode: Literal["default", "predefined", "hybrid"] | None = None
    """Team operating mode.

    ``None`` (default) derives the mode automatically: "predefined" when
    ``predefined_members`` is non-empty, "default" otherwise. Set
    explicitly to "hybrid" to keep predefined members while still
    allowing ``spawn_member`` calls during execution.
    """
    transport: Optional[TransportSpec] = None
    storage: Optional[StorageSpec] = None
    worktree: Optional[WorktreeConfig] = None
    """Optional worktree isolation config for team members."""
    workspace: Optional[TeamWorkspaceConfig] = None
    """Optional shared workspace config for team members."""
    metadata: dict[str, Any] = {}
    enable_hitt: bool = False
    """Enable Human-in-the-Team mode.

    When True, the runtime auto-registers a reserved ``human_agent``
    member alongside the declared roster. The human_agent is a first-
    class team member that the leader can assign tasks to via
    ``update_task``; it only has access to ``send_message`` and never
    goes through spawn / startup lifecycle. Setting this to True is
    also exposed to the leader as a ``build_team(enable_hitt=...)``
    tool parameter so the leader can turn HITT on dynamically when
    the user expresses intent to join the team.
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
    """Optional callback invoked on each member's DeepAgent after creation.

    Signature: ``(deep_agent: DeepAgent) -> None``.
    Used by platform adapters to inject additional rails / tools.
    Not serializable — only usable with in-process spawn mode.
    """

    def build(self) -> "TeamAgent":
        """Materialize a configured TeamAgent from this spec."""
        from openjiuwen.agent_teams.agent.team_agent import TeamAgent as _TeamAgent
        from openjiuwen.agent_teams.tools.database import DatabaseConfig as _DatabaseConfig
        from openjiuwen.harness.prompts import resolve_language

        leader_agent = self.agents.get("leader")
        if leader_agent is None:
            raise ValueError("agents dict must contain a 'leader' key")

        self._validate_reserved_names()
        self._inject_human_agent_if_enabled()

        resolved_language = resolve_language(self.language)
        for role_spec in self.agents.values():
            if role_spec.language is None:
                role_spec.language = resolved_language

        team_spec = TeamSpec(
            team_name=self.team_name,
            display_name=self.team_name,
            leader_member_name=self.leader.member_name,
            language=resolved_language,
            model_pool=list(self.model_pool),
            model_pool_strategy=self.model_pool_strategy,
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

        # Build the allocator now (rather than inside ``_setup_infra``) so
        # the leader can draw from the same rotation as teammates: with a
        # configured pool we pre-allocate the leader's model here and inject
        # it into the runtime context. Without a pool the legacy
        # PerAgentModelAllocator returns ``None`` for the leader and the
        # downstream ``ctx.member_model or agent_spec.model`` fallback in
        # ``TeamAgent._setup_agent`` keeps behavior unchanged.
        from openjiuwen.agent_teams.agent.model_allocator import build_model_allocator

        model_allocator = build_model_allocator(self, team_spec)
        leader_member_model = (
            model_allocator.allocate(model_name=self.leader.model_name)
            if team_spec.model_pool
            else None
        )

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
        # Hand the already-built allocator to the agent before ``configure``
        # runs so ``_setup_infra`` reuses the same rotation state for
        # subsequent teammate spawns instead of spawning a fresh instance.
        agent.attach_model_allocator(model_allocator)
        agent.configure(self, context)
        return agent

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

    def _inject_human_agent_if_enabled(self) -> None:
        """Ensure at least one human-agent member exists when HITT is on.

        ``enable_hitt=True`` is a convenience that bootstraps a single
        default ``human_agent`` member. If the caller already declared
        one or more members with ``role_type=HUMAN_AGENT`` (including
        under custom names), nothing is added — the explicit roster
        wins and multi-human teams work out of the box.
        Idempotent across repeated ``build()`` calls.
        """
        if not self.enable_hitt:
            return
        if any(m.role_type == TeamRole.HUMAN_AGENT for m in self.predefined_members):
            return
        self.predefined_members.append(
            TeamMemberSpec(
                member_name=HUMAN_AGENT_MEMBER_NAME,
                display_name=t("hitt.human_agent_display_name"),
                role_type=TeamRole.HUMAN_AGENT,
                persona=t("hitt.human_agent_default_persona"),
            )
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
