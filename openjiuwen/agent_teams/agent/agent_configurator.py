# coding: utf-8
"""Agent configuration, setup, and initialization for TeamAgent."""

from __future__ import annotations

import os
from typing import (
    TYPE_CHECKING,
    Any,
    Dict,
    List,
    Optional,
)

from openjiuwen.agent_teams.agent.policy import role_policy
from openjiuwen.agent_teams.messager import (
    Messager,
    create_messager,
)
from openjiuwen.agent_teams.paths import (
    independent_member_workspace,
    team_home,
    team_memory_dir as default_team_memory_dir,
)
from openjiuwen.agent_teams.schema.blueprint import TeamAgentSpec
from openjiuwen.agent_teams.schema.deep_agent_spec import SysOperationSpec
from openjiuwen.agent_teams.schema.team import (
    TeamMemberSpec,
    TeamRole,
    TeamRuntimeContext,
    TeamSpec,
)
from openjiuwen.agent_teams.tools.team import TeamBackend
from openjiuwen.core.common.logging import team_logger
from openjiuwen.core.foundation.tool import ToolCard
from openjiuwen.core.foundation.tool.base import Tool
from openjiuwen.core.runner.runner import Runner
from openjiuwen.core.runner.spawn.agent_config import (
    SpawnAgentConfig,
    SpawnAgentKind,
    serialize_runner_config,
)
from openjiuwen.core.single_agent.schema.agent_card import AgentCard
from openjiuwen.core.sys_operation import LocalWorkConfig, OperationMode
from openjiuwen.harness.deep_agent import DeepAgent
from openjiuwen.harness.prompts import resolve_language as _resolve_language

if TYPE_CHECKING:
    from openjiuwen.agent_teams.agent.model_allocator import Allocation, ModelAllocator
    from openjiuwen.agent_teams.team_workspace.manager import TeamWorkspaceManager
    from openjiuwen.agent_teams.worktree.manager import WorktreeManager
    from openjiuwen.core.memory.team.manager import TeamMemoryManager


