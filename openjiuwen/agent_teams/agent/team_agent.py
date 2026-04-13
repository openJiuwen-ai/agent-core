# coding: utf-8
"""Unified TeamAgent implementation."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import (
    Any,
    Dict,
    List,
    Optional,
)

from openjiuwen.agent_teams.agent.coordinator import (
    CoordinatorLoop,
    InnerEventMessage,
    InnerEventType,
)
from openjiuwen.agent_teams.agent.member import TeamMember
from openjiuwen.agent_teams.agent.policy import role_policy
from openjiuwen.agent_teams.agent.team_rail import TeamRail
from openjiuwen.agent_teams.messager import (
    create_messager,
    Messager,
)
from openjiuwen.agent_teams.schema.blueprint import TeamAgentSpec
from openjiuwen.agent_teams.schema.status import (
    ExecutionStatus,
    MemberStatus,
)
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
    serialize_runner_config,
    SpawnAgentConfig,
    SpawnAgentKind,
)
from openjiuwen.core.runner.spawn.process_manager import (
    SpawnConfig,
    SpawnedProcessHandle,
)
from openjiuwen.core.session.interaction.interactive_input import InteractiveInput
from openjiuwen.core.session.agent_team import Session as AgentTeamSession
from openjiuwen.core.single_agent.base import BaseAgent
from openjiuwen.core.single_agent.interrupt.state import INTERRUPTION_KEY
from openjiuwen.core.single_agent.rail.base import AgentRail
from openjiuwen.harness.deep_agent import DeepAgent
from openjiuwen.harness.prompts import resolve_language as _resolve_language


class TeamAgent(BaseAgent):
    """One implementation that can act as leader or teammate.

    Uses composition: wraps an internal DeepAgent instance instead of
    inheriting from it.
    """

    def __init__(self, card):
        super().__init__(card)
        self._deep_agent: Optional[DeepAgent] = None
        self._spec: Optional[TeamAgentSpec] = None
        self._ctx: Optional[TeamRuntimeContext] = None
        self._coordination_loop: Optional[CoordinatorLoop] = None
        self._role_policy: str = ""
        self._messager: Optional[Messager] = None
        self._subscribed_topics: list[str] = []
        self._team_backend: Optional[TeamBackend] = None
        self._task_manager = None
        self._message_manager = None
        self._session_id: Optional[str] = None
        self._team_member: Optional[TeamMember] = None
        self._stream_queue: Optional[asyncio.Queue] = None
        self._agent_task: Optional[asyncio.Task] = None
        self._dispatcher = None
        self._teammate_port_counter: int = 0
        self._spawned_handles: dict[str, SpawnedProcessHandle] = {}
        self._member_port_map: dict[str, int] = {}
        self._first_iter_gate: Optional["FirstIterationGate"] = None
        self._pending_interrupt_resumes: list[InteractiveInput] = []
        self._event_listeners: list = []
        self._model_allocator: Optional["ModelAllocator"] = None
        self._workspace_manager: Optional["TeamWorkspaceManager"] = None
        self._workspace_initialized: bool = False
        self._worktree_manager: Optional["WorktreeManager"] = None
        self._team_session: Optional[AgentTeamSession] = None

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def deep_agent(self) -> Optional[DeepAgent]:
        """Return the internal DeepAgent instance."""
        return self._deep_agent

    @property
    def deep_config(self) -> Optional["DeepAgentConfig"]:
        """Proxy: return the DeepAgent's config."""
        if self._deep_agent is None:
            return None
        return self._deep_agent.deep_config

    @property
    def spec(self) -> Optional[TeamAgentSpec]:
        """Return the team agent spec."""
        return self._spec

    @property
    def runtime_context(self) -> Optional[TeamRuntimeContext]:
        """Return the runtime context."""
        return self._ctx

    @property
    def coordination_loop(self) -> Optional[CoordinatorLoop]:
        """Return the shared coordination loop."""
        return self._coordination_loop

    @property
    def role(self) -> TeamRole:
        """Return the configured team role."""
        if self._ctx is None:
            return TeamRole.LEADER
        return self._ctx.role

    @property
    def lifecycle(self) -> str:
        """Return the team lifecycle mode."""
        if self._spec is None:
            return "temporary"
        return self._spec.lifecycle

    @property
    def role_prompt_policy(self) -> str:
        """Return the active base role policy."""
        return self._role_policy

    @property
    def mailbox_transport(self) -> Optional[Messager]:
        """Return the configured mailbox transport, if any."""
        return self._messager

    @property
    def team_spec(self) -> Optional[TeamSpec]:
        """Return the bound team spec."""
        if self._ctx is None:
            return None
        return self._ctx.team_spec

    @property
    def member_name(self) -> Optional[str]:
        """Return the current agent's member_name."""
        return self._member_name()

    @property
    def message_manager(self):
        """Return the message manager, if configured."""
        return self._message_manager

    @property
    def task_manager(self):
        """Return the task manager, if configured."""
        return self._task_manager

    @property
    def team_backend(self) -> Optional[TeamBackend]:
        """Return the team backend, if configured."""
        return self._team_backend

    def add_event_listener(self, handler) -> None:
        """Register an external event listener.

        Listeners receive every EventMessage from the transport,
        including self-published events, before any filtering.

        Args:
            handler: Async callable accepting an EventMessage.
        """
        self._event_listeners.append(handler)

    def remove_event_listener(self, handler) -> None:
        """Remove a previously registered event listener.

        Args:
            handler: The handler to remove.
        """
        try:
            self._event_listeners.remove(handler)
        except ValueError:
            pass

    async def has_team_member(self, member_name: str) -> bool:
        """Check whether a team member exists in the database."""
        if self._team_backend is None:
            return False
        return await self._team_backend.get_member(member_name) is not None

    def is_agent_ready(self) -> bool:
        """Whether the agent has been fully initialized."""
        return self._deep_agent is not None

    def is_agent_running(self) -> bool:
        """Whether the agent is in an active round."""
        return self._is_agent_running()

    def has_pending_interrupt(self) -> bool:
        """Whether the current session still has an unresolved tool interrupt."""
        session = self._deep_agent.loop_session if self._deep_agent else None
        if session is None:
            return False
        return session.get_state(INTERRUPTION_KEY) is not None

    async def start_agent(self, content: str) -> None:
        """Start a new agent round with the given content."""
        await self._start_agent(content)

    async def follow_up(self, content: str) -> None:
        """Feed content to the currently running agent."""
        if self._deep_agent is not None:
            team_logger.debug("[{}] follow_up: {:.120}", self._member_name() or "?", content)
            await self._deep_agent.follow_up(content)

    async def cancel_agent(self) -> None:
        """Cancel the running agent task."""
        team_logger.debug("[{}] cancel_agent requested", self._member_name() or "?")
        await self._cancel_agent()

    async def pause_polls(self) -> None:
        """Pause periodic polling in the coordination loop."""
        if self._coordination_loop:
            await self._coordination_loop.pause_polls()

    async def resume_polls(self) -> None:
        """Resume periodic polling in the coordination loop."""
        if self._coordination_loop:
            await self._coordination_loop.resume_polls()

    async def steer(self, content: str) -> None:
        """Steer instruction into the running agent."""
        if self._deep_agent is not None:
            team_logger.debug("[{}] steer: {:.120}", self._member_name() or "?", content)
            await self._deep_agent.steer(content)

    async def resume_interrupt(self, user_input) -> None:
        """Resume a pending HITL interrupt with structured input."""
        if not self._is_valid_interrupt_resume(user_input):
            team_logger.info("[{}] dropping stale interrupt resume input", self._member_name() or "?")
            return
        if self._is_agent_running():
            team_logger.info("[{}] queueing interrupt resume until current round completes", self._member_name() or "?")
            self._pending_interrupt_resumes.append(user_input)
            return
        await self._start_agent(user_input)

    # ------------------------------------------------------------------
    # BaseAgent abstract method: configure
    # ------------------------------------------------------------------

    def configure(self, spec: TeamAgentSpec, context: TeamRuntimeContext) -> "TeamAgent":
        """Satisfy BaseAgent.configure (sync, leader-only path)."""
        self._setup_infra(spec, context)
        self._setup_agent(spec, context)
        return self

    async def configure_team(self, spec: TeamAgentSpec, ctx: TeamRuntimeContext) -> "TeamAgent":
        """Configure the team agent.

        Team metadata and member roster are no longer pre-fetched
        here -- ``TeamRail`` reads them from the DB on demand via
        ``MtimeSectionCache`` so newly spawned members appear in the
        prompt without recreating the rail.
        """
        self._setup_infra(spec, ctx)
        self._setup_agent(spec, ctx)
        return self

    # ------------------------------------------------------------------
    # Team-specific configuration
    # ------------------------------------------------------------------

    def _resolve_agent_spec(self, spec: TeamAgentSpec, role: TeamRole):
        """Return the DeepAgentSpec for the given role, falling back to leader."""
        return spec.agents.get(role.value) or spec.agents["leader"]

    def _setup_infra(self, spec: TeamAgentSpec, ctx: TeamRuntimeContext) -> None:
        """Phase 1: set spec/context, create messager, workspace manager, register team tools."""
        self._spec = spec
        self._ctx = ctx

        messager_config = ctx.messager_config
        member_name = ctx.member_name
        if member_name and messager_config and messager_config.node_id != member_name:
            messager_config = messager_config.model_copy(update={"node_id": member_name})

        self._messager = create_messager(messager_config) if messager_config else None

        # Team shared workspace — create manager and ensure directory exists.
        if spec.workspace and spec.workspace.enabled:
            self._workspace_manager = self._create_workspace_manager(spec, ctx)

        self._tool_cards = self._register_team_tools(spec, ctx, self._messager)

        if ctx.role == TeamRole.LEADER:
            from openjiuwen.agent_teams.agent.model_allocator import ModelAllocator
            self._model_allocator = ModelAllocator(spec)

    def _create_workspace_manager(
        self, spec: TeamAgentSpec, ctx: TeamRuntimeContext,
    ) -> "TeamWorkspaceManager":
        """Create TeamWorkspaceManager and ensure the workspace directory exists.

        Args:
            spec: Team agent specification containing workspace config.
            ctx: Runtime context for resolving team_name.

        Returns:
            Configured TeamWorkspaceManager instance.
        """
        from openjiuwen.agent_teams.team_workspace.manager import TeamWorkspaceManager

        ws_config = spec.workspace
        team_name = (ctx.team_spec.team_name if ctx.team_spec else None) or spec.team_name
        ws_path = ws_config.root_path or str(
            Path.home() / ".openjiuwen" / ".agent_teams" / team_name / "team-workspace"
        )
        os.makedirs(ws_path, exist_ok=True)
        team_logger.info("Team workspace directory ensured at {}", ws_path)
        return TeamWorkspaceManager(
            config=ws_config,
            workspace_path=ws_path,
            team_name=team_name,
        )

    def _create_worktree_manager(self, spec: TeamAgentSpec) -> "WorktreeManager":
        """Create WorktreeManager for worktree isolation.

        Args:
            spec: Team agent specification containing worktree config.

        Returns:
            Configured WorktreeManager instance.
        """
        from openjiuwen.agent_teams.worktree.manager import WorktreeManager

        ws_root = self._workspace_manager.workspace_path if self._workspace_manager else None
        return WorktreeManager(
            config=spec.worktree,
            workspace_root=ws_root,
        )

    def _setup_agent(
        self,
        spec: TeamAgentSpec,
        ctx: TeamRuntimeContext,
    ) -> None:
        """Phase 2: build prompt, create DeepAgent, set up coordination."""
        agent_spec = self._resolve_agent_spec(spec, ctx.role)
        resolved_language = _resolve_language(agent_spec.language)
        self._role_policy = role_policy(ctx.role, language=resolved_language)
        member_name = ctx.member_name

        # Resolve workspace: fallback to leader's, adjust stable_base path.
        # ~/.openjiuwen/.agent_teams/{team_name}/workspaces/{member_name}_workspace/
        #
        # If the member is a predefined independent DeepAgent whose workspace
        # already exists at ~/.openjiuwen/{member_name}_workspace/, create a
        # symlink instead of a new directory so the agent keeps its identity.
        ws_spec = agent_spec.workspace or spec.agents.get("leader", agent_spec).workspace
        if ws_spec and ws_spec.stable_base:
            team_name = (ctx.team_spec.team_name if ctx.team_spec else None) or spec.team_name
            base = Path.home() / ".openjiuwen" / ".agent_teams" / team_name / "workspaces"
            team_ws_path = base / f"{member_name}_workspace"
            independent_ws = Path.home() / ".openjiuwen" / f"{member_name}_workspace"
            if independent_ws.is_dir() and not team_ws_path.exists():
                base.mkdir(parents=True, exist_ok=True)
                os.symlink(str(independent_ws), str(team_ws_path), target_is_directory=True)
            ws_spec = ws_spec.model_copy(update={"root_path": str(team_ws_path)})

        # Resolve model: member_model (allocated by leader) takes priority.
        model_config = ctx.member_model or agent_spec.model

        # Merge tools: team management tools + user-defined spec tools.
        merged_tools = list(self._tool_cards)
        if agent_spec.tools:
            merged_tools.extend(agent_spec.tools)

        # Build DeepAgent via DeepAgentSpec.build() with team overrides.
        # ``system_prompt`` is intentionally left untouched so DeepAgent's
        # default identity section stays in place; team-specific content
        # is injected later by ``TeamRail`` as discrete PromptSections.
        build_spec = agent_spec.model_copy(update={
            "card": self.card,
            "model": model_config,
            "workspace": ws_spec,
            "tools": merged_tools,
            "enable_task_loop": True,
        })
        self._deep_agent = build_spec.build()

        # Decompose team policy into ordered PromptSections via TeamRail.
        team_workspace_mount: str | None = None
        team_workspace_path: str | None = None
        if self._workspace_manager:
            resolved_team_name = (
                (ctx.team_spec.team_name if ctx.team_spec else None) or spec.team_name
            )
            team_workspace_mount = f".team/{resolved_team_name}/"
            team_workspace_path = self._workspace_manager.workspace_path

        self._deep_agent.add_rail(
            TeamRail(
                role=ctx.role,
                persona=ctx.persona,
                member_name=member_name,
                lifecycle=spec.lifecycle,
                language=resolved_language,
                predefined_team=bool(spec.predefined_members),
                base_prompt=agent_spec.system_prompt,
                team_workspace_mount=team_workspace_mount,
                team_workspace_path=team_workspace_path,
                team_backend=self._team_backend,
            )
        )

        from openjiuwen.agent_teams.agent.rails import FirstIterationGate
        self._first_iter_gate = FirstIterationGate()
        self._deep_agent.add_rail(self._first_iter_gate)

        # Mount team workspace and add transparent version-control rail.
        if self._workspace_manager:
            from openjiuwen.agent_teams.team_workspace.rails import TeamWorkspaceRail
            self._deep_agent.add_rail(
                TeamWorkspaceRail(self._workspace_manager, member_name or ""),
            )
            # Mount .team/ symlink into the agent's workspace directory.
            agent_ws_root = self._deep_agent.deep_config.workspace.root_path
            if agent_ws_root:
                self._workspace_manager.mount_into_workspace(agent_ws_root)

        is_coordinated_teammate = ctx.role == TeamRole.TEAMMATE and ctx.team_spec
        if is_coordinated_teammate and self._team_backend and self._messager:
            from openjiuwen.agent_teams.agent.rails import TeamToolApprovalRail
            approval_tools = agent_spec.approval_required_tools or []
            if approval_tools:
                self._deep_agent.add_rail(
                    TeamToolApprovalRail(
                        team_name=ctx.team_spec.team_name,
                        member_name=member_name or "",
                        db=self._team_backend.db,
                        messager=self._messager,
                        leader_member_name=ctx.team_spec.leader_member_name or "",
                        tool_names=approval_tools,
                    )
                )

        # Platform customizer: inject additional rails & tools (e.g. Claw adapter).
        if spec.agent_customizer and self._deep_agent:
            try:
                spec.agent_customizer(self._deep_agent)
            except Exception as exc:
                team_logger.warning(
                    "[{}] agent_customizer failed: {}", self._member_name() or "?", exc
                )

        # Teammate: member already exists in DB, create TeamMember now.
        # Leader: TeamMember is created in _on_teammate_created callback.
        if ctx.role == TeamRole.TEAMMATE and member_name and self._team_backend:
            self._team_member = TeamMember(
                member_name=member_name,
                team_name=self._team_backend.team_name,
                agent_card=self.card,
                db=self._team_backend.db,
                messager=self._messager,
                desc=ctx.persona,
            )

        from openjiuwen.agent_teams.agent.dispatcher import EventDispatcher
        self._dispatcher = EventDispatcher(self)
        self._coordination_loop = CoordinatorLoop(
            role=ctx.role,
            wake_callback=self._dispatcher.dispatch,
        )

    # ------------------------------------------------------------------
    # Role-based tool registration
    # ------------------------------------------------------------------

    def _register_team_tools(
        self,
        spec: TeamAgentSpec,
        ctx: TeamRuntimeContext,
        messager: Messager,
    ) -> List[ToolCard]:
        """Register role-appropriate team tools driven by permission sets."""
        from openjiuwen.agent_teams.spawn.shared_resources import get_shared_db
        from openjiuwen.agent_teams.tools.team_tools import create_team_tools
        from openjiuwen.agent_teams.schema.status import MemberMode

        team_name = (ctx.team_spec.team_name if ctx.team_spec else None) or "default"
        db = get_shared_db(ctx.db_config)

        is_leader = ctx.role == TeamRole.LEADER
        current_member_name = ctx.member_name or (
            ctx.team_spec.leader_member_name if ctx.team_spec else ""
        )

        agent_team = TeamBackend(
            team_name=team_name,
            member_name=current_member_name,
            is_leader=is_leader,
            db=db,
            messager=messager,
            teammate_mode=MemberMode(spec.teammate_mode),
            predefined_members=spec.predefined_members or None,
            model_config_allocator=self._model_allocator.allocate if self._model_allocator else None,
        )
        self._team_backend = agent_team
        self._task_manager = agent_team.task_manager
        self._message_manager = agent_team.message_manager

        exclude = {"spawn_member"} if spec.predefined_members else None
        lang = (ctx.team_spec.metadata.get("lang") if ctx.team_spec else None) or "cn"
        team_tools = create_team_tools(
            role=ctx.role.value,
            agent_team=agent_team,
            on_teammate_created=self._on_teammate_created,
            model_config_allocator=self._model_allocator.allocate if self._model_allocator else None,
            exclude_tools=exclude,
            lang=lang,
        )
        # Workspace metadata tool (lock management, version history).
        if self._workspace_manager:
            from openjiuwen.agent_teams.tools.locales import make_translator
            from openjiuwen.agent_teams.team_workspace.tools import WorkspaceMetaTool

            ws_t = make_translator(lang)
            team_tools.append(WorkspaceMetaTool(self._workspace_manager, ws_t))

        # Worktree isolation tools — teammate only.
        if not is_leader and spec.worktree and spec.worktree.enabled:
            from openjiuwen.agent_teams.tools.locales import make_translator
            from openjiuwen.agent_teams.worktree.tools import EnterWorktreeTool, ExitWorktreeTool

            self._worktree_manager = self._create_worktree_manager(spec)
            wt_t = make_translator(lang)
            team_tools.append(EnterWorktreeTool(self._worktree_manager, wt_t))
            team_tools.append(ExitWorktreeTool(self._worktree_manager, wt_t))
            # Eagerly create session state holder so asyncio.gather
            # tool calls share the same mutable object.
            from openjiuwen.agent_teams.worktree.session import init_session_state
            init_session_state()

        # Only in-process teammates share one global resource manager.
        if spec.spawn_mode == "inprocess":
            self._qualify_team_tool_ids(team_tools, team_name=team_name, member_name=current_member_name)

        # Best-effort registration with Runner's
        # resource manager.  When Runner has not been
        # bootstrapped (e.g. unit tests) we skip
        # silently -- the cards are still in
        # ability_manager for schema generation.
        try:
            Runner.resource_mgr.add_tool(team_tools)
        except Exception:
            team_logger.debug("Runner.resource_mgr not available, skipping tool registration")

        return [t.card for t in team_tools]

    @staticmethod
    def _qualify_team_tool_ids(team_tools: list[Tool], *, team_name: str, member_name: str) -> None:
        """Qualify team tool ids so each member gets distinct resource ids.

        Team tools share public names such as ``send_message`` across leader and
        teammates, but only in-process mode shares one global
        ``Runner.resource_mgr``. If the ids stay as ``team.send_message`` and
        similar, members can resolve another agent's tool instance. Qualifying
        ids keeps model-facing tool names stable while isolating the backing
        instances.
        """
        team_key = team_name or "default"
        member_key = member_name or "unknown"
        for tool in team_tools:
            if tool.card is None or not tool.card.id:
                continue
            qualified_id = f"{tool.card.id}.{team_key}.{member_key}"
            if tool.card.id != qualified_id:
                tool.card.id = qualified_id

    # ------------------------------------------------------------------
    # BaseAgent abstract methods: invoke / stream
    # ------------------------------------------------------------------

    async def invoke(self, inputs, session=None):
        """Execute via CoordinatorLoop-driven rounds.

        Feeds initial query as USER_INPUT event, collects
        all chunks, returns the last result.
        """
        team_logger.info("[{}] invoke start, role={}", self._member_name() or "?", self.role.value)
        self._stream_queue = asyncio.Queue()
        await self._start_coordination(session)
        try:
            await self._enqueue_user_input(inputs)
            await self._enqueue_mailbox_after_first_iteration()
            last_result = None
            while True:
                chunk = await self._stream_queue.get()
                if chunk is None:
                    break
                last_result = chunk
            return last_result
        finally:
            if self.lifecycle == "persistent":
                await self._pause_coordination()
                if self._team_member:
                    await self._team_member.update_status(MemberStatus.READY)
            else:
                await self._stop_coordination()
                if self._team_member:
                    await self._team_member.update_status(MemberStatus.SHUTDOWN)
            self._stream_queue = None

    async def stream(self, inputs, session=None, stream_modes=None):
        """Stream via CoordinatorLoop-driven rounds.

        Feeds initial query as USER_INPUT event, yields
        chunks from unified queue until sentinel (None).
        """
        team_logger.info("[{}] stream start, role={}", self._member_name() or "?", self.role.value)
        self._stream_queue = asyncio.Queue()
        await self._start_coordination(session)
        try:
            await self._enqueue_user_input(inputs)
            await self._enqueue_mailbox_after_first_iteration()
            while True:
                chunk = await self._stream_queue.get()
                if chunk is None:
                    break
                yield chunk
        finally:
            if self.lifecycle == "persistent":
                await self._pause_coordination()
                if self._team_member:
                    await self._team_member.update_status(MemberStatus.READY)
            else:
                await self._stop_coordination()
                if self._team_member:
                    await self._team_member.update_status(MemberStatus.SHUTDOWN)
            self._stream_queue = None

    async def interact(self, message: str) -> None:
        """Inject user input into CoordinatorLoop as USER_INPUT event."""
        if self._coordination_loop is None:
            return
        await self._coordination_loop.enqueue(
            InnerEventMessage(
                event_type=InnerEventType.USER_INPUT,
                payload={"content": message},
            )
        )

    # ------------------------------------------------------------------
    # Coordination lifecycle helpers
    # ------------------------------------------------------------------

    async def _start_coordination(
        self,
        session=None,
    ) -> None:
        """Start the coordination loop."""
        if self._coordination_loop is None:
            return
        member_name = self._member_name() or "?"
        team_logger.info("[{}] coordination starting", member_name)
        self._session_id = session.get_session_id() if session else None
        if self._session_id:
            from openjiuwen.agent_teams.spawn.context import set_session_id
            set_session_id(self._session_id)
        from openjiuwen.core.common.logging.utils import set_member_id
        set_member_id(member_name)
        if session is not None:
            if isinstance(session, AgentTeamSession):
                self._team_session = session
            else:
                team_logger.warning(
                    "[{}] TeamAgent expects AgentTeamSession; got {}. "
                    "Please invoke via Runner.run_agent_team_streaming.",
                    member_name,
                    type(session).__name__,
                )
        # Persist leader config to session for full-restart recovery
        if session and self._spec and self.role == TeamRole.LEADER:
            self._persist_leader_config(session)
        # Eagerly initialize the DB so TeamRail's before_model_call probe
        # functions never hit an uninitialized database.  Must run after
        # set_session_id() so create_cur_session_tables uses the right names.
        if self._team_backend:
            await self._team_backend.db.initialize()
        # Async workspace initialization (git init + artifact dirs), idempotent.
        if self._workspace_manager and not self._workspace_initialized:
            await self._workspace_manager.initialize(
                remote_url=self._spec.workspace.remote_url if self._spec and self._spec.workspace else None,
            )
            self._workspace_initialized = True

        await self._update_status(MemberStatus.READY)
        if not self._coordination_loop.is_running:
            await self._coordination_loop.start()
        if self._messager:
            team_name = self._team_name()
            if team_name and not self._subscribed_topics:
                await self._subscribe_transport(team_name)

    async def _enqueue_mailbox_after_first_iteration(self) -> None:
        """Wait for agent's first iteration, then enqueue POLL_MAILBOX.

        Skipped for leader — leader has no pre-existing unread messages at startup.
        """
        if self.role == TeamRole.LEADER:
            return
        if self._first_iter_gate is None or self._coordination_loop is None:
            return
        await self._first_iter_gate.wait()
        await self._coordination_loop.enqueue(
            InnerEventMessage(event_type=InnerEventType.POLL_MAILBOX),
        )

    async def _enqueue_user_input(self, inputs: Any) -> None:
        """Extract query from inputs and enqueue as USER_INPUT event."""
        query = inputs.get("query", "") if isinstance(inputs, dict) else inputs
        if self._coordination_loop is None:
            return
        await self._coordination_loop.enqueue(
            InnerEventMessage(
                event_type=InnerEventType.USER_INPUT,
                payload={"content": query},
            )
        )

    async def _pause_coordination(self) -> None:
        """Pause coordination for persistent teams.

        Publishes TEAM_STANDBY so teammates pause their polls,
        then stops the leader's own loop without killing
        teammate processes.
        """
        team_logger.info("[{}] coordination pausing (persistent)", self._member_name() or "?")
        # Signal teammates to pause polls
        if self._messager and self.role == TeamRole.LEADER:
            from openjiuwen.agent_teams.schema.events import (
                EventMessage,
                TeamStandbyEvent,
                TeamTopic,
            )
            from openjiuwen.agent_teams.spawn.context import get_session_id
            team_name = self._team_name()
            if team_name:
                try:
                    await self._messager.publish(
                        topic_id=TeamTopic.TEAM.build(get_session_id(), team_name),
                        message=EventMessage.from_event(TeamStandbyEvent(team_name=team_name)),
                    )
                except Exception as e:
                    team_logger.error("Failed to publish TEAM_STANDBY: {}", e)
        await self._unsubscribe_transport()
        if self._coordination_loop:
            await self._coordination_loop.stop()
        self._close_stream()
        self._team_session = None

    async def _stop_coordination(self) -> None:
        """Stop the coordination loop, send sentinel, and unsubscribe."""
        team_logger.info("[{}] coordination stopping", self._member_name() or "?")
        await self._unsubscribe_transport()
        # Shut down all spawned teammate processes
        for mid, handle in list(self._spawned_handles.items()):
            try:
                await handle.shutdown()
            except Exception as e:
                team_logger.error("Error shutting down teammate {}: {}", mid, e)
        self._spawned_handles.clear()
        if self._coordination_loop is None:
            return
        await self._coordination_loop.stop()
        self._close_stream()
        self._team_session = None

    def _close_stream(self) -> None:
        """Send sentinel to signal stream consumers that no more data is coming."""
        if self._stream_queue is not None:
            self._stream_queue.put_nowait(None)

    async def _subscribe_transport(self, team_name: str) -> None:
        """Subscribe to all TeamTopic channels on the transport."""
        if not self._messager or not self._coordination_loop:
            return
        from openjiuwen.agent_teams.spawn.context import get_session_id
        from openjiuwen.agent_teams.schema.events import EventMessage, TeamTopic

        local_member_name = self._member_name() or ""

        async def _filter_self(event: EventMessage) -> None:
            for listener in self._event_listeners:
                try:
                    await listener(event)
                except Exception as e:
                    team_logger.error("Event listener error: {}", e)
            if local_member_name and event.sender_id == local_member_name:
                team_logger.debug("ignoring self-published event: {}", event.event_type)
                return
            await self._coordination_loop.enqueue(event)

        session_id = get_session_id()
        await self._messager.register_direct_message_handler(
            self._coordination_loop.enqueue,
        )
        for topic in TeamTopic:
            topic_str = topic.build(session_id, team_name)
            await self._messager.subscribe(
                topic_str,
                _filter_self,
            )
            self._subscribed_topics.append(topic_str)

    async def _unsubscribe_transport(self) -> None:
        """Unsubscribe from all topics and unregister P2P handler."""
        if not self._messager:
            return
        try:
            await self._messager.unregister_direct_message_handler()
        except Exception:
            team_logger.debug("failed to unregister direct message handler during cleanup")
        for topic in self._subscribed_topics:
            try:
                await self._messager.unsubscribe(topic)
            except Exception:
                team_logger.debug("failed to unsubscribe topic {} during cleanup", topic)
        self._subscribed_topics.clear()

    def _is_agent_running(self) -> bool:
        """Check if the DeepAgent is currently in an active round."""
        return self._agent_task is not None and not self._agent_task.done()

    async def _cancel_agent(self) -> None:
        """Cancel the running agent task and update execution status."""
        await self._update_execution(ExecutionStatus.CANCEL_REQUESTED)
        if self._agent_task and not self._agent_task.done():
            await self._update_execution(ExecutionStatus.CANCELLING)
            self._agent_task.cancel()

    async def _start_agent(
        self,
        initial_message: Any,
    ) -> None:
        """Run one round of DeepAgent via Runner in background.

        Chunks are pushed to _stream_queue for the outer
        stream()/invoke() to yield.
        """
        if self._deep_agent is None or self._stream_queue is None:
            return
        preview = initial_message if isinstance(initial_message, str) else type(initial_message).__name__
        team_logger.info("[{}] start_agent: {:.120}", self._member_name() or "?", str(preview))
        self._agent_task = asyncio.create_task(
            self._run_one_round(initial_message),
        )

    async def _update_status(self, status: MemberStatus) -> None:
        """Update member status if this agent belongs to a team."""
        if self._team_member:
            await self._team_member.update_status(status)

    async def _update_execution(self, status: ExecutionStatus) -> None:
        """Update member execution status if this agent belongs to a team."""
        if self._team_member:
            await self._team_member.update_execution_status(status)

    async def _run_one_round(
        self,
        message: Any,
    ) -> None:
        """Execute one DeepAgent stream round via Runner."""
        await self._update_status(MemberStatus.BUSY)
        try:
            await self._execute_round(message)
            await self._update_status(MemberStatus.READY)
        except BaseException as e:
            team_logger.error("Failed to execute deep agent, {}", e, exc_info=True)
            await self._update_status(MemberStatus.ERROR)
        finally:
            self._agent_task = None
            next_resume = self._dequeue_valid_interrupt_resume()
            if next_resume is not None and self._stream_queue is not None:
                await self._start_agent(next_resume)
            else:
                await self._wake_mailbox_if_interrupt_cleared()
                if self._team_member and await self._team_member.status() == MemberStatus.SHUTDOWN_REQUESTED:
                    self._close_stream()

    async def _execute_round(
        self,
        message: Any,
    ) -> None:
        """Execute the agent invocation via Runner.

        Derives a fresh AgentSession from the team session each round so that
        pre_run/post_run lifecycle (checkpoint recover/save) fires correctly.
        Leader uses token-level streaming; teammates use invoke for simpler execution.
        """
        await self._update_execution(ExecutionStatus.STARTING)
        await self._update_execution(ExecutionStatus.RUNNING)
        try:
            inputs = {"query": message}
            async for chunk in Runner.run_agent_streaming(
                self._deep_agent, inputs, session=self._session_id
            ):
                if self._stream_queue is not None:
                    await self._stream_queue.put(chunk)
            await self._update_execution(ExecutionStatus.COMPLETING)
            await self._update_execution(ExecutionStatus.COMPLETED)
        except asyncio.CancelledError:
            await self._update_execution(ExecutionStatus.CANCELLED)
            raise
        except asyncio.TimeoutError:
            await self._update_execution(ExecutionStatus.TIMED_OUT)
            raise
        except Exception as e:
            team_logger.error("DeepAgent round error: %s", e)
            await self._update_execution(ExecutionStatus.FAILED)
            raise
        finally:
            await self._update_execution(ExecutionStatus.IDLE)

    def _is_valid_interrupt_resume(self, user_input: Any) -> bool:
        """Return True when the supplied InteractiveInput still targets a pending interrupt."""
        if not isinstance(user_input, InteractiveInput):
            return False
        session = self._deep_agent.loop_session if self._deep_agent else None
        if session is None:
            return False
        state = session.get_state(INTERRUPTION_KEY)
        if state is None:
            return False
        interrupted = getattr(state, "interrupted_tools", {}) or {}
        pending_ids = set()
        for entry in interrupted.values():
            requests = getattr(entry, "interrupt_requests", {}) or {}
            pending_ids.update(requests.keys())
        if not pending_ids:
            return False
        resume_ids = set(user_input.user_inputs.keys())
        return bool(resume_ids) and resume_ids.issubset(pending_ids)

    def _dequeue_valid_interrupt_resume(self) -> Optional[InteractiveInput]:
        """Pop the next still-valid queued interrupt resume input."""
        while self._pending_interrupt_resumes:
            candidate = self._pending_interrupt_resumes.pop(0)
            if self._is_valid_interrupt_resume(candidate):
                return candidate
        return None

    async def _wake_mailbox_if_interrupt_cleared(self) -> None:
        """Nudge mailbox processing once an interrupt gate has been cleared."""
        if self.role != TeamRole.TEAMMATE:
            return
        if self.has_pending_interrupt():
            return
        if self._coordination_loop is None:
            return
        await self._coordination_loop.enqueue(
            InnerEventMessage(event_type=InnerEventType.POLL_MAILBOX),
        )

    def _member_name(self) -> Optional[str]:
        """Return the current agent's member_name."""
        return self._ctx.member_name if self._ctx else None

    def _team_name(self) -> Optional[str]:
        """Return the current team_name."""
        if self._ctx and self._ctx.team_spec:
            return self._ctx.team_spec.team_name
        return None

    # ------------------------------------------------------------------
    # Rail / callback proxies to internal DeepAgent
    # ------------------------------------------------------------------

    async def register_rail(self, rail: AgentRail) -> "TeamAgent":
        """Proxy rail registration to the internal DeepAgent."""
        if self._deep_agent is not None:
            await self._deep_agent.register_rail(rail)
        return self

    async def unregister_rail(self, rail: AgentRail) -> "TeamAgent":
        """Proxy rail unregistration to the internal DeepAgent."""
        if self._deep_agent is not None:
            await self._deep_agent.unregister_rail(rail)
        return self

    # ------------------------------------------------------------------
    # Spawn / clone helpers (unchanged logic)
    # ------------------------------------------------------------------

    def build_spawn_payload(
        self,
        ctx: TeamRuntimeContext,
        *,
        initial_message: Optional[str] = None,
    ) -> dict[str, Any]:
        """Build the payload used to bootstrap one teammate."""
        team_spec = self.team_spec
        member_transport = self._build_member_messager_config(ctx.member_name)
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
        """Build runtime context for one teammate from a predefined member spec."""
        return TeamRuntimeContext(
            role=member_spec.role_type,
            member_name=member_spec.member_name,
            persona=member_spec.persona,
            team_spec=self._ctx.team_spec,
            messager_config=self._build_member_messager_config(member_spec.member_name),
            db_config=self._ctx.db_config,
        )

    def _build_member_messager_config(self, member_name: str):
        if self._ctx is None or self._ctx.messager_config is None:
            return None
        leader_cfg = self._ctx.messager_config
        meta = self._spec.metadata if self._spec else {}
        base_port = meta.get("teammate_base_port", 16000)
        port_offset = meta.get("teammate_port_offset", 10)

        # Reuse cached port on restart; assign new port on first spawn
        mid = member_name
        if mid in self._member_port_map:
            port_base = self._member_port_map[mid]
        else:
            port_base = base_port + self._teammate_port_counter * port_offset
            self._teammate_port_counter += 1
            self._member_port_map[mid] = port_base

        updates: Dict[str, Any] = {
            "node_id": member_name,
            "direct_addr": f"tcp://127.0.0.1:{port_base}",
            "pubsub_publish_addr": leader_cfg.pubsub_publish_addr,
            "pubsub_subscribe_addr": leader_cfg.pubsub_subscribe_addr,
        }
        # Teammates never run the pubsub proxy — only connect to the leader's.
        metadata = dict(leader_cfg.metadata)
        metadata.pop("pubsub_bind", None)
        updates["metadata"] = metadata
        return leader_cfg.model_copy(update=updates)

    def build_spawn_config(self, ctx: TeamRuntimeContext) -> SpawnAgentConfig:
        """Build JSON-safe spawn config for one teammate process."""
        context = ctx
        logging_config = self._build_member_logging_config(ctx.member_name or "", ctx.member_name or "")
        return SpawnAgentConfig(
            agent_kind=SpawnAgentKind.TEAM_AGENT,
            runner_config=serialize_runner_config(Runner.get_config()),
            logging_config=logging_config,
            session_id=None,
            payload={
                "spec": self._spec.model_dump(mode="json"),
                "context": context.model_dump(mode="json"),
            },
        )

    @staticmethod
    def _build_member_logging_config(member_name: str, name: str) -> dict[str, Any]:
        """Build a logging config with member-specific log file paths to avoid overwrites."""
        from openjiuwen.core.common.logging.log_config import get_log_config_snapshot

        config = get_log_config_snapshot()
        member_tag = member_name or name
        sinks = config.get("sinks", {})
        for sink in sinks.values():
            target = sink.get("target")
            if not isinstance(target, str) or target in ("stdout", "stderr"):
                continue
            # Insert member tag into file path: ./logs/run/jiuwen.log -> ./logs/run/teammates/{tag}/jiuwen.log
            parts = target.rsplit("/", 1)
            if len(parts) == 2:
                sink["target"] = f"{parts[0]}/teammates/{member_tag}/{parts[1]}"
            else:
                sink["target"] = f"teammates/{member_tag}/{target}"
        return config

    @classmethod
    async def from_spawn_payload(cls, payload: Dict[str, Any]) -> "TeamAgent":
        """Rebuild a TeamAgent from JSON-safe spawn payload dict."""
        from openjiuwen.agent_teams.schema.blueprint import TeamAgentSpec as _Spec
        from openjiuwen.agent_teams.schema.team import TeamRuntimeContext as _Ctx
        from openjiuwen.core.single_agent.schema.agent_card import AgentCard

        spec = _Spec.model_validate(payload["spec"])
        context = _Ctx.model_validate(payload["context"])

        agent_spec = spec.agents.get(context.role.value) or spec.agents["leader"]
        team_name = (context.team_spec.team_name if context.team_spec else None) or spec.team_name
        card_id = f"{team_name}_{context.member_name}" if context.member_name else "unknown"
        card = agent_spec.card or AgentCard(
            id=card_id,
            name=context.member_name or "unknown",
            description=f"Teammate: {context.persona}" if context.persona else "Teammate",
        )
        agent = cls(card)
        await agent.configure_team(spec, context)
        return agent

    def _init_leader_member(self, member_name: str) -> None:
        """Initialize TeamMember for the leader after DB registration."""
        self._team_member = TeamMember(
            member_name=member_name,
            team_name=self._team_backend.team_name,
            agent_card=self.card,
            db=self._team_backend.db,
            messager=self._messager,
            desc=self._ctx.persona,
        )

    async def _on_teammate_created(self, teammate_id: str):
        team_logger.info("[{}] on_teammate_created: {}", self._member_name() or "?", teammate_id)
        if teammate_id == self._member_name():
            self._init_leader_member(teammate_id)
            return
        ctx = await self._build_context_from_db(teammate_id)
        if ctx is None:
            return
        teammate = await self._team_backend.get_member(teammate_id)
        await self.spawn_teammate(
            ctx,
            initial_message=teammate.prompt if teammate else None,
            session=self._session_id,
            spawn_config=SpawnConfig(health_check_timeout=30, health_check_interval=50),
        )

    async def _build_context_from_db(self, member_name: str) -> Optional[TeamRuntimeContext]:
        """Build a TeamRuntimeContext directly from DB record."""
        teammate = await self._team_backend.get_member(member_name)
        if teammate is None:
            team_logger.error("Teammate {} not found in database", member_name)
            return None

        member_model = self._deserialize_member_model(teammate.model_config_json)

        return TeamRuntimeContext(
            role=TeamRole.TEAMMATE,
            member_name=teammate.member_name,
            persona=teammate.desc or "",
            team_spec=self._ctx.team_spec,
            messager_config=self._build_member_messager_config(teammate.member_name),
            db_config=self._ctx.db_config,
            member_model=member_model,
        )

    @staticmethod
    def _deserialize_member_model(json_str: Optional[str]) -> Optional["TeamModelConfig"]:
        """Deserialize model_config_json from DB."""
        if not json_str:
            return None
        from openjiuwen.agent_teams.schema.deep_agent_spec import TeamModelConfig
        return TeamModelConfig.model_validate_json(json_str)

    async def spawn_teammate(
        self,
        ctx: TeamRuntimeContext,
        *,
        initial_message: Optional[str] = None,
        session: Optional[Any] = None,
        spawn_config: Optional[SpawnConfig] = None,
    ):
        """Spawn one teammate via subprocess or in-process coroutine.

        The returned handle is tracked internally and an on_unhealthy
        callback is registered so the leader can auto-restart the
        teammate when consecutive health checks fail.
        """
        member_name = ctx.member_name
        team_logger.info("[{}] spawning teammate: {}", self._member_name() or "?", member_name)

        if self._spec and self._spec.spawn_mode == "inprocess":
            from openjiuwen.agent_teams.spawn.inprocess_spawn import inprocess_spawn

            handle = await inprocess_spawn(
                team_agent=self,
                ctx=ctx,
                initial_message=initial_message,
                session_id=self._session_id or session,
            )
        else:
            handle = await Runner.spawn_agent(
                self.build_spawn_config(ctx),
                self.build_spawn_payload(
                    ctx,
                    initial_message=initial_message,
                ),
                session=session,
                spawn_config=spawn_config,
            )

        self._spawned_handles[member_name] = handle

        def _trigger_unhealthy_recovery() -> asyncio.Task:
            return asyncio.ensure_future(self._on_teammate_unhealthy(member_name))

        handle.on_unhealthy = _trigger_unhealthy_recovery
        return handle

    # ------------------------------------------------------------------
    # Fault tolerance: cleanup, restart, recover
    # ------------------------------------------------------------------

    async def _on_teammate_unhealthy(self, member_name: str) -> None:
        """Handle a teammate whose process has become unhealthy.

        Cleans up the dead process, marks the member as RESTARTING
        in the database, and attempts to re-spawn.
        """
        team_logger.warning("Teammate {} detected as unhealthy, initiating restart", member_name)
        await self._cleanup_teammate(member_name)
        if self._team_backend:
            await self._team_backend.db.update_member_status(member_name, self._team_name(),
                                                             MemberStatus.RESTARTING.value)
        await self._restart_teammate(member_name)

    async def _cleanup_teammate(self, member_name: str) -> None:
        """Clean up resources for a dead/dying teammate process."""
        handle = self._spawned_handles.pop(member_name, None)
        if handle is None:
            return
        try:
            await handle.stop_health_check()
            if handle.is_alive:
                await handle.force_kill()
        except Exception as e:
            team_logger.error("Error cleaning up teammate {}: {}", member_name, e)

    async def _restart_teammate(self, member_name: str, max_retries: int = 3) -> bool:
        """Restart a teammate process, recovering config from DB.

        Retries with exponential backoff. Publishes MemberRestartedEvent
        on success; marks ERROR on exhaustion.
        """
        ctx = await self._build_context_from_db(member_name)
        if ctx is None:
            team_logger.error("Cannot recover spawn config for {}", member_name)
            return False

        teammate = await self._team_backend.get_member(member_name)
        initial_message = teammate.prompt if teammate else None
        spawn_config = SpawnConfig(health_check_timeout=30, health_check_interval=50)

        for attempt in range(1, max_retries + 1):
            try:
                team_logger.info("Restarting teammate {} (attempt {}/{})", member_name, attempt, max_retries)
                await self.spawn_teammate(
                    ctx,
                    initial_message=initial_message,
                    spawn_config=spawn_config,
                )
                await self._publish_restart_event(member_name, attempt)
                team_logger.info("Teammate {} restarted successfully", member_name)
                return True
            except Exception as e:
                team_logger.error("Restart attempt {} for {} failed: {}", attempt, member_name, e)
                if attempt < max_retries:
                    await asyncio.sleep(2 ** attempt)

        # All retries exhausted
        if self._team_backend:
            await self._team_backend.db.update_member_status(member_name, self._team_name(), MemberStatus.ERROR.value)
        return False

    async def _publish_restart_event(self, member_name: str, restart_count: int) -> None:
        """Publish MemberRestartedEvent on the team topic."""
        if not self._messager or not self._team_backend:
            return
        from openjiuwen.agent_teams.spawn.context import get_session_id
        from openjiuwen.agent_teams.schema.events import (
            EventMessage,
            MemberRestartedEvent,
            TeamTopic,
        )
        try:
            await self._messager.publish(
                topic_id=TeamTopic.TEAM.build(get_session_id(), self._team_backend.team_name),
                message=EventMessage.from_event(MemberRestartedEvent(
                    team_name=self._team_backend.team_name,
                    member_name=member_name,
                    restart_count=restart_count,
                )),
            )
        except Exception as e:
            team_logger.error("Failed to publish restart event for {}: {}", member_name, e)

    async def resume_for_new_session(self, session) -> None:
        """Prepare a persistent team for a new session.

        Switches the session context, creates new dynamic tables for
        the new session (tasks, messages), and persists leader config.
        Existing teammate processes and DB member records are retained.

        Args:
            session: The new session to attach to.
        """
        from openjiuwen.agent_teams.spawn.context import set_session_id
        self._session_id = session.get_session_id()
        set_session_id(self._session_id)
        self._team_session = session if isinstance(session, AgentTeamSession) else None

        if self._team_backend:
            await self._team_backend.db.create_cur_session_tables()

        if self._spec and self.role == TeamRole.LEADER:
            self._persist_leader_config(session)

    async def recover_team(self) -> list[str]:
        """Re-launch all non-shutdown teammates from database state.

        Called after the leader has been reconstructed (e.g. via
        ``recover_from_session``) to bring the full team back online.
        """
        if not self._team_backend:
            return []

        team_logger.info("[{}] recovering team", self._member_name() or "?")
        all_members = await self._team_backend.list_members()
        leader_member_name = self._member_name()
        restarted: list[str] = []

        for member in all_members:
            if member.member_name == leader_member_name:
                continue
            if member.status == MemberStatus.SHUTDOWN.value:
                continue
            await self._team_backend.db.update_member_status(
                member.member_name, self._team_name(), MemberStatus.RESTARTING.value,
            )
            if await self._restart_teammate(member.member_name):
                restarted.append(member.member_name)

        return restarted

    # ------------------------------------------------------------------
    # Leader config persistence / recovery
    # ------------------------------------------------------------------

    def _persist_leader_config(self, session) -> None:
        """Persist leader's spec + context to session state for recovery."""
        session.update_state({
            "spec": self._spec.model_dump(mode="json"),
            "context": self._ctx.model_dump(mode="json"),
            "team_name": self._team_name(),
        })

    @classmethod
    def recover_from_session(cls, session) -> "TeamAgent":
        """Recover a leader TeamAgent from a persisted session.

        Used in full-restart scenario: all processes exited, the caller
        re-creates the leader from session state.

        Args:
            session: AgentTeamSession with persisted leader config in state.
        """
        from openjiuwen.agent_teams.schema.blueprint import TeamAgentSpec as _Spec
        from openjiuwen.agent_teams.schema.team import TeamRuntimeContext as _Ctx
        from openjiuwen.core.single_agent.schema.agent_card import AgentCard

        state = session.get_state()
        spec_data = state.get("spec")
        if spec_data is None:
            raise ValueError("No leader spec found in session state")
        spec = _Spec.model_validate(spec_data)
        context = _Ctx.model_validate(state["context"])

        agent_spec = spec.agents.get(context.role.value) or spec.agents["leader"]
        team_name = (context.team_spec.team_name if context.team_spec else None) or spec.team_name
        card_id = f"{team_name}_{context.member_name}" if context.member_name else "leader"
        card = agent_spec.card or AgentCard(
            id=card_id,
            name=context.member_name or "leader",
        )
        agent = cls(card)
        agent.configure(spec, context)
        return agent


__all__ = ["TeamAgent"]
