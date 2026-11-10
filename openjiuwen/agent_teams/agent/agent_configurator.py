# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Agent configuration, setup, and initialization for TeamAgent."""

from __future__ import annotations

import os
from typing import (
    TYPE_CHECKING,
    Any,
    Callable,
    Optional,
)

from openjiuwen.agent_teams.agent.blueprint import TeamAgentBlueprint
from openjiuwen.agent_teams.agent.infra import TeamInfra
from openjiuwen.agent_teams.agent.payload import SpawnPayloadBuilder
from openjiuwen.agent_teams.agent.resources import PrivateAgentResources
from openjiuwen.agent_teams.harness import TeamHarness
from openjiuwen.agent_teams.kv_cache import kv_cache_hooks
from openjiuwen.agent_teams.messager import (
    Messager,
    create_messager,
)
from openjiuwen.agent_teams.paths import team_home
from openjiuwen.agent_teams.paths import (
    team_memory_dir as default_team_memory_dir,
)
from openjiuwen.agent_teams.runtime.team_plan import is_team_plan_enabled
from openjiuwen.agent_teams.schema.blueprint import TeamAgentSpec
from openjiuwen.agent_teams.schema.deep_agent_spec import RailSpec, SysOperationSpec, WorkspaceSpec
from openjiuwen.agent_teams.schema.team import (
    TeamMemberSpec,
    TeamRole,
    TeamRuntimeContext,
    TeamSpec,
)
from openjiuwen.agent_teams.tools.team import TeamBackend
from openjiuwen.agent_teams.workspace_layout import ensure_team_member_workspace_link
from openjiuwen.core.common.logging import team_logger
from openjiuwen.core.runner.spawn.agent_config import (
    SpawnAgentConfig,
)
from openjiuwen.core.single_agent.schema.agent_card import AgentCard
from openjiuwen.core.sys_operation import LocalWorkConfig, OperationMode
from openjiuwen.harness.prompts import resolve_language as _resolve_language

if TYPE_CHECKING:
    from openjiuwen.agent_teams.agent.member_runtime import MemberRuntime
    from openjiuwen.agent_teams.memory.manager import TeamMemoryManager
    from openjiuwen.agent_teams.models.allocator import Allocation, ModelAllocator
    from openjiuwen.agent_teams.team_workspace.manager import TeamWorkspaceManager
    from openjiuwen.agent_teams.workflow.swarmflow_infra import SwarmflowInfra
    from openjiuwen.harness.tools.worktree import WorktreeManager


_TEAM_WORKTREE_BASH_DENY_PATTERNS = [
    r"\bgit(?:\s+(?:-[A-Za-z](?:\s+\S+)?|--[^\s;&|]+(?:=\S+)?))*\s+worktree\s+"
    r"(?:add|remove|prune|move|repair|lock|unlock)\b",
]


_SPAWN_TOOLS = (
    "spawn_teammate",
    "spawn_human_agent",
    "spawn_bridge_agent",
    "spawn_external_cli",
)


def _resolve_team_mode(spec: TeamAgentSpec) -> str:
    """Resolve effective team_mode for spawn gating.

    - Explicit ``spec.team_mode`` wins.
    - Otherwise: non-avatar predefined members ⇒ ``hybrid`` (spawn kept);
      empty / avatar-only roster ⇒ ``default`` (spawn kept).
    - ``predefined`` must be set explicitly; it strips every spawn_* tool.
    """
    if spec.team_mode is not None:
        return spec.team_mode
    # HUMAN_AGENT predefined members are HITT roster declarations, and
    # BRIDGE_AGENT entries are bridge-to-remote declarations — neither
    # is a signal to flip the team away from "default". A roster of
    # ordinary predefined teammates derives "hybrid": the leader keeps
    # its spawn_* tools so the roster can still grow at runtime.
    # Lock it down by setting an explicit "predefined" team_mode.
    avatar_roles = {TeamRole.HUMAN_AGENT, TeamRole.BRIDGE_AGENT}
    non_avatar_predefined = [m for m in spec.predefined_members if m.role_type not in avatar_roles]
    return "hybrid" if non_avatar_predefined else "default"


class _SwarmflowModelResolver:
    """Resolves ``agent(model=...)`` hints against the live team pool.

    Callable (same contract as the historical closure) so it flows unchanged
    through inject_team_handles → rails → tool_factory → runner → backend.
    ``pool_names`` exposes accepted names so the engine can fail fast on an
    unknown hint before the start event is emitted.
    """

    def __init__(self, team_spec: Any) -> None:
        self._spec = team_spec

    def __call__(self, model_name: str) -> Any:
        """Resolve a ``model`` hint to a worker ``TeamModelConfig``.

        Returns a model *config* (not a built ``Model``): swarmflow workers
        go through the spec build path, where ``DeepAgentSpec.model`` is a
        ``TeamModelConfig`` resolved at construction. ``None`` (no hint)
        falls back to the worker base spec's own model.

        An explicit name that does not resolve RAISES instead of falling
        back: a typo'd model would otherwise silently run on the default
        model while the UI keeps showing the requested name. The engine's
        pre-flight check rejects unknown hints before any side effect;
        this raise backs the non-engine callers (CLI MCP tool set, tool
        gateway) and the shrunk-pool window behind that check.
        """
        resolved = None
        if self._spec is not None:
            from openjiuwen.agent_teams.models.allocator import resolve_member_model

            resolved = resolve_member_model(self._spec, model_name=model_name, model_index=None)
        if resolved is None and model_name:
            raise ValueError(
                f"swarmflow model {model_name!r} not found in the team model pool; "
                "an explicitly requested model never falls back to the default"
            )
        return resolved

    def pool_names(self) -> list[str]:
        """Accepted ``model`` hint names (live read; pool refresh stays visible)."""
        if self._spec is None:
            return []
        return [e.model_name for e in (self._spec.model_pool or [])]