def _resolve_team_mode(spec: TeamAgentSpec) -> str:
    if spec.team_mode is not None:
        return spec.team_mode
    return "predefined" if spec.predefined_members else "default"


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
        self.spec: Optional[TeamAgentSpec] = None
        self.ctx: Optional[TeamRuntimeContext] = None
        self.role_policy: str = ""
        self.workspace_manager: Optional[TeamWorkspaceManager] = None
        self.workspace_initialized: bool = False
        self.worktree_manager: Optional[WorktreeManager] = None
        self.model_allocator: Optional[ModelAllocator] = None
        self.leader_allocation: Optional[Allocation] = None
        self.tool_cards: List[ToolCard] = []
        self.deep_agent: Optional[DeepAgent] = None
        self.team_backend: Optional[TeamBackend] = None
        self.task_manager: Any = None
        self.message_manager: Any = None
        self.messager: Optional[Messager] = None
        self.member_port_map: dict[str, int] = {}
        self.teammate_port_counter: int = 0
        self.memory_manager: Optional[TeamMemoryManager] = None

    def configure(self, spec: TeamAgentSpec, ctx: TeamRuntimeContext) -> DeepAgent:
        """Main entry point: configure infrastructure and build DeepAgent."""
        self.setup_infra(spec, ctx)
        return self.setup_agent(spec, ctx)

    def setup_infra(self, spec: TeamAgentSpec, ctx: TeamRuntimeContext, *, on_teammate_created=None) -> None:
        """Phase 1: set spec/context, create messager, workspace manager, register team tools."""
        self.spec = spec
        self.ctx = ctx

        messager_config = ctx.messager_config
        member_name = ctx.member_name
        if member_name and messager_config and messager_config.node_id != member_name:
            messager_config = messager_config.model_copy(update={"node_id": member_name})

        self.messager = create_messager(messager_config) if messager_config else None

        if spec.workspace and spec.workspace.enabled:
            self.workspace_manager = self.create_workspace_manager(spec, ctx)

        if ctx.role == TeamRole.LEADER and self.model_allocator is None:
            from openjiuwen.agent_teams.agent.model_allocator import (
                build_model_allocator,
            )

            self.model_allocator = build_model_allocator(spec, ctx.team_spec)

        self.tool_cards = self.register_team_tools(spec, ctx, self.messager, on_teammate_created=on_teammate_created)

    def create_workspace_manager(
        self,
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
        from openjiuwen.agent_teams.worktree.manager import WorktreeManager

        ws_root = self.workspace_manager.workspace_path if self.workspace_manager else None
        return WorktreeManager(
            config=spec.worktree,
            workspace_root=ws_root,
        )

    def setup_agent(
        self,
        spec: TeamAgentSpec,
        ctx: TeamRuntimeContext,
    ) -> DeepAgent:
        """Phase 2: build prompt, create DeepAgent, set up coordination."""
        agent_spec = self.resolve_agent_spec(spec, ctx.role, ctx.member_name)
        resolved_language = _resolve_language(agent_spec.language)
        self.role_policy = role_policy(ctx.role, language=resolved_language)
        member_name = ctx.member_name

        ws_spec = agent_spec.workspace or spec.agents.get("leader", agent_spec).workspace
        if ws_spec and ws_spec.stable_base:
            team_name = (ctx.team_spec.team_name if ctx.team_spec else None) or spec.team_name
            base = team_home(team_name) / "workspaces"
            team_ws_path = base / f"{member_name}_workspace"
            independent_ws = independent_member_workspace(member_name)
            if independent_ws.is_dir() and not team_ws_path.exists():
                base.mkdir(parents=True, exist_ok=True)
                os.symlink(str(independent_ws), str(team_ws_path), target_is_directory=True)
            ws_spec = ws_spec.model_copy(update={"root_path": str(team_ws_path)})

        if ws_spec and ws_spec.root_path and self.team_backend:
            self.team_backend.register_cleanup_path(ws_spec.root_path)

        if self.workspace_manager and ws_spec and ws_spec.root_path:
            self.workspace_manager.mount_into_workspace(ws_spec.root_path)

        model_config = ctx.member_model or agent_spec.model

        merged_tools = list(self.tool_cards)
        if agent_spec.tools:
            merged_tools.extend(agent_spec.tools)

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
                "tools": merged_tools,
                "enable_skill_discovery": True,
                "enable_task_loop": True,
            }
        )
        self.deep_agent = build_spec.build()

        team_workspace_mount: str | None = None
        team_workspace_path: str | None = None
        if self.workspace_manager:
            resolved_team_name = (ctx.team_spec.team_name if ctx.team_spec else None) or spec.team_name
            team_workspace_mount = f".team/{resolved_team_name}/"
            team_workspace_path = self.workspace_manager.workspace_path

        from openjiuwen.agent_teams.agent.team_rail import TeamRail

        self.deep_agent.add_rail(
            TeamRail(
                role=ctx.role,
                persona=ctx.persona,
                member_name=member_name,
                lifecycle=spec.lifecycle,
                teammate_mode=spec.teammate_mode,
                language=resolved_language,
                team_mode=_resolve_team_mode(spec),
                base_prompt=agent_spec.system_prompt,
                team_workspace_mount=team_workspace_mount,
                team_workspace_path=team_workspace_path,
                team_backend=self.team_backend,
            )
        )

        from openjiuwen.agent_teams.agent.rails import FirstIterationGate

        first_iter_gate = FirstIterationGate()
        self.deep_agent.add_rail(first_iter_gate)

        if self.workspace_manager:
            from openjiuwen.agent_teams.team_workspace.rails import TeamWorkspaceRail

            self.deep_agent.add_rail(
                TeamWorkspaceRail(self.workspace_manager, member_name or ""),
            )

        is_coordinated_teammate = ctx.role == TeamRole.TEAMMATE and ctx.team_spec
        if is_coordinated_teammate and self.team_backend and self.messager:
            from openjiuwen.agent_teams.agent.rails import TeamToolApprovalRail

            approval_tools = agent_spec.approval_required_tools or []
            if approval_tools:
                self.deep_agent.add_rail(
                    TeamToolApprovalRail(
                        team_name=ctx.team_spec.team_name,
                        member_name=member_name or "",
                        db=self.team_backend.db,
                        messager=self.messager,
                        leader_member_name=ctx.team_spec.leader_member_name or "",
                        tool_names=approval_tools,
                    )
                )

        # Team memory manager (only when explicitly enabled in the spec).
        # Construction must happen before agent_customizer so platform
        # adapters that touch memory tools can see them.
        self.memory_manager = self._build_memory_manager(spec, ctx, agent_spec, resolved_language, member_name)

        if spec.agent_customizer and self.deep_agent:
            try:
                spec.agent_customizer(self.deep_agent, member_name, ctx.role.value)
            except Exception as exc:
                team_logger.warning(
                    "[{}] agent_customizer failed: {}",
                    member_name or "?",
                    exc,
                )

        return self.deep_agent

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

        from openjiuwen.core.memory.team.config import resolve_embedding_config
        from openjiuwen.core.memory.team.manager import TeamMemoryManager
        from openjiuwen.core.memory.team.manager_params import TeamMemoryManagerParams

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

        agent_workspace = self.deep_agent.deep_config.workspace if self.deep_agent else None
        sys_operation = self.deep_agent.deep_config.sys_operation if self.deep_agent else None

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

    def resolve_agent_spec(
        self,
        spec: TeamAgentSpec,
        role: TeamRole,
        member_name: Optional[str] = None,
    ):
        if member_name and member_name in spec.agents:
            return spec.agents[member_name]
        return spec.agents.get(role.value) or spec.agents.get("teammate") or spec.agents["leader"]

    def register_team_tools(
        self,
        spec: TeamAgentSpec,
        ctx: TeamRuntimeContext,
        messager: Messager,
        on_teammate_created=None,
    ) -> List[ToolCard]:
        from openjiuwen.agent_teams.schema.status import MemberMode
        from openjiuwen.agent_teams.spawn.shared_resources import get_shared_db
        from openjiuwen.agent_teams.tools.team_tools import create_team_tools

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
            teammate_mode=MemberMode(spec.teammate_mode),
            predefined_members=spec.predefined_members or None,
            model_config_allocator=self.model_allocator.allocate if self.model_allocator else None,
            leader_allocation=self.leader_allocation if is_leader else None,
        )
        self.team_backend = agent_team
        self.task_manager = agent_team.task_manager
        self.message_manager = agent_team.message_manager

        if self.workspace_manager:
            agent_team.register_cleanup_path(self.workspace_manager.workspace_path)

        agent_team.register_cleanup_path(str(team_home(team_name)))

        exclude = {"spawn_member"} if _resolve_team_mode(spec) == "predefined" else None
        lang = _resolve_language(ctx.team_spec.language if ctx.team_spec else None)
        team_tools = create_team_tools(
            role=ctx.role.value,
            agent_team=agent_team,
            teammate_mode=spec.teammate_mode,
            on_teammate_created=on_teammate_created,
            model_config_allocator=self.model_allocator.allocate if self.model_allocator else None,
            exclude_tools=exclude,
            lang=lang,
        )
        if self.workspace_manager:
            from openjiuwen.agent_teams.team_workspace.tools import WorkspaceMetaTool
            from openjiuwen.agent_teams.tools.locales import make_translator

            ws_t = make_translator(lang)
            team_tools.append(WorkspaceMetaTool(self.workspace_manager, ws_t))

        if not is_leader and spec.worktree and spec.worktree.enabled:
            from openjiuwen.agent_teams.tools.locales import make_translator
            from openjiuwen.agent_teams.worktree.tools import EnterWorktreeTool, ExitWorktreeTool

            self.worktree_manager = self.create_worktree_manager(spec)
            wt_t = make_translator(lang)
            team_tools.append(EnterWorktreeTool(self.worktree_manager, wt_t))
            team_tools.append(ExitWorktreeTool(self.worktree_manager, wt_t))
            from openjiuwen.agent_teams.worktree.session import init_session_state

            init_session_state()

        if spec.spawn_mode == "inprocess":
            _qualify_team_tool_ids(team_tools, team_name=team_name, member_name=current_member_name)

        try:
            Runner.resource_mgr.add_tool(team_tools)
        except Exception:
            team_logger.debug("Runner.resource_mgr not available, skipping tool registration")

        return [t.card for t in team_tools]

    def update_model_pool(self, new_pool: list) -> None:
        if self.ctx is None or self.ctx.team_spec is None:
            return
        from openjiuwen.agent_teams.agent.model_allocator import build_model_allocator
        from openjiuwen.agent_teams.schema.team import inherit_pool_ids

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
        team_spec = self.team_spec
        member_transport = self.build_member_messager_config(ctx.member_name)
        return {
            "coordination": {
                "team_name": team_spec.team_name if team_spec else "",
                "display_name": team_spec.display_name if team_spec else "",
                "leader_member_name": team_spec.leader_member_name if team_spec else None,
                "member_name": ctx.member_name,
                "role": ctx.role.value,
                "persona": ctx.persona,
                "transport": (member_transport.model_dump(mode="json") if member_transport is not None else None),
            },
            "query": initial_message or "Join the team and wait for your first assignment.",
        }

    def build_member_context(self, member_spec: TeamMemberSpec) -> TeamRuntimeContext:
        return TeamRuntimeContext(
            role=member_spec.role_type,
            member_name=member_spec.member_name,
            persona=member_spec.persona,
            team_spec=self.ctx.team_spec,
            messager_config=self.build_member_messager_config(member_spec.member_name),
            db_config=self.ctx.db_config,
        )

    def build_member_messager_config(self, member_name: str):
        if self.ctx is None or self.ctx.messager_config is None:
            return None
        leader_cfg = self.ctx.messager_config
        meta = self.spec.metadata if self.spec else {}
        base_port = meta.get("teammate_base_port", 16000)
        port_offset = meta.get("teammate_port_offset", 10)

        mid = member_name
        if mid in self.member_port_map:
            port_base = self.member_port_map[mid]
        else:
            port_base = base_port + self.teammate_port_counter * port_offset
            self.teammate_port_counter += 1
            self.member_port_map[mid] = port_base

        updates: Dict[str, Any] = {
            "node_id": member_name,
            "direct_addr": f"tcp://127.0.0.1:{port_base}",
            "pubsub_publish_addr": leader_cfg.pubsub_publish_addr,
            "pubsub_subscribe_addr": leader_cfg.pubsub_subscribe_addr,
        }
        metadata = dict(leader_cfg.metadata)
        metadata.pop("pubsub_bind", None)
        updates["metadata"] = metadata
        return leader_cfg.model_copy(update=updates)

    def build_spawn_config(self, ctx: TeamRuntimeContext) -> SpawnAgentConfig:
        logging_config = _build_member_logging_config(ctx.member_name or "", ctx.member_name or "")
        return SpawnAgentConfig(
            agent_kind=SpawnAgentKind.TEAM_AGENT,
            runner_config=serialize_runner_config(Runner.get_config()),
            logging_config=logging_config,
            session_id=None,
            payload={
                "spec": self.spec.model_dump(mode="json"),
                "context": ctx.model_dump(mode="json"),
            },
        )

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


def _qualify_team_tool_ids(team_tools: list[Tool], *, team_name: str, member_name: str) -> None:
    team_key = team_name or "default"
    member_key = member_name or "unknown"
    for tool in team_tools:
        if tool.card is None or not tool.card.id:
            continue
        qualified_id = f"{tool.card.id}.{team_key}.{member_key}"
        if tool.card.id != qualified_id:
            tool.card.id = qualified_id


def _build_member_logging_config(member_name: str, name: str) -> dict[str, Any]:
    from openjiuwen.core.common.logging.log_config import get_log_config_snapshot

    config = get_log_config_snapshot()
    member_tag = member_name or name
    sinks = config.get("sinks", {})
    for sink in sinks.values():
        target = sink.get("target")
        if not isinstance(target, str) or target in ("stdout", "stderr"):
            continue
        parts = target.rsplit("/", 1)
        if len(parts) == 2:
            sink["target"] = f"{parts[0]}/teammates/{member_tag}/{parts[1]}"
        else:
            sink["target"] = f"teammates/{member_tag}/{target}"
    return config
