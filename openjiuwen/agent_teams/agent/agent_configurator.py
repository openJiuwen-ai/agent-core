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
from openjiuwen.agent_teams.paths import team_workspace_dir
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
from openjiuwen.agent_teams.skill.rail_spec import (
    build_team_skill_rail_spec,
    complete_declared_team_skill_rails,
)
from openjiuwen.agent_teams.tools.team import TeamBackend
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


_TEAM_WORKTREE_BASH_DENY_PATTERNS = [
    r"\bgit(?:\s+(?:-[A-Za-z](?:\s+\S+)?|--[^\s;&|]+(?:=\S+)?))*\s+worktree\s+"
    r"(?:add|remove|prune|move|repair|lock|unlock)\b",
]


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
            # Conditional creation: an in-process teammate
            # whose manager was injected by ``share_workspace_cache_with``
            # before ``configure`` already carries the leader's manager —
            # do not overwrite it with a per-agent copy, otherwise the
            # ``_assemble_member_workspace`` reuse check would miss and the
            # teammate would re-scan the team-workspace md files.
            if self.workspace_manager is None:
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
            on_before_team_cleaned=on_before_team_cleaned,
            on_team_cleaned=on_team_cleaned,
            on_team_built=on_team_built,
        )

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
        ws_path = ws_config.root_path or str(team_workspace_dir(team_name))
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
            # External CLI members (claude/codex): when evolution is on, also
            # build the in-team workspace dir + write B-class identity md (no
            # symlink out of the team tree), mirroring in-process members so
            # evolved values reach the model via the shared cache and survive
            # a session restart. Silently skipped when evolution is off or no
            # shared workspace_manager is wired.
            self._prepare_external_cli_workspace(spec, ctx)
            # This branch returns above before the regular
            # ``_attach_workspace_cache`` call in the DeepAgent path, so the
            # member's ``TeamBackend`` never inherits the leader's manager —
            # ``workspace_cache`` stays None and ``bind_team_tools`` builds the
            # translator with ``ws_cache=None``, falling back to framework
            # defaults instead of the team's evolved C-class tool
            # descriptions/params. Reuse the same attach entry as in-process
            # members so external-CLI cache reads route through the shared
            # manager (no second attach path).
            resolved_language = self._blueprint.language if self._blueprint else "cn"
            self._attach_workspace_cache(spec, ctx, resolved_language)
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
            from openjiuwen.agent_teams.team_workspace.binder import (
                prepare_member_workspace,
            )

            team_name = (ctx.team_spec.team_name if ctx.team_spec else None) or spec.team_name
            # Block C member-directory linker: flatten the member's real
            # directory out of the team tree, expose it in-team via a link.
            # The root returned is always the in-team ``team_member_workspace_dir``
            # — A/B code never notices the link.
            root_path = prepare_member_workspace(
                team_name=team_name,
                member_name=ctx.member_name or "",
                role=ctx.role,
                leader_member_name=(ctx.team_spec.leader_member_name if ctx.team_spec else None)
                or spec.leader.member_name,
                predefined_members={m.member_name for m in spec.predefined_members},
                member_workspace_prefix=spec.member_workspace_prefix,
            )
            ws_spec = ws_spec.model_copy(update={"root_path": root_path})

        # cwd is a separate layer from the workspace. The workspace stays the
        # member's private artifact directory (memory, Skill visibility
        # declaration); cwd is where shell runs and relative paths resolve. Team
        # isolation moves cwd into the worktree without dragging the workspace
        # along -- otherwise the member's artifacts and its Skill grants would
        # live inside an ephemeral checkout and vanish with it. A projectless
        # team member (no project, no worktree) instead runs in its own
        # isolated ``work/<member>/`` under the shared artifact root, so its
        # intermediate files stay per-member instead of piling up together.
        worktree_path = ctx.worktree_path
        project_root_or_cwd = agent_spec.project_root or agent_spec.cwd or None
        if worktree_path:
            member_cwd = worktree_path
        elif project_root_or_cwd:
            member_cwd = project_root_or_cwd
        elif spec.build_context is not None:
            # No project and no worktree: the platform may allocate a per-member
            # work directory. Derive a member view (so the per-team root and the
            # member name combine) and ask the platform for the work dir.
            member_cwd = spec.build_context.derive(
                member_name=ctx.member_name,
            ).resolve_member_work_dir()
        else:
            member_cwd = None
        member_project_root = project_root_or_cwd

        workspace_root_path = ws_spec.root_path if ws_spec is not None else None
        # The workspace is now always the member's own directory (never the
        # project dir, never a worktree), so it is unconditionally ours to
        # clean up.
        if workspace_root_path and self.team_backend is not None:
            self.team_backend.register_cleanup_path(workspace_root_path)

        model_config = ctx.member_model or agent_spec.model

        sys_operation_spec = agent_spec.sys_operation or SysOperationSpec(
            id=f"{self._card.id}.sys_operation",
            mode=OperationMode.LOCAL,
            work_config=LocalWorkConfig(shell_allowlist=None),
        )
        # Keep ``None`` as auto mode. Image support is cached process-wide by
        # endpoint and model, so team members reuse the main warm-up verdict
        # instead of paying for one probe per member.
        enable_read_image_multimodal = agent_spec.enable_read_image_multimodal
        # Skills are cleared and discovery is switched off on purpose: the
        # DeepAgent factory auto-adds the generic SkillUseRail when either is
        # truthy, and that rail scans the member workspace's own ``skills/``
        # node plus every mounted team directory. A team member instead reads
        # the one shared Skill library through ``core.team.skill_use``, which
        # narrows it by the member's and the team's visibility declarations.
        # ``agent_spec.skills`` is not discarded -- it travels into that rail's
        # params as the member declaration's seed allow-list.
        build_spec = agent_spec.model_copy(
            update={
                "card": self._card,
                "model": model_config,
                "workspace": ws_spec,
                "cwd": member_cwd,
                "project_root": member_project_root,
                "sys_operation": sys_operation_spec,
                "tools": list(agent_spec.tools or []),
                "skills": [],
                "enable_skill_discovery": False,
                "enable_task_loop": True,
                "enable_read_image_multimodal": enable_read_image_multimodal,
            }
        )

        resolved_team_name = (ctx.team_spec.team_name if ctx.team_spec else None) or spec.team_name
        teammate_mode = str(spec.teammate_mode)

        team_workspace_path: str | None = None
        if self.workspace_manager:
            team_workspace_path = self.workspace_manager.workspace_path
        # The team's shared final-deliverables directory travels on the build
        # context (platform-filled for projectless members, None for members
        # bound to a project). Surfaced to the team info body by the policy
        # rail only when set, so members with a project keep the bullet off.
        team_outputs_dir: str | None = (
            spec.build_context.team_outputs_dir
            if spec.build_context is not None
            else None
        )

        # Decide which team rails this member gets, as declarative RailSpecs.
        # Live handles ride on the build context's extras (injected below); only
        # serializable static config goes in params. Decisions that depend on
        # team config (predefined roster, plan-mode, reliability gating) stay
        # here; "can it build" gates (a missing handle) live in the factories.
        from openjiuwen.agent_teams.rails.elements import (
            OBSERVABILITY,
            TEAM_OBSERVABILITY,
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
                    "dispatch_mode": spec.dispatch_mode,
                    "lifecycle": spec.lifecycle,
                    "team_mode": _resolve_team_mode(spec),
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
                    "team_workspace_path": team_workspace_path,
                    "team_outputs_dir": team_outputs_dir,
                    "expose_human_agents_to_teammates": spec.expose_human_agents_to_teammates,
                    "steer_batch_size": spec.steer_batch_size,
                    "fork_source": ctx.fork_source or "",
                },
            ),
        ]
        if self.workspace_manager:
            team_rail_specs.append(RailSpec(type=TEAM_WORKSPACE, params={}))

        is_coordinated_teammate = ctx.role.is_coordinated_member and ctx.team_spec
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
        # Swarmflow worker-model resolver (leader + enable_swarmflow only). A
        # positional pool lookup by ``agent(model=...)`` name hint; None when the
        # team spec is absent so the worker falls back to the leader's model. The
        # leader-only async ``swarmflow`` tool is gated on this being non-None.
        swarmflow_model_resolver: Optional[Callable[[str], Any]] = None
        swarmflow_worker_base_spec = None
        swarmflow_human_base_spec = None
        swarmflow_concurrency_governor = None
        swarmflow_budget = None
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
            # human_agent spec is configured (it just lacks human-tuned desc).
            swarmflow_human_base_spec = base_specs.get("human_agent") or swarmflow_worker_base_spec

            # Workers also need the observability rail for agent spans.
            if swarmflow_worker_base_spec is not None:
                swarmflow_worker_base_spec = swarmflow_worker_base_spec.model_copy(
                    update={
                        "rails": list(swarmflow_worker_base_spec.rails or []) + observability_rail_specs,
                    },
                )

            from openjiuwen.agent_teams.workflow.concurrency import (
                ConcurrencyGovernor,
                validate_swarmflow_concurrency,
            )

            l2_cap = validate_swarmflow_concurrency(spec.swarmflow_concurrency)
            swarmflow_concurrency_governor = ConcurrencyGovernor(
                spec.swarmflow_concurrency,
                agents_per_run_cap=l2_cap,
            )

            from openjiuwen.agent_teams.workflow.engine.budget import BudgetLedger

            # One ledger per leader, shared by every run it launches (like the
            # governor's L3): concurrent runs draw down one pool. ``total=None``
            # keeps it unbounded while still giving scripts a live ``spent()``.
            swarmflow_budget = BudgetLedger(total=spec.swarmflow_budget)

        inject_team_handles(
            member_build_context.extras,
            team_backend=self.team_backend,
            workspace_manager=self.workspace_manager,
            model_allocator=self.model_allocator,
            messager=self.messager,
            swarmflow_model_resolver=swarmflow_model_resolver,
            swarmflow_worker_base_spec=swarmflow_worker_base_spec,
            swarmflow_human_base_spec=swarmflow_human_base_spec,
            swarmflow_concurrency_governor=swarmflow_concurrency_governor,
            swarmflow_budget=swarmflow_budget,
            reliability_components=reliability_components,
            permissions_override=ctx.permissions_override,
            worktree_manager=self.worktree_manager,
        )

        # Fold the team rails into the spec rails (after the user rails, to keep
        # the init order consistent with the legacy mount order).
        team_rail_specs.extend(observability_rail_specs)
        base_rails = _apply_team_worktree_shell_guard(
            list(build_spec.rails or []),
            enabled=ctx.role in {TeamRole.LEADER, TeamRole.TEAMMATE, TeamRole.EXTERNAL_CLI},
        )
        if ctx.role in {
            TeamRole.LEADER,
            TeamRole.TEAMMATE,
            TeamRole.EXTERNAL_CLI,
        } and not _has_team_worktree_shell_guard(base_rails):
            team_logger.warning(
                "Team-managed worktree shell guard was not applied for member {} because core.sys_operation is absent",
                member_name,
            )
        # Declared last, once base_rails is final: whether the Skill rail brings
        # its own read_file / bash fallback depends on a system-operation rail
        # being present, and a blueprint that declares its own Skill rail keeps it.
        # A blueprint that declared a bare team Skill rail is completed first --
        # it owns the exposure mode, but only the member is knowable here.
        member_workspace_path = ws_spec.root_path
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
            declared_rails=base_rails,
            team_workspace_path=team_workspace_path,
            member_workspace_path=member_workspace_path,
        )
        if skill_rail_spec is not None:
            team_rail_specs.append(skill_rail_spec)
        build_spec = build_spec.model_copy(
            update={"rails": base_rails + team_rail_specs},
        )

        # Attach the evolvable-workspace cache BEFORE the harness build so
        # the rail factories mint their A-class loaders against it — a fresh
        # instance (first build / COLD_RECOVER) whose cache attached
        # afterwards would bind the framework read-only loader and never
        # see the team's evolved prompt values.
        self._attach_workspace_cache(spec, ctx, resolved_language)

        self.harness = TeamHarness.build(
            agent_spec=build_spec,
            role=ctx.role,
            member_name=member_name,
            initial_plan_mode=is_team_plan_leader,
            build_context=member_build_context,
        )

        # Leader's own model calls (decision / tool use / script generation) bill
        # the session budget — NOT the per-run budget. Only when swarmflow is
        # enabled (swarmflow_budget is a real ledger) does the leader carry this
        # rail; the per-run ledger is passed as None so the rail only adds to the
        # session pool. TinyAgent intent classify gets the same treatment in
        # avatar_session_backend.
        if swarmflow_budget is not None and ctx.role == TeamRole.LEADER:
            from openjiuwen.agent_teams.workflow.backends.budget_rail import SwarmflowBudgetRail

            self.harness.add_rail(SwarmflowBudgetRail(swarmflow_budget, workflow_budget=None))

        # Team memory manager (only when explicitly enabled in the spec).
        self.memory_manager = self._build_memory_manager(spec, ctx, agent_spec, resolved_language, member_name)

        # Assembly: write the evolvable workspace files (idempotent on every
        # spawn / session recovery).
        self._assemble_member_workspace(spec, ctx, resolved_language)

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
            enable_fork=spec.enable_fork,
            evolution_enabled=spec.evolution_enabled,
            member_workspace_prefix=spec.member_workspace_prefix,
            external_cli_agents=spec.external_cli_agents,
            on_before_team_cleaned=on_before_team_cleaned,
            on_team_cleaned=on_team_cleaned,
            on_team_built=on_team_built,
            on_member_started=self._on_teammate_created,
            leader_member_name=ctx.team_spec.leader_member_name if ctx.team_spec else None,
        )

        def _snapshot_length() -> int:
            h = self.harness
            if h is not None and hasattr(h, "get_deep_agent"):
                native = h.get_deep_agent()
                if native is not None:
                    return len(native.get_current_context())
            return 0

        agent_team.set_snapshot_length(_snapshot_length)

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

    # ── evolvable-workspace assembly ──────────────────────────────────────

    def _attach_workspace_cache(
        self,
        spec: TeamAgentSpec,
        ctx: TeamRuntimeContext,
        resolved_language: str,
    ) -> None:
        """Create (or reuse) the evolvable-workspace cache and attach it.

        Runs **before** ``TeamHarness.build`` so the rail factories mint their
        A-class loaders against an already-attached cache — without this, a
        fresh instance (first build / COLD_RECOVER) would bind the framework
        read-only loader and the team's evolved prompt values would never
        reach the model. Idempotent: a manager that already carries a cache
        (in-process teammate sharing the leader's manager via
        ``share_workspace_cache_with``) is reused, never re-created.

        Also called from the external-CLI branch of ``setup_agent``: that
        branch returns before the DeepAgent path, but the member's
        ``TeamBackend`` still needs its cache reads routed to the shared
        manager so ``bind_team_tools`` (in-process claude SDK path) sees
        evolved C-class tool descriptions/params. When the leader's manager
        already carries a cache (the normal case for a spawned teammate), the
        reuse branch below attaches it and returns; a cache-less manager
        falls through to create one.

        Skips silently when the member has no team context (single-agent
        without a team spec) or evolution is off.
        """
        team_name = (ctx.team_spec.team_name if ctx.team_spec else None) or spec.team_name
        member_name = ctx.member_name
        if not member_name:
            return

        from openjiuwen.agent_teams.team_workspace.workspace_store import WorkspaceStore

        evolution_enabled = True
        if ctx.team_spec is not None:
            evolution_enabled = ctx.team_spec.evolution_enabled
        if not evolution_enabled:
            # Evolution disabled: no cache object is built — the manager
            # carries None and every read falls back to framework / DB.
            return

        # Team-level cache reuse: in-process teammates share
        # the leader's manager by reference via ``share_workspace_cache_with``
        # (run before configure). When the manager already carries a cache,
        # just route the backend's reads to it — the leader's one cache
        # instance is the team's single source of truth.
        if self.workspace_manager is not None and self.workspace_manager.workspace_cache is not None:
            if self.team_backend is not None:
                self.team_backend.attach_workspace_manager(self.workspace_manager)
            team_logger.info(
                "[workspace] {} reuses team-level cache (shared manager)",
                member_name,
            )
            return

        # Create the evolvable-workspace cache object so every read-side
        # consumer binds the same instance. An empty object:
        # no proactive build, no file scan. Values fill lazily on the first
        # ``get*`` (miss reads the file once, then hits) and drop on the
        # Runner finally pause path via ``invalidate``.
        from openjiuwen.agent_teams.team_workspace.workspace_cache import WorkspaceCache

        store = WorkspaceStore()
        cache = WorkspaceCache(
            store,
            team_name,
            language=resolved_language,
        )
        # One cache instance lives on the workspace manager — every
        # read-side consumer (backend overlay via the manager delegation,
        # rails / worker backend / tiny agent / scheduler) takes the same
        # instance through ``manager.workspace_cache``.
        if self.workspace_manager is not None:
            self.workspace_manager.attach_workspace_cache(cache)
        # Route backend cache reads to the manager (single source of truth):
        # A-class rail factories and the B-class member overlay
        # read ``team_backend.workspace_cache``, which delegates to the
        # manager's resident instance.
        if self.team_backend is not None:
            self.team_backend.attach_workspace_manager(self.workspace_manager)

    def _prepare_external_cli_workspace(
        self,
        spec: TeamAgentSpec,
        ctx: TeamRuntimeContext,
    ) -> None:
        """Ensure the external CLI member's in-team workspace exists and seed
        B-class identity md (no symlink out of the team tree).

        Mirrors what ``_assemble_member_workspace`` does for an in-process
        teammate, but the CLI member's workspace is a pure in-team directory
        (``workspaces/<m>_workspace/``) — no symlink out of the team tree, no
        ref count. The identity md is primed into the shared workspace cache
        (the leader's, injected via ``share_workspace_cache_with`` before
        ``configure``), so evolved values reach the leader's roster overlay
        and survive a session restart re-read.

        Skips silently when evolution is disabled or the member has no shared
        workspace manager (``spec.workspace`` not enabled) — in those cases the
        CLI member keeps its lightweight behaviour and identity lives in the DB
        only.
        """
        if not spec.evolution_enabled:
            return
        if self.workspace_manager is None:
            return
        member_name = ctx.member_name
        if not member_name:
            return
        team_name = (ctx.team_spec.team_name if ctx.team_spec else None) or spec.team_name
        # Pure in-team directory (same layout as the leader's): mkdir, no link.
        from openjiuwen.agent_teams.paths import team_member_workspace_dir

        team_member_workspace_dir(team_name, member_name).mkdir(
            parents=True, exist_ok=True
        )
        # B-class identity md write + cache prime. ``resolved_language`` is
        # unused for member identity (no lang suffix); pass empty string.
        self._assemble_member_workspace(spec, ctx, "")

    def _assemble_member_workspace(
        self,
        spec: TeamAgentSpec,
        ctx: TeamRuntimeContext,
        resolved_language: str,
    ) -> None:
        """Seed the evolvable B-class member files (write side only).

        Runs on every spawn / session recovery inside ``setup_agent`` and is
        idempotent: existing directories, junctions and baselines are reused.
        The cache attach itself happens earlier in ``setup_agent`` (via
        ``_attach_workspace_cache``, before the harness build) — this method
        only writes the B-class member identity files and primes the shared
        cache with the final bodies.

        Skips silently when the member has no team context (single-agent)
        or when the evolution mechanism is disabled.
        """
        team_name = (ctx.team_spec.team_name if ctx.team_spec else None) or spec.team_name
        member_name = ctx.member_name
        if not member_name:
            return
        if not spec.evolution_enabled:
            # Evolution disabled: the write side is off — no B-class file.
            return

        from openjiuwen.agent_teams.team_workspace.assembler import WorkspaceAssembler
        from openjiuwen.agent_teams.team_workspace.workspace_store import WorkspaceStore

        # B-class member values come from the runtime context (mirrors the DB
        # row / evolved md). Team-level files (system prompt templates, tool
        # descriptions, team identity) are written earlier — at
        # ``coordination.start`` (framework-source A/C baselines) and at
        # ``build_team`` (team identity) — not in per-member assembly.
        member_desc = ctx.desc or None
        member_prompt = ctx.prompt or None

        store = WorkspaceStore()
        cache = self.workspace_manager.workspace_cache if self.workspace_manager else None
        assembler = WorkspaceAssembler(store, cache=cache)
        # B-class member identity — written on member spawn / recovery.
        assembler.write_member_identity(
            team_name=team_name,
            member_name=member_name,
            member_desc=member_desc,
            member_prompt=member_prompt,
        )