def _derive_swarmflow_worker_base_spec(
    spec: TeamAgentSpec,
    observability_rail_specs: list[Any],
) -> Any:
    """Worker base = teammate spec (leader fallback) + observability rails.

    Workers are "a teammate without team tools": the raw agents[...] spec
    carries teammate capabilities but not the team rails (those are injected
    per member), so a worker built from it has no team tools by construction.
    ``progressive_tool`` is only written when the team opts in, so a team that
    leaves it off keeps the base spec exactly as configured.
    """
    base = spec.agents.get("teammate") or spec.agents.get("leader")
    if base is None:
        return None
    update: dict[str, Any] = {"rails": list(base.rails or []) + observability_rail_specs}
    if getattr(spec, "progressive_tool", None):
        from openjiuwen.agent_teams.schema.deep_agent_spec import ProgressiveToolSpec

        update["progressive_tool"] = ProgressiveToolSpec(enabled=True)
    return base.model_copy(update=update)


def _derive_swarmflow_external_cli_agents(spec: TeamAgentSpec) -> tuple[Any, ...]:
    """The swarmflow third-party allowlist lives on the blueprint spec only —
    the runtime ``TeamSpec`` never carries ``external_cli_agents``."""
    return tuple(getattr(spec, "external_cli_agents", ()) or ())


def _spawn_tools_excluded_for_mode(team_mode: str) -> list[str]:
    """Return spawn tool names stripped from the leader for ``team_mode``.

    ``default`` / ``hybrid`` keep spawn (dynamic / seed+增援). Only
    ``predefined`` locks the roster and excludes every spawn_* tool.
    """
    if team_mode == "predefined":
        return list(_SPAWN_TOOLS)
    return []


def _apply_team_worktree_shell_guard(rails: list[Any], *, enabled: bool) -> list[Any]:
    """Merge team-managed worktree shell guards into core.sys_operation rails."""
    if not enabled:
        return rails
    from openjiuwen.agent_teams.rails.builtin_elements import SYS_OPERATION

    guarded: list[Any] = []
    found_sys_operation = False
    for rail in rails:
        if getattr(rail, "type", None) != SYS_OPERATION:
            guarded.append(rail)
            continue
        found_sys_operation = True
        params = dict(getattr(rail, "params", None) or {})
        deny_patterns = list(params.get("bash_deny_patterns") or [])
        for pattern in _TEAM_WORKTREE_BASH_DENY_PATTERNS:
            if pattern not in deny_patterns:
                deny_patterns.append(pattern)
        params["bash_deny_patterns"] = deny_patterns
        guarded.append(rail.model_copy(update={"params": params}))
    if not found_sys_operation:
        guarded.append(
            RailSpec(
                type=SYS_OPERATION,
                params={"bash_deny_patterns": list(_TEAM_WORKTREE_BASH_DENY_PATTERNS)},
            )
        )
    return guarded


def _has_team_worktree_shell_guard(rails: list[Any]) -> bool:
    from openjiuwen.agent_teams.rails.builtin_elements import SYS_OPERATION

    for rail in rails:
        if getattr(rail, "type", None) != SYS_OPERATION:
            continue
        params = getattr(rail, "params", None) or {}
        deny_patterns = set(params.get("bash_deny_patterns") or [])
        if all(pattern in deny_patterns for pattern in _TEAM_WORKTREE_BASH_DENY_PATTERNS):
            return True
    return False


