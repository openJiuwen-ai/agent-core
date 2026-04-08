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
from openjiuwen.agent_teams.agent.policy import (
    build_system_prompt,
    role_policy,
)
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
from openjiuwen.core.single_agent.base import BaseAgent
from openjiuwen.core.single_agent.interrupt.state import INTERRUPTION_KEY
from openjiuwen.core.single_agent.rail.base import AgentRail
from openjiuwen.harness import create_deep_agent
from openjiuwen.harness.deep_agent import DeepAgent
from openjiuwen.harness.rails.filesystem_rail import FileSystemRail


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
        self._session = None
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
    def member_id(self) -> Optional[str]:
        """Return the current agent's member_id."""
        return self._member_id()

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

    async def has_team_member(self, member_id: str) -> bool:
        """Check whether a team member exists in the database."""
        if self._team_backend is None:
            return False
        return await self._team_backend.get_member(member_id) is not None

    def is_agent_ready(self) -> bool:
        """Whether the agent has been fully initialized."""
        return self._deep_agent is not None

    def is_agent_running(self) -> bool:
        """Whether the agent is in an active round."""
        return self._is_agent_running()

    def has_pending_interrupt(self) -> bool:
        """Whether the current session still has an unresolved tool interrupt."""
        if self._session is None:
            return False
        return self._session.get_state(INTERRUPTION_KEY) is not None

    async def start_agent(self, content: str) -> None:
        """Start a new agent round with the given content."""
        await self._start_agent(content, self._session)

    async def follow_up(self, content: str) -> None:
        """Feed content to the currently running agent."""
        if self._deep_agent is not None:
            team_logger.debug("[{}] follow_up: {:.120}", self._member_id() or "?", content)
            await self._deep_agent.follow_up(content)

    async def cancel_agent(self) -> None:
        """Cancel the running agent task."""
        team_logger.debug("[{}] cancel_agent requested", self._member_id() or "?")
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
            team_logger.debug("[{}] steer: {:.120}", self._member_id() or "?", content)
            await self._deep_agent.steer(content, self._session)

    async def resume_interrupt(self, user_input) -> None:
        """Resume a pending HITL interrupt with structured input."""
        if not self._is_valid_interrupt_resume(user_input):
            team_logger.info("[{}] dropping stale interrupt resume input", self._member_id() or "?")
            return
        if self._is_agent_running():
            team_logger.info("[{}] queueing interrupt resume until current round completes", self._member_id() or "?")
            self._pending_interrupt_resumes.append(user_input)
            return
        await self._start_agent(user_input, self._session)

    # ------------------------------------------------------------------
    # BaseAgent abstract method: configure
    # ------------------------------------------------------------------

    def configure(self, spec: TeamAgentSpec, context: TeamRuntimeContext) -> "TeamAgent":
        """Satisfy BaseAgent.configure (sync, leader-only path)."""
        self._setup_infra(spec, context)
        self._setup_agent(spec, context)
        return self

    async def configure_team(self, spec: TeamAgentSpec, ctx: TeamRuntimeContext) -> "TeamAgent":
        """Configure with team context fetched from DB."""
        self._setup_infra(spec, ctx)
        team_info, team_members = await self._fetch_team_context()
        self._setup_agent(spec, ctx, team_info=team_info, team_members=team_members)
        return self

    # ------------------------------------------------------------------
    # Team-specific configuration
    # ------------------------------------------------------------------

    def _resolve_agent_spec(self, spec: TeamAgentSpec, role: TeamRole):
        """Return the DeepAgentSpec for the given role, falling back to leader."""
        return spec.agents.get(role.value) or spec.agents["leader"]

    def _setup_infra(self, spec: TeamAgentSpec, ctx: TeamRuntimeContext) -> None:
        """Phase 1: set spec/context, create messager, register team tools."""
        self._spec = spec
        self._ctx = ctx

        messager_config = ctx.messager_config
        member_id = ctx.member_id
        if member_id and messager_config and messager_config.node_id != member_id:
            messager_config = messager_config.model_copy(update={"node_id": member_id})

        if messager_config and messager_config.backend == "team_runtime":
            from openjiuwen.agent_teams.spawn.shared_resources import get_shared_runtime

            self._messager = create_messager(messager_config, runtime=get_shared_runtime())
        elif messager_config:
            self._messager = create_messager(messager_config)
        else:
            self._messager = None

        self._tool_cards = self._register_team_tools(spec, ctx, self._messager)

    def _setup_agent(
        self,
        spec: TeamAgentSpec,
        ctx: TeamRuntimeContext,
        *,
        team_info: dict[str, Any] | None = None,
        team_members: list[dict[str, str]] | None = None,
    ) -> None:
        """Phase 2: build prompt, create DeepAgent, set up coordination."""
        agent_spec = self._resolve_agent_spec(spec, ctx.role)
        language = agent_spec.language or "cn"
        self._role_policy = role_policy(ctx.role, language=language)

        workspace_spec = agent_spec.workspace or spec.agents.get("leader", agent_spec).workspace
        workspace_obj = workspace_spec.build() if workspace_spec else None
        if workspace_obj and workspace_spec and workspace_spec.stable_base:
            workspace_obj.root_path = str(
                Path(os.getcwd()) / ".agent_teams" / "workspaces"
            )
        model = agent_spec.model.build() if agent_spec.model else None
        member_id = ctx.member_id

        from openjiuwen.agent_teams.agent.rails import FirstIterationGate
        fs_rail = FileSystemRail()
        self._first_iter_gate = FirstIterationGate()
        rails = [fs_rail, self._first_iter_gate]
        is_coordinated_teammate = ctx.role == TeamRole.TEAMMATE and ctx.team_spec
        if is_coordinated_teammate and self._team_backend and self._messager:
            from openjiuwen.agent_teams.agent.rails import TeamToolApprovalRail
            approval_tools = agent_spec.approval_required_tools or []
            if approval_tools:
                rails.append(
                    TeamToolApprovalRail(
                        team_id=ctx.team_spec.team_id,
                        member_id=member_id or "",
                        db=self._team_backend.db,
                        messager=self._messager,
                        leader_id=ctx.team_spec.leader_member_id or "",
                        tool_names=approval_tools,
                    )
                )

        system_prompt = build_system_prompt(
            role=ctx.role,
            persona=ctx.persona,
            domain=ctx.domain,
            base_prompt=agent_spec.system_prompt,
            team_info=team_info,
            team_members=team_members,
            member_id=member_id,
            lifecycle=spec.lifecycle,
            language=language,
            predefined_team=bool(spec.predefined_members),
        )
        team_logger.info("当前成员系统提示词：\n{}", system_prompt)

        self._deep_agent = create_deep_agent(
            model=model,
            card=self.card,
            system_prompt=system_prompt,
            tools=self._tool_cards,
            rails=rails,
            workspace=workspace_obj,
            enable_task_loop=True,
            max_iterations=agent_spec.max_iterations,
            completion_timeout=agent_spec.completion_timeout,
        )

        # Teammate: member already exists in DB, create TeamMember now.
        # Leader: TeamMember is created in _on_teammate_created callback.
        if ctx.role == TeamRole.TEAMMATE and member_id and self._team_backend:
            self._team_member = TeamMember(
                member_id=member_id,
                team_id=self._team_backend.team_id,
                name=ctx.member_spec.name if ctx.member_spec else member_id,
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

    async def _fetch_team_context(
        self,
    ) -> tuple[dict[str, Any] | None, list[dict[str, str]] | None]:
        """Fetch team_info and team_members from DB via _team_backend."""
        if not self._team_backend:
            return None, None

        team_info_obj = await self._team_backend.get_team_info()
        team_info: dict[str, Any] | None = None
        if team_info_obj:
            team_info = {
                "name": team_info_obj.name,
                "desc": team_info_obj.desc or "",
            }

        all_members = await self._team_backend.list_members()
        team_members: list[dict[str, str]] | None = None
        if all_members:
            team_members = [
                {"name": m.name, "member_id": m.member_id, "desc": m.desc or ""}
                for m in all_members
            ]

        return team_info, team_members

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

        team_id = (ctx.team_spec.team_id if ctx.team_spec else None) or "default"
        db = get_shared_db(ctx.db_config)

        is_leader = ctx.role == TeamRole.LEADER
        current_member_id = ctx.member_id or (
            ctx.team_spec.leader_member_id if ctx.team_spec else ""
        )

        agent_team = TeamBackend(
            team_id=team_id,
            member_id=current_member_id,
            is_leader=is_leader,
            db=db,
            messager=messager,
            teammate_mode=MemberMode(spec.teammate_mode),
            predefined_members=spec.predefined_members or None,
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
            exclude_tools=exclude,
            lang=lang,
        )

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

    # ------------------------------------------------------------------
    # BaseAgent abstract methods: invoke / stream
    # ------------------------------------------------------------------

    async def invoke(self, inputs, session=None):
        """Execute via CoordinatorLoop-driven rounds.

        Feeds initial query as USER_INPUT event, collects
        all chunks, returns the last result.
        """
        team_logger.info("[{}] invoke start, role={}", self._member_id() or "?", self.role.value)
        self._stream_queue = asyncio.Queue()
        await self._start_coordination(session)
        try:
            await self._enqueue_user_input(inputs)
            asyncio.create_task(self._enqueue_mailbox_after_first_iteration())
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
        team_logger.info("[{}] stream start, role={}", self._member_id() or "?", self.role.value)
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
        team_logger.info("[{}] coordination starting", self._member_id() or "?")
        self._session = session
        if session:
            from openjiuwen.agent_teams.spawn.context import set_session_id
            set_session_id(session.get_session_id())
        # Persist leader config to session for full-restart recovery
        if session and self._spec and self.role == TeamRole.LEADER:
            self._persist_leader_config(session)
        await self._update_status(MemberStatus.READY)
        if not self._coordination_loop.is_running:
            await self._coordination_loop.start()
        if self._messager:
            team_id = self._team_id()
            if team_id and not self._subscribed_topics:
                await self._subscribe_transport(team_id)

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
        team_logger.info("[{}] coordination pausing (persistent)", self._member_id() or "?")
        # Signal teammates to pause polls
        if self._messager and self.role == TeamRole.LEADER:
            from openjiuwen.agent_teams.schema.events import (
                EventMessage,
                TeamStandbyEvent,
                TeamTopic,
            )
            from openjiuwen.agent_teams.spawn.context import get_session_id
            team_id = self._team_id()
            if team_id:
                try:
                    await self._messager.publish(
                        topic_id=TeamTopic.TEAM.build(get_session_id(), team_id),
                        message=EventMessage.from_event(TeamStandbyEvent(team_id=team_id)),
                    )
                except Exception as e:
                    team_logger.error("Failed to publish TEAM_STANDBY: {}", e)
        await self._unsubscribe_transport()
        if self._coordination_loop:
            await self._coordination_loop.stop()
        self._close_stream()

    async def _stop_coordination(self) -> None:
        """Stop the coordination loop, send sentinel, and unsubscribe."""
        team_logger.info("[{}] coordination stopping", self._member_id() or "?")
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

    def _close_stream(self) -> None:
        """Send sentinel to signal stream consumers that no more data is coming."""
        if self._stream_queue is not None:
            self._stream_queue.put_nowait(None)

    async def _subscribe_transport(self, team_id: str) -> None:
        """Subscribe to all TeamTopic channels on the transport."""
        if not self._messager or not self._coordination_loop:
            return
        from openjiuwen.agent_teams.spawn.context import get_session_id
        from openjiuwen.agent_teams.schema.events import EventMessage, TeamTopic

        local_member_id = self._member_id() or ""

        async def _filter_self(event: EventMessage) -> None:
            for listener in self._event_listeners:
                try:
                    await listener(event)
                except Exception as e:
                    team_logger.error("Event listener error: {}", e)
            if local_member_id and event.sender_id == local_member_id:
                team_logger.debug("ignoring self-published event: {}", event.event_type)
                return
            await self._coordination_loop.enqueue(event)

        session_id = get_session_id()
        await self._messager.register_direct_message_handler(
            self._coordination_loop.enqueue,
        )
        for topic in TeamTopic:
            topic_str = topic.build(session_id, team_id)
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
        session=None,
    ) -> None:
        """Run one round of DeepAgent via Runner in background.

        Chunks are pushed to _stream_queue for the outer
        stream()/invoke() to yield.
        """
        if self._deep_agent is None or self._stream_queue is None:
            return
        preview = initial_message if isinstance(initial_message, str) else type(initial_message).__name__
        team_logger.info("[{}] start_agent: {:.120}", self._member_id() or "?", str(preview))
        self._agent_task = asyncio.create_task(
            self._run_one_round(initial_message, session),
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
        session=None,
    ) -> None:
        """Execute one DeepAgent stream round via Runner."""
        await self._update_status(MemberStatus.BUSY)
        try:
            await self._execute_round(message, session)
            await self._update_status(MemberStatus.READY)
        except BaseException as e:
            team_logger.error("Failed to execute deep agent, {}", e, exc_info=True)
            await self._update_status(MemberStatus.ERROR)
        finally:
            self._agent_task = None
            next_resume = self._dequeue_valid_interrupt_resume()
            if next_resume is not None and self._stream_queue is not None:
                await self._start_agent(next_resume, session)
            else:
                await self._wake_mailbox_if_interrupt_cleared()
                if self._team_member and await self._team_member.status() == MemberStatus.SHUTDOWN_REQUESTED:
                    self._close_stream()

    async def _execute_round(
        self,
        message: Any,
        session=None,
    ) -> None:
        """Execute the agent invocation and manage execution status."""
        await self._update_execution(ExecutionStatus.STARTING)
        await self._update_execution(ExecutionStatus.RUNNING)
        try:
            response = await self.deep_agent.invoke(inputs={"query": message}, session=session)
            if self._stream_queue is not None:
                await self._stream_queue.put(response)
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
        if self._session is None:
            return False
        state = self._session.get_state(INTERRUPTION_KEY)
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

    def _member_id(self) -> Optional[str]:
        """Return the current agent's member_id."""
        return self._ctx.member_id if self._ctx else None

    def _team_id(self) -> Optional[str]:
        """Return the current team_id."""
        if self._ctx and self._ctx.team_spec:
            return self._ctx.team_spec.team_id
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
        member_spec: TeamMemberSpec,
        *,
        initial_message: Optional[str] = None,
    ) -> dict[str, Any]:
        """Build the payload used to bootstrap one teammate."""
        team_spec = self.team_spec
        member_transport = self._build_member_messager_config(member_spec)
        return {
            "coordination": {
                "team_id": team_spec.team_id if team_spec else "",
                "team_name": team_spec.name if team_spec else "",
                "leader_member_id": team_spec.leader_member_id if team_spec else None,
                "member_id": member_spec.member_id,
                "role": member_spec.role_type.value,
                "persona": member_spec.persona,
                "domain": member_spec.domain,
                "transport": (member_transport.model_dump(mode="json") if member_transport is not None else None),
            },
            "query": initial_message or "Join the team and wait for your first assignment.",
        }

    def build_member_context(self, member_spec: TeamMemberSpec) -> TeamRuntimeContext:
        """Build runtime context for one teammate from leader state."""
        return TeamRuntimeContext(
            role=member_spec.role_type,
            member_spec=member_spec,
            team_spec=self._ctx.team_spec,
            messager_config=self._build_member_messager_config(member_spec),
            db_config=self._ctx.db_config,
        )

    def _build_member_messager_config(self, member_spec: TeamMemberSpec):
        if self._ctx is None or self._ctx.messager_config is None:
            return None
        leader_cfg = self._ctx.messager_config
        meta = self._spec.metadata if self._spec else {}
        base_port = meta.get("teammate_base_port", 16000)
        port_offset = meta.get("teammate_port_offset", 10)

        # Reuse cached port on restart; assign new port on first spawn
        mid = member_spec.member_id
        if mid in self._member_port_map:
            port_base = self._member_port_map[mid]
        else:
            port_base = base_port + self._teammate_port_counter * port_offset
            self._teammate_port_counter += 1
            self._member_port_map[mid] = port_base

        updates: Dict[str, Any] = {
            "node_id": member_spec.member_id,
            "direct_addr": f"tcp://127.0.0.1:{port_base}",
            "pubsub_publish_addr": leader_cfg.pubsub_publish_addr,
            "pubsub_subscribe_addr": leader_cfg.pubsub_subscribe_addr,
        }
        # Teammates never run the pubsub proxy — only connect to the leader's.
        metadata = dict(leader_cfg.metadata)
        metadata.pop("pubsub_bind", None)
        updates["metadata"] = metadata
        return leader_cfg.model_copy(update=updates)

    def build_spawn_config(self, member_spec: TeamMemberSpec) -> SpawnAgentConfig:
        """Build JSON-safe spawn config for one teammate process."""
        context = self.build_member_context(member_spec)
        logging_config = self._build_member_logging_config(member_spec)
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
    def _build_member_logging_config(member_spec: TeamMemberSpec) -> dict[str, Any]:
        """Build a logging config with member-specific log file paths to avoid overwrites."""
        from openjiuwen.core.common.logging.log_config import get_log_config_snapshot

        config = get_log_config_snapshot()
        member_tag = member_spec.member_id or member_spec.name
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
        card = agent_spec.card or AgentCard(
            id=context.member_id or "unknown",
            name=context.member_spec.name if context.member_spec else "unknown",
            description=f"Teammate for domain {context.domain}",
        )
        agent = cls(card)
        await agent.configure_team(spec, context)
        return agent

    def _init_leader_member(self, member_id: str) -> None:
        """Initialize TeamMember for the leader after DB registration."""
        self._team_member = TeamMember(
            member_id=member_id,
            team_id=self._team_backend.team_id,
            name=self._ctx.member_spec.name if self._ctx.member_spec else member_id,
            agent_card=self.card,
            db=self._team_backend.db,
            messager=self._messager,
            desc=self._ctx.persona,
        )

    async def _on_teammate_created(self, teammate_id: str):
        team_logger.info("[{}] on_teammate_created: {}", self._member_id() or "?", teammate_id)
        if teammate_id == self._member_id():
            self._init_leader_member(teammate_id)
            return
        member_spec, spawn_config = await self._recover_member_spec(teammate_id)
        if member_spec is None:
            return
        teammate = await self._team_backend.get_member(teammate_id)
        await self.spawn_teammate(
            member_spec,
            initial_message=teammate.prompt if teammate else None,
            session=self._session.get_session_id() if self._session else None,
            spawn_config=spawn_config,
        )

    async def _recover_member_spec(
        self, member_id: str,
    ) -> tuple[Optional[TeamMemberSpec], SpawnConfig]:
        """Recover a TeamMemberSpec from the database.

        Used by both first-start (_on_teammate_created) and restart
        (_restart_teammate) paths, keeping the logic in one place.
        """
        teammate = await self._team_backend.get_member(member_id)
        if teammate is None:
            team_logger.error("Teammate {} not found in database", member_id)
            return None, SpawnConfig()

        member_spec = TeamMemberSpec(
            member_id=teammate.member_id,
            name=teammate.name,
            persona=teammate.desc or "",
            domain=teammate.name,
            prompt_hint=teammate.prompt,
        )
        spawn_config = SpawnConfig(
            health_check_timeout=30,
            health_check_interval=50,
        )
        return member_spec, spawn_config

    async def spawn_teammate(
        self,
        member_spec: TeamMemberSpec,
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
        member_id = member_spec.member_id
        team_logger.info("[{}] spawning teammate: {}", self._member_id() or "?", member_id)

        if self._spec and self._spec.spawn_mode == "inprocess":
            from openjiuwen.agent_teams.spawn.inprocess_spawn import inprocess_spawn

            handle = await inprocess_spawn(
                team_agent=self,
                member_spec=member_spec,
                initial_message=initial_message,
                session_id=self._session.get_session_id() if self._session else session,
            )
        else:
            handle = await Runner.spawn_agent(
                self.build_spawn_config(member_spec),
                self.build_spawn_payload(
                    member_spec,
                    initial_message=initial_message,
                ),
                session=session,
                spawn_config=spawn_config,
            )

        self._spawned_handles[member_id] = handle

        def _trigger_unhealthy_recovery() -> asyncio.Task:
            return asyncio.ensure_future(self._on_teammate_unhealthy(member_id))

        handle.on_unhealthy = _trigger_unhealthy_recovery
        return handle

    # ------------------------------------------------------------------
    # Fault tolerance: cleanup, restart, recover
    # ------------------------------------------------------------------

    async def _on_teammate_unhealthy(self, member_id: str) -> None:
        """Handle a teammate whose process has become unhealthy.

        Cleans up the dead process, marks the member as RESTARTING
        in the database, and attempts to re-spawn.
        """
        team_logger.warning("Teammate {} detected as unhealthy, initiating restart", member_id)
        await self._cleanup_teammate(member_id)
        if self._team_backend:
            await self._team_backend.db.update_member_status(member_id, MemberStatus.RESTARTING.value)
        await self._restart_teammate(member_id)

    async def _cleanup_teammate(self, member_id: str) -> None:
        """Clean up resources for a dead/dying teammate process."""
        handle = self._spawned_handles.pop(member_id, None)
        if handle is None:
            return
        try:
            await handle.stop_health_check()
            if handle.is_alive:
                await handle.force_kill()
        except Exception as e:
            team_logger.error("Error cleaning up teammate {}: {}", member_id, e)

    async def _restart_teammate(self, member_id: str, max_retries: int = 3) -> bool:
        """Restart a teammate process, recovering config from DB.

        Retries with exponential backoff. Publishes MemberRestartedEvent
        on success; marks ERROR on exhaustion.
        """
        member_spec, spawn_config = await self._recover_member_spec(member_id)
        if member_spec is None:
            team_logger.error("Cannot recover spawn config for {}", member_id)
            return False

        teammate = await self._team_backend.get_member(member_id)
        initial_message = teammate.prompt if teammate else None

        for attempt in range(1, max_retries + 1):
            try:
                team_logger.info("Restarting teammate {} (attempt {}/{})", member_id, attempt, max_retries)
                await self.spawn_teammate(
                    member_spec,
                    initial_message=initial_message,
                    spawn_config=spawn_config,
                )
                await self._publish_restart_event(member_id, attempt)
                team_logger.info("Teammate {} restarted successfully", member_id)
                return True
            except Exception as e:
                team_logger.error("Restart attempt {} for {} failed: {}", attempt, member_id, e)
                if attempt < max_retries:
                    await asyncio.sleep(2 ** attempt)

        # All retries exhausted
        if self._team_backend:
            await self._team_backend.db.update_member_status(member_id, MemberStatus.ERROR.value)
        return False

    async def _publish_restart_event(self, member_id: str, restart_count: int) -> None:
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
                topic_id=TeamTopic.TEAM.build(get_session_id(), self._team_backend.team_id),
                message=EventMessage.from_event(MemberRestartedEvent(
                    team_id=self._team_backend.team_id,
                    member_id=member_id,
                    restart_count=restart_count,
                )),
            )
        except Exception as e:
            team_logger.error("Failed to publish restart event for {}: {}", member_id, e)

    async def resume_for_new_session(self, session) -> None:
        """Prepare a persistent team for a new session.

        Switches the session context, creates new dynamic tables for
        the new session (tasks, messages), and persists leader config.
        Existing teammate processes and DB member records are retained.

        Args:
            session: The new session to attach to.
        """
        from openjiuwen.agent_teams.spawn.context import set_session_id
        self._session = session
        set_session_id(session.get_session_id())

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

        team_logger.info("[{}] recovering team", self._member_id() or "?")
        all_members = await self._team_backend.list_members()
        leader_id = self._member_id()
        restarted: list[str] = []

        for member in all_members:
            if member.member_id == leader_id:
                continue
            if member.status == MemberStatus.SHUTDOWN.value:
                continue
            await self._team_backend.db.update_member_status(
                member.member_id, MemberStatus.RESTARTING.value,
            )
            if await self._restart_teammate(member.member_id):
                restarted.append(member.member_id)

        return restarted

    # ------------------------------------------------------------------
    # Leader config persistence / recovery
    # ------------------------------------------------------------------

    def _persist_leader_config(self, session) -> None:
        """Persist leader's spec + context to session state for recovery."""
        session.update_state({
            "spec": self._spec.model_dump(mode="json"),
            "context": self._ctx.model_dump(mode="json"),
            "team_id": self._team_id(),
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
        card = agent_spec.card or AgentCard(
            id=context.member_id or "leader",
            name=context.member_spec.name if context.member_spec else "leader",
        )
        agent = cls(card)
        agent.configure(spec, context)
        return agent


__all__ = ["TeamAgent"]
