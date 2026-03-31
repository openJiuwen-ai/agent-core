# coding: utf-8
"""Unified TeamAgent implementation."""

from __future__ import annotations

import asyncio
from typing import (
    Any,
    Dict,
    List,
    Optional,
)

from openjiuwen.agent_teams.agent.coordination import (
    CoordinationLoop,
    InnerEventMessage,
    InnerEventType,
)
from openjiuwen.agent_teams.agent.policy import (
    build_system_prompt,
    role_policy,
)
from openjiuwen.agent_teams.messager import (
    create_messager,
    Messager,
)
from openjiuwen.agent_teams.schema.blueprint import TeamAgentSpec
from openjiuwen.agent_teams.schema.team import TeamRuntimeContext
from openjiuwen.agent_teams.schema.team import (
    TeamMemberSpec,
    TeamRole,
    TeamSpec,
)
from openjiuwen.agent_teams.tools.member import TeamMember
from openjiuwen.agent_teams.tools.status import ExecutionStatus, MemberStatus
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
from openjiuwen.core.single_agent.base import BaseAgent
from openjiuwen.core.single_agent.rail.base import AgentRail
from openjiuwen.deepagents import create_deep_agent
from openjiuwen.deepagents.deep_agent import DeepAgent
from openjiuwen.deepagents.rails.filesystem_rail import FileSystemRail


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
        self._coordination_loop: Optional[CoordinationLoop] = None
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
    def coordination_loop(self) -> Optional[CoordinationLoop]:
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

    def is_agent_ready(self) -> bool:
        """Whether the agent has been fully initialized."""
        return self._deep_agent is not None

    def is_agent_running(self) -> bool:
        """Whether the agent is in an active round."""
        return self._is_agent_running()

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

    async def steer(self, content: str) -> None:
        """Steer instruction into the running agent."""
        if self._deep_agent is not None:
            team_logger.debug("[{}] steer: {:.120}", self._member_id() or "?", content)
            await self._deep_agent.steer(content, self._session)

    # ------------------------------------------------------------------
    # BaseAgent abstract method: configure
    # ------------------------------------------------------------------

    def configure(self, spec: TeamAgentSpec, context: TeamRuntimeContext) -> "TeamAgent":
        """Satisfy BaseAgent.configure – delegate to _configure_team."""
        return self._configure_team(spec, context)

    # ------------------------------------------------------------------
    # Team-specific configuration
    # ------------------------------------------------------------------

    def _resolve_agent_spec(self, spec: TeamAgentSpec, role: TeamRole):
        """Return the DeepAgentSpec for the given role, falling back to leader."""
        return spec.agents.get(role.value) or spec.agents["leader"]

    def _configure_team(self, spec: TeamAgentSpec, ctx: TeamRuntimeContext) -> "TeamAgent":
        """Configure this TeamAgent and its underlying DeepAgent."""
        self._spec = spec
        self._ctx = ctx
        agent_spec = self._resolve_agent_spec(spec, ctx.role)

        language = agent_spec.language or "cn"
        self._role_policy = role_policy(ctx.role, language=language)

        workspace_obj = agent_spec.workspace.build() if agent_spec.workspace else None
        stop_condition = agent_spec.stop_condition.build() if agent_spec.stop_condition else None
        model = agent_spec.model.build() if agent_spec.model else None

        messager_config = ctx.messager_config
        member_id = ctx.member_id
        if member_id and messager_config and messager_config.node_id != member_id:
            messager_config = messager_config.model_copy(update={"node_id": member_id})
        self._messager = create_messager(messager_config) if messager_config else None

        tools = self._register_team_tools(spec, ctx, self._messager)

        from openjiuwen.agent_teams.agent.rails import FirstIterationGate
        fs_rail = FileSystemRail()
        self._first_iter_gate = FirstIterationGate()

        system_prompt = build_system_prompt(
            role=ctx.role,
            persona=ctx.persona,
            domain=ctx.domain,
            base_prompt=agent_spec.system_prompt,
            team_info=ctx.team_info or None,
            team_members=ctx.team_members or None,
            member_id=member_id,
            lifecycle=spec.lifecycle,
            language=language,
        )
        team_logger.info("当前成员系统提示词：\n{}", system_prompt)

        self._deep_agent = create_deep_agent(
            model=model,
            card=self.card,
            system_prompt=system_prompt,
            tools=tools,
            rails=[fs_rail, self._first_iter_gate],
            workspace=workspace_obj,
            stop_condition=stop_condition,
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
        self._coordination_loop = CoordinationLoop(
            role=ctx.role,
            wake_callback=self._dispatcher.dispatch,
        )
        return self

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
        from openjiuwen.agent_teams.tools.database import TeamDatabase
        from openjiuwen.agent_teams.tools.team import TeamBackend
        from openjiuwen.agent_teams.tools.team_tools import create_team_tools
        from openjiuwen.agent_teams.tools.status import MemberMode

        team_id = (ctx.team_spec.team_id if ctx.team_spec else None) or "default"
        db = TeamDatabase(ctx.db_config)

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
        )
        self._team_backend = agent_team
        self._task_manager = agent_team.task_manager
        self._message_manager = agent_team.message_manager

        team_tools = create_team_tools(
            role=ctx.role.value,
            agent_team=agent_team,
            on_teammate_created=self._on_teammate_created,
        )

        # Best-effort registration with Runner's
        # resource manager.  When Runner has not been
        # bootstrapped (e.g. unit tests) we skip
        # silently -- the cards are still in
        # ability_manager for schema generation.
        try:
            from openjiuwen.core.runner import Runner

            Runner.resource_mgr.add_tool(team_tools)
        except Exception:
            pass

        return [t.card for t in team_tools]

    # ------------------------------------------------------------------
    # BaseAgent abstract methods: invoke / stream
    # ------------------------------------------------------------------

    async def invoke(self, inputs, session=None):
        """Execute via CoordinationLoop-driven rounds.

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
            await self._stop_coordination()
            if self._team_member:
                await self._team_member.update_status(MemberStatus.SHUTDOWN)
            self._stream_queue = None

    async def stream(self, inputs, session=None, stream_modes=None):
        """Stream via CoordinationLoop-driven rounds.

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
            await self._stop_coordination()
            if self._team_member:
                await self._team_member.update_status(MemberStatus.SHUTDOWN)
            self._stream_queue = None

    async def interact(self, message: str) -> None:
        """Inject user input into CoordinationLoop as USER_INPUT event."""
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
            from openjiuwen.agent_teams.tools.context import set_session_id
            set_session_id(session.get_session_id())
        # Persist leader config to session for full-restart recovery
        if session and self._spec and self.role == TeamRole.LEADER:
            self._persist_leader_config(session)
        await self._update_status(MemberStatus.READY)
        await self._coordination_loop.start()
        if self._messager:
            team_id = self._team_id()
            if team_id:
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
        query = inputs.get("query", "") if isinstance(inputs, dict) else str(inputs)
        if self._coordination_loop is None:
            return
        await self._coordination_loop.enqueue(
            InnerEventMessage(
                event_type=InnerEventType.USER_INPUT,
                payload={"content": query},
            )
        )

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
        if self._stream_queue is not None:
            await self._stream_queue.put(None)

    async def _subscribe_transport(self, team_id: str) -> None:
        """Subscribe to all TeamTopic channels on the transport."""
        if not self._messager or not self._coordination_loop:
            return
        from openjiuwen.agent_teams.tools.context import get_session_id
        from openjiuwen.agent_teams.tools.team_events import EventMessage, TeamTopic

        local_member_id = self._member_id() or ""

        async def _filter_self(event: EventMessage) -> None:
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
            pass
        for topic in self._subscribed_topics:
            try:
                await self._messager.unsubscribe(topic)
            except Exception:
                pass
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
        initial_message: str,
        session=None,
    ) -> None:
        """Run one round of DeepAgent via Runner in background.

        Chunks are pushed to _stream_queue for the outer
        stream()/invoke() to yield.
        """
        if self._deep_agent is None or self._stream_queue is None:
            return
        team_logger.info("[{}] start_agent: {:.120}", self._member_id() or "?", initial_message)
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
        message: str,
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

    async def _execute_round(
        self,
        message: str,
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

    def clone_for_member(self, member_spec: TeamMemberSpec) -> "TeamAgent":
        """Create a teammate-configured TeamAgent using the same implementation."""
        context = self._build_member_context(member_spec)
        card = self.card.model_copy(update={
            "id": member_spec.member_id,
            "name": member_spec.name,
            "description": f"Teammate for domain {member_spec.domain}",
        })
        teammate = TeamAgent(card)
        teammate._configure_team(self._spec, context)
        return teammate

    def _build_member_context(self, member_spec: TeamMemberSpec) -> TeamRuntimeContext:
        """Build runtime context for one teammate from leader state."""
        return TeamRuntimeContext(
            role=member_spec.role_type,
            member_spec=member_spec,
            team_spec=self._ctx.team_spec,
            team_info=member_spec.metadata.get("team_info", {}),
            team_members=member_spec.metadata.get("team_members", []),
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
        context = self._build_member_context(member_spec)
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
    def from_spawn_payload(cls, payload: Dict[str, Any]) -> "TeamAgent":
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
        agent._configure_team(spec, context)
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

        team_info_obj = await self._team_backend.get_team_info()
        all_members = await self._team_backend.list_members()

        team_info_dict: Dict[str, Any] = {}
        if team_info_obj:
            team_info_dict = {
                "name": team_info_obj.name,
                "desc": team_info_obj.desc or "",
                "prompt": team_info_obj.prompt or "",
            }

        team_members_info = [
            {"name": m.name, "member_id": m.member_id, "desc": m.desc or ""}
            for m in all_members
        ]

        member_spec = TeamMemberSpec(
            member_id=teammate.member_id,
            name=teammate.name,
            persona=teammate.desc or "",
            domain=teammate.name,
            prompt_hint=teammate.prompt,
            metadata={
                "team_info": team_info_dict,
                "team_members": team_members_info,
            },
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
    ) -> SpawnedProcessHandle:
        """Spawn one teammate process via Runner.spawn_agent.

        The returned handle is tracked internally and an on_unhealthy
        callback is registered so the leader can auto-restart the
        teammate when consecutive health checks fail.
        """
        member_id = member_spec.member_id
        team_logger.info("[{}] spawning teammate: {}", self._member_id() or "?", member_id)
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
        handle.on_unhealthy = lambda: asyncio.ensure_future(
            self._on_teammate_unhealthy(member_id)
        )
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
        from openjiuwen.agent_teams.tools.context import get_session_id
        from openjiuwen.agent_teams.tools.team_events import (
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
        agent._configure_team(spec, context)
        return agent


__all__ = ["TeamAgent"]