class AgentConfigurator:
    """Handles agent configuration, setup, and initialization.

    Responsibilities:
    - Spec and context management
    - Workspace and worktree setup
    - Tool registration
    - Model allocation
    - DeepAgent construction
    """

    def __init__(self, card: AgentCard):
        self._card = card
        self._blueprint: Optional[TeamAgentBlueprint] = None
        self._spawn_payload_builder: Optional[SpawnPayloadBuilder] = None
        self._infra = TeamInfra()
        self._resources = PrivateAgentResources()
        self.leader_allocation: Optional[Allocation] = None
        self._on_teammate_created: Optional[Any] = None

    # ------------------------------------------------------------------
    # Field forwarding to TeamInfra / PrivateAgentResources
    # ------------------------------------------------------------------

    @property
    def infra(self) -> TeamInfra:
        """Return the per-process infrastructure container."""
        return self._infra

    @property
    def resources(self) -> PrivateAgentResources:
        """Return the per-instance runtime resources container."""
        return self._resources

    @property
    def messager(self) -> Optional[Messager]:
        return self._infra.messager

    @messager.setter
    def messager(self, value: Optional[Messager]) -> None:
        self._infra.messager = value

    @property
    def team_backend(self) -> Optional[TeamBackend]:
        return self._infra.team_backend

    @team_backend.setter
    def team_backend(self, value: Optional[TeamBackend]) -> None:
        self._infra.team_backend = value

    @property
    def workspace_manager(self) -> Optional["TeamWorkspaceManager"]:
        return self._infra.workspace_manager

    @workspace_manager.setter
    def workspace_manager(self, value: Optional["TeamWorkspaceManager"]) -> None:
        self._infra.workspace_manager = value

    @property
    def workspace_initialized(self) -> bool:
        return self._infra.workspace_initialized

    @workspace_initialized.setter
    def workspace_initialized(self, value: bool) -> None:
        self._infra.workspace_initialized = value

    @property
    def swarmflow(self) -> Optional["SwarmflowInfra"]:
        return self._infra.swarmflow

    @swarmflow.setter
    def swarmflow(self, value: Optional["SwarmflowInfra"]) -> None:
        self._infra.swarmflow = value

    @property
    def task_manager(self) -> Any:
        return self._infra.task_manager

    @task_manager.setter
    def task_manager(self, value: Any) -> None:
        self._infra.task_manager = value

    @property
    def message_manager(self) -> Any:
        return self._infra.message_manager

    @message_manager.setter
    def message_manager(self, value: Any) -> None:
        self._infra.message_manager = value

    @property
    def harness(self) -> Optional["MemberRuntime"]:
        return self._resources.harness

    @harness.setter
    def harness(self, value: Optional["MemberRuntime"]) -> None:
        self._resources.harness = value

    @property
    def worktree_manager(self) -> Optional["WorktreeManager"]:
        return self._resources.worktree_manager

    @worktree_manager.setter
    def worktree_manager(self, value: Optional["WorktreeManager"]) -> None:
        self._resources.worktree_manager = value

    @property
    def memory_manager(self) -> Optional["TeamMemoryManager"]:
        return self._resources.memory_manager

    @memory_manager.setter
    def memory_manager(self, value: Optional["TeamMemoryManager"]) -> None:
        self._resources.memory_manager = value

    @property
    def model_allocator(self) -> Optional["ModelAllocator"]:
        return self._resources.model_allocator

    @model_allocator.setter
    def model_allocator(self, value: Optional["ModelAllocator"]) -> None:
        self._resources.model_allocator = value

    def configure(
        self,
        spec: TeamAgentSpec,
        ctx: TeamRuntimeContext,
        *,
        member_runtime: Optional["MemberRuntime"] = None,
    ) -> "MemberRuntime":
        """Main entry point: configure infrastructure and build the runtime."""
        self.setup_infra(spec, ctx)
        return self.setup_agent(spec, ctx, member_runtime=member_runtime)

    def setup_infra(
        self,
        spec: TeamAgentSpec,
        ctx: TeamRuntimeContext,
        *,
        on_teammate_created=None,
        on_before_team_cleaned=None,
        on_team_cleaned=None,
        on_team_built=None,
    ) -> None:
        """Phase 1: set spec/context, create messager, workspace manager, prepare team backend."""
        agent_spec = self.resolve_agent_spec(spec, ctx.role, ctx.member_name)
        resolved_language = _resolve_language(agent_spec.language)
        self._blueprint = TeamAgentBlueprint(
            card=self._card,
            spec=spec,
            ctx=ctx,
            language=resolved_language,
        )
        self._spawn_payload_builder = SpawnPayloadBuilder(spec, ctx)
        self._on_teammate_created = on_teammate_created

        messager_config = ctx.messager_config
        member_name = ctx.member_name
        if member_name and messager_config and messager_config.node_id != member_name:
            messager_config = messager_config.model_copy(update={"node_id": member_name})

        self.messager = create_messager(messager_config) if messager_config else None

        if spec.workspace and spec.workspace.enabled:
            self.workspace_manager = self.create_workspace_manager(spec, ctx)

        if ctx.role == TeamRole.LEADER and self.model_allocator is None:
            from openjiuwen.agent_teams.models.allocator import (
                build_model_allocator,
            )

            self.model_allocator = build_model_allocator(spec, ctx.team_spec)

        if ctx.role == TeamRole.LEADER:
            kv_cache_hooks.ensure_leader_registry(self)

        self.setup_team_backend(
            spec,
            ctx,
            self.messager,
            on_before_team_cleaned=on_before_team_cleaned,
            on_team_cleaned=on_team_cleaned,
            on_team_built=on_team_built,
        )
        # Route backend cache reads to the workspace manager (single source
        # of truth). Cache object itself may still be None until an evolution
        # assembly path attaches one; consumers tolerate None.
        if self.team_backend is not None and self.workspace_manager is not None:
            self.team_backend.attach_workspace_manager(self.workspace_manager)

        if ctx.role == TeamRole.LEADER and spec.worktree and spec.worktree.enabled:
            self.worktree_manager = self.create_worktree_manager(spec)

        # Tiny-agent model resolver: map a model_name to a TeamModelConfig against
        # the team pool (None when no pool / unknown name). Stored on infra so
        # team-scoped tiny agents and ephemeral callers resolve a model name
        # identically. See openjiuwen.agent_teams.tiny_agent.
        if ctx.team_spec is not None:
            from openjiuwen.agent_teams.models.allocator import resolve_member_model

            def _tiny_model_resolver(model_name: str, _team_spec=ctx.team_spec) -> Any:
                return resolve_member_model(_team_spec, model_name=model_name, model_index=None)

            self._infra.tiny_agent_model_resolver = _tiny_model_resolver

    @staticmethod
    def create_workspace_manager(
        spec: TeamAgentSpec,
        ctx: TeamRuntimeContext,
    ) -> TeamWorkspaceManager:
        from openjiuwen.agent_teams.team_workspace.manager import TeamWorkspaceManager

        ws_config = spec.workspace
        team_name = (ctx.team_spec.team_name if ctx.team_spec else None) or spec.team_name
        ws_path = ws_config.root_path or str(team_home(team_name) / "team-workspace")
        os.makedirs(ws_path, exist_ok=True)
        team_logger.info("Team workspace directory ensured at {}", ws_path)
        return TeamWorkspaceManager(
            config=ws_config,
            workspace_path=ws_path,
            team_name=team_name,
        )

    def create_worktree_manager(self, spec: TeamAgentSpec) -> WorktreeManager:
        from openjiuwen.harness.tools.worktree import (
            WorktreeManager,
        )
        from openjiuwen.harness.tools.worktree import WorktreeCreatedEvent as HarnessWorktreeCreatedEvent
        from openjiuwen.harness.tools.worktree import WorktreeRemovedEvent as HarnessWorktreeRemovedEvent

        ws_mgr = self.workspace_manager

        event_handler = None
        if ws_mgr is not None or self.messager is not None:

            async def _mirror_worktree_into_workspace(event: Any) -> None:
                """Keep ``.worktree/{slug}`` in lockstep with manager events.

                Translates the generic worktree lifecycle stream into team
                workspace mount/unmount calls. Single-agent callers never
                install this handler, so the symlink view is team-only by
                construction.
                """
                if ws_mgr is not None:
                    if isinstance(event, HarnessWorktreeCreatedEvent):
                        ws_mgr.mount_worktree(event.worktree_name, event.worktree_path)
                    elif isinstance(event, HarnessWorktreeRemovedEvent):
                        ws_mgr.unmount_worktree(event.worktree_name)

                if self.messager is not None and self.team_backend is not None:
                    from openjiuwen.agent_teams.context import get_session_id
                    from openjiuwen.agent_teams.schema.events import TeamTopic

                    message = self._build_team_worktree_event_message(event)
                    if message is None:
                        return
                    try:
                        await self.messager.publish(
                            topic_id=TeamTopic.TEAM.build(get_session_id(), self.team_backend.team_name),
                            message=message,
                        )
                    except Exception as e:
                        team_logger.warning("Failed to publish worktree event: {}", e)

            event_handler = _mirror_worktree_into_workspace

        return WorktreeManager(
            config=spec.worktree,
            event_handler=event_handler,
        )

    @staticmethod
    def _build_team_worktree_event_message(event: Any) -> Any:
        """Translate generic harness worktree events into team bus messages."""
        from openjiuwen.agent_teams.schema.events import (
            EventMessage,
            WorktreeCreatedEvent,
            WorktreeRemovedEvent,
        )
        from openjiuwen.harness.tools.worktree import WorktreeCreatedEvent as HarnessWorktreeCreatedEvent
        from openjiuwen.harness.tools.worktree import WorktreeRemovedEvent as HarnessWorktreeRemovedEvent

        if isinstance(event, HarnessWorktreeCreatedEvent):
            return EventMessage.from_event(
                WorktreeCreatedEvent(
                    worktree_name=event.worktree_name,
                    worktree_path=event.worktree_path,
                    existed=event.existed,
                )
            )
        if isinstance(event, HarnessWorktreeRemovedEvent):
            return EventMessage.from_event(
                WorktreeRemovedEvent(
                    worktree_name=event.worktree_name,
                    worktree_path=event.worktree_path,
                )
            )
        return None

    def setup_agent(
        self,
        spec: TeamAgentSpec,
        ctx: TeamRuntimeContext,
        *,
        member_runtime: Optional["MemberRuntime"] = None,
    ) -> "MemberRuntime":
        """Phase 2: build the member runtime and set up coordination.

        The default path builds a ``TeamHarness`` over DeepAgent. When
        ``member_runtime`` is supplied (e.g. an ``ExternalCliRuntime`` for an
        external CLI member), it is adopted as-is and the DeepAgent / rail /
        memory setup is skipped — coordination still drives it
        through the same :class:`MemberRuntime` surface.
        """
        if member_runtime is not None:
            self.harness = member_runtime
            self.memory_manager = None
            return member_runtime

        agent_spec = self.resolve_agent_spec(spec, ctx.role, ctx.member_name)
        resolved_language = self._blueprint.language if self._blueprint else _resolve_language(agent_spec.language)
        member_name = ctx.member_name

        ws_spec = agent_spec.workspace or spec.agents.get("leader", agent_spec).workspace
        if ws_spec is None:
            # A team member always owns a workspace -- it is where its
            # artifacts, memory and the .team mount live, and DeepAgent keys
            # its cwd initialisation off it.
            ws_spec = WorkspaceSpec(stable_base=True)
        if ws_spec.stable_base:
            team_name = (ctx.team_spec.team_name if ctx.team_spec else None) or spec.team_name
            ws_spec = ws_spec.model_copy(
                update={"root_path": ensure_team_member_workspace_link(team_name, member_name)}
            )

        # cwd is a separate layer from the workspace. The workspace stays the
        # member's private artifact directory (memory, Skill visibility
        # declaration); cwd is where shell runs and relative paths resolve. Team
        # isolation moves cwd into the worktree without dragging the workspace
        # along -- otherwise the member's artifacts and its Skill grants would
        # live inside an ephemeral checkout and vanish with it.
        member_cwd = ctx.worktree_path or agent_spec.cwd or None
        member_project_root = agent_spec.project_root or agent_spec.cwd or None

        workspace_root_path = ws_spec.root_path if ws_spec is not None else None
        # The workspace is now always the member's own directory (never the
        # project dir, never a worktree), so it is unconditionally ours to
        # clean up.
        if workspace_root_path and self.team_backend is not None:
            self.team_backend.register_cleanup_path(workspace_root_path)

        if self.workspace_manager and ws_spec and ws_spec.root_path:
            self.workspace_manager.mount_into_workspace(ws_spec.root_path)

        model_config = ctx.member_model or agent_spec.model

        sys_operation_spec = agent_spec.sys_operation or SysOperationSpec(
            id=f"{self._card.id}.sys_operation",
            mode=OperationMode.LOCAL,
            work_config=LocalWorkConfig(shell_allowlist=None),
        )
        # Members default to metadata-only read_file images: the modality probe
        # costs a full LLM round-trip per member on every team start. A blueprint
        # that wants native image input says so explicitly on the agent spec.
        enable_read_image_multimodal = agent_spec.enable_read_image_multimodal
        if enable_read_image_multimodal is None:
            enable_read_image_multimodal = False
        # Keep ``skills`` / ``enable_skill_discovery`` for now. Clearing them
        # is deferred until ``core.team.skill_use`` is confirmed mounted
        # (see below): otherwise a gated-out team Skill rail would leave the
        # member with neither the generic SkillUseRail nor the team rail.
        # ``agent_spec.skills`` still seeds the team rail's bootstrap allow-list.
        build_spec = agent_spec.model_copy(
            update={
                "card": self._card,
                "model": model_config,
                "workspace": ws_spec,
                "cwd": member_cwd,
                "project_root": member_project_root,
                "sys_operation": sys_operation_spec,
                "tools": list(agent_spec.tools or []),
                "enable_task_loop": True,
                "enable_read_image_multimodal": enable_read_image_multimodal,
            }
        )

        resolved_team_name = (ctx.team_spec.team_name if ctx.team_spec else None) or spec.team_name
        teammate_mode = str(spec.teammate_mode)

        team_workspace_mount: str | None = None
        team_workspace_path: str | None = None
        if self.workspace_manager:
            team_workspace_mount = ".team/"
            team_workspace_path = self.workspace_manager.workspace_path

        # Decide which team rails this member gets, as declarative RailSpecs.
        # Live handles ride on the build context's extras (injected below); only
        # serializable static config goes in params. Decisions that depend on
        # team config (predefined roster, plan-mode, reliability gating) stay
        # here; "can it build" gates (a missing handle) live in the factories.
        from openjiuwen.agent_teams.rails.elements import (
            OBSERVABILITY,
            TEAM_OBSERVABILITY,
            TEAM_DEBATE_ROUND_CAP,
            TEAM_PLAN_MODE,
            TEAM_POLICY,
            TEAM_RELIABILITY,
            TEAM_SESSION_BUDGET,
            TEAM_TOOL,
            TEAM_TOOL_APPROVAL,
            TEAM_WORKSPACE,
        )
        from openjiuwen.agent_teams.rails.registration import (
            ensure_harness_elements_registered,
        )
        from openjiuwen.agent_teams.rails.team_context import inject_team_handles
        from openjiuwen.agent_teams.schema.build_context import BuildContext

        ensure_harness_elements_registered()

        # Observability rail specs — shared by all agents (members + swarmflow
        # workers). Two rails, always mounted as a pair: ``core.observability``
        # owns the agent span itself (harness-level, team-agnostic) and
        # ``core.team.observability`` layers the team identity onto it. Each
        # provider checks is_initialized() — no-op when disabled.
        observability_rail_specs = [
            RailSpec(type=TEAM_OBSERVABILITY),
            RailSpec(type=OBSERVABILITY),
        ]

        # Predefined teams pin their roster — strip every dynamic spawn tool
        # (one per role_type) from the leader's tool set. default/hybrid keep spawn.
        exclude = _spawn_tools_excluded_for_mode(_resolve_team_mode(spec))

        team_rail_specs: list[RailSpec] = [
            RailSpec(
                type=TEAM_TOOL,
                params={
                    "teammate_mode": teammate_mode,
                    "dispatch_mode": spec.dispatch_mode,
                    "lifecycle": spec.lifecycle,
                    "exclude_tools": exclude,
                    "qualify_ids": spec.spawn_mode == "inprocess",
                    "team_name": resolved_team_name,
                    "team_permissions_enabled": spec.enable_permissions,
                },
            ),
            RailSpec(
                type=TEAM_POLICY,
                params={
                    "prompt": ctx.prompt or "",
                    "display_name": ctx.display_name or "",
                    "member_workspace_path": workspace_root_path,
                    "lifecycle": spec.lifecycle,
                    "teammate_mode": teammate_mode,
                    "team_mode": _resolve_team_mode(spec),
                    "dispatch_mode": spec.dispatch_mode,
                    "base_prompt": agent_spec.system_prompt,
                    "team_workspace_mount": team_workspace_mount,
                    "team_workspace_path": team_workspace_path,
                    "expose_human_agents_to_teammates": spec.expose_human_agents_to_teammates,
                },
            ),
        ]
        if self.workspace_manager:
            team_rail_specs.append(RailSpec(type=TEAM_WORKSPACE, params={}))

        is_coordinated_teammate = ctx.role == TeamRole.TEAMMATE and ctx.team_spec
        approval_tools = agent_spec.approval_required_tools or []
        can_request_approval = is_coordinated_teammate and self.team_backend and self.messager
        # When team permissions are enabled the platform-mounted
        # TeamPermissionRail replaces TeamToolApprovalRail.
        if can_request_approval and approval_tools and not spec.enable_permissions:
            team_rail_specs.append(
                RailSpec(
                    type=TEAM_TOOL_APPROVAL,
                    params={
                        "team_name": ctx.team_spec.team_name,
                        "leader_member_name": ctx.team_spec.leader_member_name or "",
                        "tool_names": list(approval_tools),
                    },
                ),
            )

        is_team_plan_leader = ctx.role == TeamRole.LEADER and is_team_plan_enabled(spec)
        if is_team_plan_leader:
            team_rail_specs.append(RailSpec(type=TEAM_PLAN_MODE, params={}))

        if (
            spec.dispatch_mode == "autonomous"
            and ctx.role in {TeamRole.LEADER, TeamRole.TEAMMATE}
            and spec.max_debate_rounds is not None
        ):
            team_rail_specs.append(
                RailSpec(
                    type=TEAM_DEBATE_ROUND_CAP,
                    params={
                        "max_debate_rounds": spec.max_debate_rounds,
                        "team_name": resolved_team_name,
                    },
                ),
            )
            team_logger.info(
                "[config] {} debate cap enabled: max_debate_rounds={}",
                member_name or ctx.role.value,
                spec.max_debate_rounds,
            )
        else:
            team_logger.info(
                "[config] {} debate cap skipped (autonomous={}, role={}, max_debate_rounds={})",
                member_name or ctx.role.value,
                spec.dispatch_mode == "autonomous",
                ctx.role.value,
                spec.max_debate_rounds,
            )

        # session-level budget rail. Autonomous teams whose
        # chat never matches the debate pattern (member→leader reports,
        # leader broadcasts) escape the per-teammate debate cap entirely;
        # this rail bounds the whole session instead — rounds / messages /
        # input tokens, no leader/report exemptions. Injected whenever any
        # ceiling is configured (opt-in; jiuwenswarm passes template values
        # through without defaults).
        session_ceilings = {
            "max_total_rounds": spec.max_total_rounds,
            "max_total_messages": spec.max_total_messages,
            "token_budget": spec.token_budget,
        }
        if (
            spec.dispatch_mode == "autonomous"
            and ctx.role in {TeamRole.LEADER, TeamRole.TEAMMATE}
            and any(value is not None for value in session_ceilings.values())
        ):
            team_rail_specs.append(
                RailSpec(
                    type=TEAM_SESSION_BUDGET,
                    params=dict(session_ceilings),
                ),
            )
            team_logger.info(
                "[config] {} session budget enabled: max_total_rounds={}, "
                "max_total_messages={}, token_budget={}",
                member_name or ctx.role.value,
                spec.max_total_rounds,
                spec.max_total_messages,
                spec.token_budget,
            )
        else:
            configured_ceilings = [
                name for name, value in session_ceilings.items() if value is not None
            ]
            team_logger.info(
                "[config] {} session budget skipped (autonomous={}, role={}, "
                "ceilings configured: {})",
                member_name or ctx.role.value,
                spec.dispatch_mode == "autonomous",
                ctx.role.value,
                configured_ceilings or "none",
            )

        reliability_cfg = spec.reliability
        reliability_components = None
        if reliability_cfg and reliability_cfg.enabled and member_name:
            role_value = ctx.role.value
            is_leader = role_value == "leader"
            # Leader uses a LocalAnomalyReporter (no messager needed); teammates
            # need one for the cross-process EventAnomalyReporter.
            if role_value in reliability_cfg.monitor_roles and (is_leader or self.messager):
                from openjiuwen.agent_teams.reliability.factory import (
                    build_reliability_components,
                )

                # Build the stateful core once here (single writer of extras) and
                # inject it below; each native rebuild wraps it in a fresh rail so
                # detector windows / the leader sink survive across run cycles.
                reliability_components = build_reliability_components(
                    reliability_cfg,
                    member_name=member_name,
                    messager=self.messager,
                    team_name=resolved_team_name,
                    sender_id=member_name,
                    is_leader=is_leader,
                )
                team_rail_specs.append(
                    RailSpec(
                        type=TEAM_RELIABILITY,
                        params={
                            "reliability_cfg": reliability_cfg.model_dump(),
                            "team_name": resolved_team_name,
                            "sender_id": member_name,
                            "is_leader": is_leader,
                        },
                    ),
                )

        # Build the per-member context that carries the team live handles in
        # extras. With a platform-supplied build_context, derive a per-member
        # view and decouple its extras (so members never share handles /
        # caches); otherwise synthesize a minimal context.
        # Workspace root controls where the agent runs; project_dir controls
        # project-scoped providers such as LSP and prompt workspace context.
        # Keep both on the same worktree to avoid a teammate running in the
        # isolated checkout while project-aware capabilities still point at the
        # original repository.
        member_project_dir = ctx.worktree_path if ctx.worktree_path else None
        if spec.build_context is not None:
            context_overrides: dict[str, Any] = {
                "member_name": member_name,
                "role": ctx.role.value,
                "language": resolved_language,
                "member_card_id": self._card.id,
            }
            if member_project_dir:
                context_overrides["project_dir"] = member_project_dir
            member_build_context = spec.build_context.derive(
                **context_overrides,
            )
            member_build_context.extras = dict(member_build_context.extras)
        else:
            member_build_context = BuildContext(
                language=resolved_language,
                member_name=member_name,
                role=ctx.role.value,
                member_card_id=self._card.id,
                project_dir=member_project_dir,
            )
        # Swarmflow shared infrastructure (worker-model resolver / worker +
        # human base specs / concurrency governor / budget ledger /
        # external-CLI allowlist / expert table). Team-level singleton hosted
        # on the leader's TeamInfra: the leader constructs once (gate:
        # role + ``enable_swarmflow``); in-process teammates read the
        # instance injected by ``share_swarmflow_with`` before configure.
        swarmflow_model_resolver: Optional[Callable[[str], Any]] = None
        swarmflow_worker_base_spec = None
        swarmflow_human_base_spec = None
        swarmflow_concurrency_governor = None
        swarmflow_budget = None
        swarmflow_external_cli_agents: tuple[Any, ...] = ()
        swarmflow_expert_specs: dict[str, Any] = {}
        infra = self.swarmflow
        if infra is None and ctx.role == TeamRole.LEADER and spec.enable_swarmflow:
            from openjiuwen.agent_teams.workflow.swarmflow_infra import build_swarmflow_infra

            infra = build_swarmflow_infra(spec, ctx, observability_rail_specs)
            self.swarmflow = infra
        if infra is not None:
            swarmflow_model_resolver = infra.model_resolver
            swarmflow_worker_base_spec = infra.worker_base_spec
            swarmflow_human_base_spec = infra.human_base_spec
            swarmflow_concurrency_governor = infra.concurrency_governor
            swarmflow_budget = infra.budget
            swarmflow_external_cli_agents = infra.external_cli_agents
            swarmflow_expert_specs = infra.expert_specs

        inject_team_handles(
            member_build_context.extras,
            team_backend=self.team_backend,
            workspace_manager=self.workspace_manager,
            model_allocator=self.model_allocator,
            messager=self.messager,
            on_teammate_created=self._on_teammate_created,
            swarmflow_model_resolver=swarmflow_model_resolver,
            swarmflow_worker_base_spec=swarmflow_worker_base_spec,
            swarmflow_human_base_spec=swarmflow_human_base_spec,
            swarmflow_concurrency_governor=swarmflow_concurrency_governor,
            swarmflow_budget=swarmflow_budget,
            swarmflow_external_cli_agents=swarmflow_external_cli_agents,
            swarmflow_expert_specs=swarmflow_expert_specs,
            reliability_components=reliability_components,
            permissions_override=ctx.permissions_override,
            worktree_manager=self.worktree_manager,
        )

        # Fold the team rails into the spec rails (after the user rails, to keep
        # the init order consistent with the legacy mount order).
        team_rail_specs.extend(observability_rail_specs)
        base_rails = _apply_team_worktree_shell_guard(
            list(build_spec.rails or []),
            enabled=ctx.role in {TeamRole.LEADER, TeamRole.TEAMMATE},
        )
        if ctx.role in {TeamRole.LEADER, TeamRole.TEAMMATE} and not _has_team_worktree_shell_guard(base_rails):
            team_logger.warning(
                "Team-managed worktree shell guard was not applied for member {} because core.sys_operation is absent",
                member_name,
            )
        # Declared last, once base_rails is final: complete any bare
        # ``core.team.skill_use`` from the blueprint, then auto-declare one when
        # the blueprint did not. ``agent_spec.skills`` seeds the allow-list.
        from openjiuwen.agent_teams.skill.rail_spec import (
            build_team_skill_rail_spec,
            complete_declared_team_skill_rails,
        )

        member_workspace_path = ws_spec.root_path if ws_spec is not None else None
        base_rails = complete_declared_team_skill_rails(
            base_rails,
            team_name=resolved_team_name,
            member_name=member_name,
            config_skills=agent_spec.skills,
            team_workspace_path=team_workspace_path,
            member_workspace_path=member_workspace_path,
        )
        skill_rail_spec = build_team_skill_rail_spec(
            team_name=resolved_team_name,
            member_name=member_name,
            config_skills=agent_spec.skills,
            declared_rails=base_rails + team_rail_specs,
            team_workspace_path=team_workspace_path,
            member_workspace_path=member_workspace_path,
        )
        if skill_rail_spec is not None:
            team_rail_specs.append(skill_rail_spec)

        from openjiuwen.agent_teams.rails.elements import TEAM_SKILL_USE

        merged_rails = base_rails + team_rail_specs
        has_team_skill_rail = any(
            getattr(rail, "type", None) == TEAM_SKILL_USE for rail in merged_rails
        )
        # Only suppress the factory SkillUseRail when the team Skill rail will
        # actually be present; otherwise keep agents.<role>.skills / discovery.
        rail_update: dict[str, Any] = {"rails": merged_rails}
        if has_team_skill_rail:
            rail_update["skills"] = []
            rail_update["enable_skill_discovery"] = False
        build_spec = build_spec.model_copy(update=rail_update)

        self.harness = TeamHarness.build(
            agent_spec=build_spec,
            role=ctx.role,
            member_name=member_name,
            initial_plan_mode=is_team_plan_leader,
            build_context=member_build_context,
        )

        # A member's own model calls bill the session budget — NOT the per-run
        # budget. Every member whose swarmflow handles are wired carries this
        # rail (leader or in-process teammate that received the share).
        if swarmflow_budget is not None:
            from openjiuwen.agent_teams.workflow.backends.budget_rail import SwarmflowBudgetRail

            self.harness.add_rail(SwarmflowBudgetRail(swarmflow_budget, workflow_budget=None))

        # Team memory manager (only when explicitly enabled in the spec).
        self.memory_manager = self._build_memory_manager(spec, ctx, agent_spec, resolved_language, member_name)

        return self.harness

    def _build_memory_manager(
        self,
        spec: TeamAgentSpec,
        ctx: TeamRuntimeContext,
        agent_spec: Any,
        resolved_language: str,
        member_name: Optional[str],
    ) -> Optional[TeamMemoryManager]:
        if not (spec.memory and spec.memory.enabled):
            return None

        from openjiuwen.agent_teams.memory.config import resolve_embedding_config
        from openjiuwen.agent_teams.memory.manager import TeamMemoryManager
        from openjiuwen.agent_teams.memory.manager_params import TeamMemoryManagerParams

        resolved_team_name = (ctx.team_spec.team_name if ctx.team_spec else None) or spec.team_name
        resolved_embedding = resolve_embedding_config(spec.memory)

        # Temporary lifecycle: read-only source points to the parent
        # agent's workspace so the team can inherit prior memories without
        # mutating them.
        read_only_source = spec.memory.parent_workspace_path if spec.lifecycle == "temporary" else None

        # Persistent lifecycle: pick the explicit team_memory_dir if set,
        # otherwise fall back to the standard layout under team_home.
        team_memory_dir = None
        if spec.memory.shared_memory and spec.lifecycle == "persistent":
            team_memory_dir = spec.memory.team_memory_dir or str(default_team_memory_dir(resolved_team_name))

        agent_workspace = self.harness.workspace if self.harness else None
        sys_operation = self.harness.sys_operation if self.harness else None

        params = TeamMemoryManagerParams(
            member_name=member_name or "",
            team_name=resolved_team_name,
            role=ctx.role.value,
            lifecycle=spec.lifecycle,
            scenario=spec.memory.scenario,
            embedding_config=resolved_embedding,
            workspace=agent_workspace,
            sys_operation=sys_operation,
            team_memory_dir=team_memory_dir,
            language=resolved_language,
            prompt_mode=spec.memory.member_memory_prompt_mode,
            enable_auto_extract=(spec.memory.auto_extract and spec.lifecycle == "persistent"),
            read_only_source_workspace=read_only_source,
            db=self.team_backend.db if self.team_backend else None,
            task_manager=self.task_manager,
            extraction_model=None,
            timezone_offset_hours=spec.memory.timezone_offset_hours,
        )
        return TeamMemoryManager(params)

    @staticmethod
    def resolve_agent_spec(
        spec: TeamAgentSpec,
        role: TeamRole,
        member_name: Optional[str] = None,
    ):
        if member_name and member_name in spec.agents:
            return spec.agents[member_name]
        return spec.agents.get(role.value) or spec.agents.get("teammate") or spec.agents["leader"]

    def setup_team_backend(
        self,
        spec: TeamAgentSpec,
        ctx: TeamRuntimeContext,
        messager: Messager,
        *,
        on_before_team_cleaned=None,
        on_team_cleaned=None,
        on_team_built=None,
    ) -> TeamBackend:
        """Construct the TeamBackend and register cleanup paths.

        Tool wiring is done by ``TeamToolRail`` during the agent's lazy
        rail init, so this stage only owns the backend itself plus the
        team / workspace cleanup-path registry.

        Args:
            on_team_cleaned: Optional async callback threaded into the
                ``TeamBackend`` so the hosting ``TeamAgent`` is notified
                on the ``clean_team`` success path. Wired for every role;
                only the leader can ever fire it (``clean_team`` is a
                leader-only tool).
            on_before_team_cleaned: Optional async callback threaded into
                ``TeamBackend`` before the team DB row is deleted. Cleanup
                that needs member metadata should be wired here.
            on_team_built: Optional async callback threaded into the
                ``TeamBackend`` so the hosting ``TeamAgent`` can persist
                DB lifecycle state after ``build_team`` succeeds.
        """
        from openjiuwen.agent_teams.schema.status import MemberMode
        from openjiuwen.agent_teams.spawn.shared_resources import get_shared_db

        team_name = (ctx.team_spec.team_name if ctx.team_spec else None) or "default"
        db = get_shared_db(ctx.db_config)

        is_leader = ctx.role == TeamRole.LEADER
        current_member_name = ctx.member_name or (ctx.team_spec.leader_member_name if ctx.team_spec else "")
        agent_team = TeamBackend(
            team_name=team_name,
            member_name=current_member_name,
            is_leader=is_leader,
            db=db,
            messager=messager,
            teammate_mode=MemberMode(str(spec.teammate_mode)),
            predefined_members=spec.predefined_members or None,
            model_config_allocator=self.model_allocator.allocate if self.model_allocator else None,
            leader_allocation=self.leader_allocation if is_leader else None,
            leader_prompt=ctx.prompt if is_leader else "",
            enable_hitt=spec.enable_hitt,
            enable_bridge=spec.enable_bridge,
            dispatch_mode=spec.dispatch_mode,
            enable_task_verification=spec.enable_task_verification,
            external_cli_agents=spec.external_cli_agents,
            on_before_team_cleaned=on_before_team_cleaned,
            on_team_cleaned=on_team_cleaned,
            on_team_built=on_team_built,
            leader_member_name=ctx.team_spec.leader_member_name if ctx.team_spec else None,
        )
        self.team_backend = agent_team
        self.task_manager = agent_team.task_manager
        self.message_manager = agent_team.message_manager

        if self.workspace_manager:
            agent_team.register_cleanup_path(self.workspace_manager.workspace_path)

        return agent_team

    def update_model_pool(self, new_pool: list) -> None:
        if self.ctx is None or self.ctx.team_spec is None:
            return
        from openjiuwen.agent_teams.models import build_model_allocator, inherit_pool_ids

        merged = inherit_pool_ids(self.ctx.team_spec.model_pool, list(new_pool))
        self.ctx.team_spec.model_pool = merged
        self.model_allocator = build_model_allocator(self.spec, self.ctx.team_spec)

    def attach_model_allocator(
        self,
        allocator: ModelAllocator,
        *,
        leader_allocation: Optional[Allocation] = None,
    ) -> None:
        self.model_allocator = allocator
        self.leader_allocation = leader_allocation

    def restore_allocator_state(self, state: dict) -> None:
        if self.model_allocator is not None:
            self.model_allocator.load_state_dict(state)

    def build_spawn_payload(
        self,
        ctx: TeamRuntimeContext,
        *,
        initial_message: Optional[str] = None,
    ) -> dict[str, Any]:
        return self._spawn_payload_builder.build_spawn_payload(ctx, initial_message=initial_message)

    def build_member_context(self, member_spec: TeamMemberSpec) -> TeamRuntimeContext:
        return self._spawn_payload_builder.build_member_context(member_spec)

    def build_member_messager_config(self, member_name: str):
        return self._spawn_payload_builder.build_member_messager_config(member_name)

    def build_spawn_config(self, ctx: TeamRuntimeContext) -> SpawnAgentConfig:
        return self._spawn_payload_builder.build_spawn_config(ctx)

    @property
    def blueprint(self) -> Optional[TeamAgentBlueprint]:
        """Return the static assembly blueprint, or None before configure()."""
        return self._blueprint

    @property
    def spec(self) -> Optional[TeamAgentSpec]:
        return self._blueprint.spec if self._blueprint else None

    @property
    def ctx(self) -> Optional[TeamRuntimeContext]:
        return self._blueprint.ctx if self._blueprint else None

    @property
    def team_spec(self) -> Optional[TeamSpec]:
        if self.ctx is None:
            return None
        return self.ctx.team_spec

    @property
    def role(self) -> TeamRole:
        if self.ctx is None:
            return TeamRole.LEADER
        return self.ctx.role

    @property
    def lifecycle(self) -> str:
        if self.spec is None:
            return "temporary"
        return self.spec.lifecycle

    @property
    def member_name(self) -> Optional[str]:
        return self.ctx.member_name if self.ctx else None

    @property
    def team_name(self) -> Optional[str]:
        if self.ctx and self.ctx.team_spec:
            return self.ctx.team_spec.team_name
        return None
