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
from openjiuwen.agent_teams.messager import (
    Messager,
    create_messager,
)
from openjiuwen.agent_teams.paths import team_home
from openjiuwen.agent_teams.paths import (
    team_memory_dir as default_team_memory_dir,
)
from openjiuwen.agent_teams.prompts import role_policy
from openjiuwen.agent_teams.runtime.team_plan import is_team_plan_enabled
from openjiuwen.agent_teams.schema.blueprint import TeamAgentSpec
from openjiuwen.agent_teams.schema.deep_agent_spec import SysOperationSpec, WorkspaceSpec
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
    from openjiuwen.harness.tools.worktree import WorktreeManager


def _resolve_team_mode(spec: TeamAgentSpec) -> str:
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
            role_policy=role_policy(ctx.role, language=resolved_language),
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

        self.setup_team_backend(
            spec,
            ctx,
            self.messager,
            on_team_cleaned=on_team_cleaned,
            on_team_built=on_team_built,
        )

        if ctx.role == TeamRole.LEADER and spec.worktree and spec.worktree.enabled:
            self.worktree_manager = self.create_worktree_manager(spec)

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
        workspace_is_worktree = bool(ctx.worktree_path)
        if ctx.worktree_path:
            base_ws_spec = ws_spec or WorkspaceSpec()
            ws_spec = base_ws_spec.model_copy(
                update={
                    "root_path": ctx.worktree_path,
                    "stable_base": False,
                }
            )
        if ws_spec and ws_spec.stable_base:
            team_name = (ctx.team_spec.team_name if ctx.team_spec else None) or spec.team_name
            ws_spec = ws_spec.model_copy(
                update={"root_path": ensure_team_member_workspace_link(team_name, member_name)}
            )

        workspace_root_path = ws_spec.root_path if ws_spec is not None else None
        should_register_cleanup_path = (
            bool(workspace_root_path)
            and self.team_backend is not None
            and not workspace_is_worktree
        )
        if should_register_cleanup_path and workspace_root_path is not None:
            self.team_backend.register_cleanup_path(workspace_root_path)

        if self.workspace_manager and ws_spec and ws_spec.root_path:
            self.workspace_manager.mount_into_workspace(ws_spec.root_path)

        model_config = ctx.member_model or agent_spec.model

        sys_operation_spec = agent_spec.sys_operation or SysOperationSpec(
            id=f"{self._card.id}.sys_operation",
            mode=OperationMode.LOCAL,
            work_config=LocalWorkConfig(shell_allowlist=None),
        )
        build_spec = agent_spec.model_copy(
            update={
                "card": self._card,
                "model": model_config,
                "workspace": ws_spec,
                "sys_operation": sys_operation_spec,
                "tools": list(agent_spec.tools or []),
                "enable_skill_discovery": True,
                "enable_task_loop": True,
            }
        )

        resolved_team_name = (ctx.team_spec.team_name if ctx.team_spec else None) or spec.team_name
        teammate_mode = str(spec.teammate_mode)

        team_workspace_mount: str | None = None
        team_workspace_path: str | None = None
        if self.workspace_manager:
            team_workspace_mount = f".team/{resolved_team_name}/"
            team_workspace_path = self.workspace_manager.workspace_path

        # Decide which team rails this member gets, as declarative RailSpecs.
        # Live handles ride on the build context's extras (injected below); only
        # serializable static config goes in params. Decisions that depend on
        # team config (predefined roster, plan-mode, reliability gating) stay
        # here; "can it build" gates (a missing handle) live in the factories.
        from openjiuwen.agent_teams.rails.elements import (
            TEAM_PLAN_MODE,
            TEAM_POLICY,
            TEAM_RELIABILITY,
            TEAM_TOOL,
            TEAM_TOOL_APPROVAL,
            TEAM_WORKSPACE,
        )
        from openjiuwen.agent_teams.rails.registration import (
            ensure_harness_elements_registered,
        )
        from openjiuwen.agent_teams.rails.team_context import inject_team_handles
        from openjiuwen.agent_teams.schema.build_context import BuildContext
        from openjiuwen.agent_teams.schema.deep_agent_spec import RailSpec

        ensure_harness_elements_registered()

        # Observability rail spec — shared by all agents (members + swarmflow workers).
        # The provider checks is_initialized() — no-op when disabled.
        observability_rail_spec = RailSpec(type="core.observability")

        # Predefined teams pin their roster — strip every dynamic spawn tool
        # (one per role_type) from the leader's tool set.
        exclude = (
            ["spawn_teammate", "spawn_human_agent", "spawn_bridge_agent", "spawn_external_cli"]
            if _resolve_team_mode(spec) == "predefined"
            else []
        )

        team_rail_specs: list[RailSpec] = [
            RailSpec(
                type=TEAM_TOOL,
                params={
                    "teammate_mode": teammate_mode,
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
                    "persona": ctx.persona or "",
                    "lifecycle": spec.lifecycle,
                    "teammate_mode": teammate_mode,
                    "team_mode": _resolve_team_mode(spec),
                    "base_prompt": agent_spec.system_prompt,
                    "team_workspace_mount": team_workspace_mount,
                    "team_workspace_path": team_workspace_path,
                    "expose_human_agents_to_teammates": spec.expose_human_agents_to_teammates,
                    "enable_swarmflow": spec.enable_swarmflow,
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
        if spec.build_context is not None:
            member_build_context = spec.build_context.derive(
                member_name=member_name,
                role=ctx.role.value,
                language=resolved_language,
                member_card_id=self._card.id,
            )
            member_build_context.extras = dict(member_build_context.extras)
        else:
            member_build_context = BuildContext(
                language=resolved_language,
                member_name=member_name,
                role=ctx.role.value,
                member_card_id=self._card.id,
            )
        # Swarmflow worker-model resolver (leader + enable_swarmflow only). A
        # positional pool lookup by ``agent(model=...)`` name hint; None when the
        # team spec is absent so the worker falls back to the leader's model. The
        # leader-only async ``swarmflow`` tool is gated on this being non-None.
        swarmflow_model_resolver: Optional[Callable[[str], Any]] = None
        swarmflow_worker_base_spec = None
        swarmflow_human_base_spec = None
        if ctx.role == TeamRole.LEADER and spec.enable_swarmflow:
            team_spec_for_models = ctx.team_spec

            def swarmflow_model_resolver(model_name: str, _spec=team_spec_for_models) -> Any:
                """Resolve an ``agent(model=...)`` name hint to a worker ``TeamModelConfig``.

                Returns a model *config* (not a built ``Model``): swarmflow workers
                go through the spec build path, where ``DeepAgentSpec.model`` is a
                ``TeamModelConfig`` resolved at construction. ``None`` falls back to
                the worker base spec's own model.
                """
                if _spec is None:
                    return None
                from openjiuwen.agent_teams.models.allocator import resolve_member_model

                return resolve_member_model(_spec, model_name=model_name, model_index=None)

            # Workers are "a teammate without team tools": derive each worker from
            # the team's teammate spec (or the leader spec when no teammate exists).
            # The raw agents[...] spec carries teammate capabilities but NOT the
            # team rails (those are injected here, per member), so a worker built
            # straight from it has no team tools by construction.
            base_specs = spec.agents
            swarmflow_worker_base_spec = base_specs.get("teammate") or base_specs.get("leader")
            # Human-session avatars derive from the human_agent spec; fall back to
            # the worker base spec so human_session still works when no dedicated
            # human_agent spec is configured (it just lacks human-tuned persona).
            swarmflow_human_base_spec = base_specs.get("human_agent") or swarmflow_worker_base_spec

            # Workers also need the observability rail for agent spans.
            if swarmflow_worker_base_spec is not None:
                swarmflow_worker_base_spec = swarmflow_worker_base_spec.model_copy(
                    update={
                        "rails": list(swarmflow_worker_base_spec.rails or [])
                                 + [observability_rail_spec],
                    },
                )

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
            reliability_components=reliability_components,
            permissions_override=ctx.permissions_override,
            worktree_manager=self.worktree_manager,
        )

        # Fold the team rails into the spec rails (after the user rails, to keep
        # the init order consistent with the legacy mount order).
        team_rail_specs.append(observability_rail_spec)
        build_spec = build_spec.model_copy(
            update={"rails": list(build_spec.rails or []) + team_rail_specs},
        )

        self.harness = TeamHarness.build(
            agent_spec=build_spec,
            role=ctx.role,
            member_name=member_name,
            initial_plan_mode=is_team_plan_leader,
            build_context=member_build_context,
        )

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
            enable_hitt=spec.enable_hitt,
            enable_bridge=spec.enable_bridge,
            external_cli_agents=spec.external_cli_agents,
            on_team_cleaned=on_team_cleaned,
            on_team_built=on_team_built,
            leader_member_name=ctx.team_spec.leader_member_name if ctx.team_spec else None,
        )
        self.team_backend = agent_team
        self.task_manager = agent_team.task_manager
        self.message_manager = agent_team.message_manager

        if self.workspace_manager:
            agent_team.register_cleanup_path(self.workspace_manager.workspace_path)

        agent_team.register_cleanup_path(str(team_home(team_name)))

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
    def role_policy(self) -> str:
        return self._blueprint.role_policy if self._blueprint else ""

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
