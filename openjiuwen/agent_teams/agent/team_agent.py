# coding: utf-8
"""Unified TeamAgent implementation."""

from __future__ import annotations

import asyncio
from typing import (
    TYPE_CHECKING,
    Any,
    Dict,
    List,
    Optional,
)

from openjiuwen.agent_teams.agent.agent_configurator import (
    AgentConfigurator,
    _build_member_logging_config,
    _qualify_team_tool_ids,
)
from openjiuwen.agent_teams.agent.coordination_manager import CoordinationManager
from openjiuwen.agent_teams.agent.coordinator import (
    CoordinatorLoop,
    InnerEventMessage,
    InnerEventType,
)
from openjiuwen.agent_teams.agent.member import TeamMember
from openjiuwen.agent_teams.agent.recovery_manager import RecoveryManager
from openjiuwen.agent_teams.agent.session_manager import SessionManager
from openjiuwen.agent_teams.agent.spawn_manager import SpawnManager
from openjiuwen.agent_teams.agent.stream_controller import StreamController
from openjiuwen.agent_teams.messager import Messager
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
from openjiuwen.core.runner.spawn.agent_config import SpawnAgentConfig
from openjiuwen.core.runner.spawn.process_manager import SpawnConfig
from openjiuwen.core.single_agent.base import BaseAgent
from openjiuwen.core.single_agent.rail.base import AgentRail
from openjiuwen.harness.deep_agent import DeepAgent

if TYPE_CHECKING:
    from openjiuwen.agent_teams.agent.model_allocator import Allocation, ModelAllocator
    from openjiuwen.agent_teams.schema.team import ModelPoolEntry
    from openjiuwen.agent_teams.team_workspace.manager import TeamWorkspaceManager
    from openjiuwen.agent_teams.worktree.manager import WorktreeManager
    from openjiuwen.harness.schema.config import DeepAgentConfig


# pylint: disable=too-many-public-methods
class TeamAgent(BaseAgent):
    """One implementation that can act as leader or teammate.

    Uses composition: wraps an internal DeepAgent instance instead of
    inheriting from it. Delegates to specialized managers for
    configuration, streaming, spawning, recovery, and session management.
    """

    def __init__(self, card):
        super().__init__(card)
        self._configurator = AgentConfigurator(card)
        self._coordination_loop: Optional[CoordinatorLoop] = None
        self._dispatcher = None
        self._event_listeners: list = []
        self._team_member: Optional[TeamMember] = None
        self._pending_user_query: str = ""

        self._spawn_manager = SpawnManager(
            configurator=self._configurator,
            session_id_getter=lambda: self._session_manager.session_id,
            team_agent_getter=lambda: self,
        )
        self._recovery_manager = RecoveryManager(
            configurator=self._configurator,
            spawn_manager=self._spawn_manager,
        )
        self._session_manager = SessionManager(
            configurator=self._configurator,
            recovery_manager=self._recovery_manager,
        )
        self._stream_controller = StreamController(
            deep_agent_getter=lambda: self._configurator.deep_agent,
            member_name_getter=self._member_name,
            status_updater=self._update_status,
            execution_updater=self._update_execution,
            team_member_getter=lambda: self._team_member,
            session_id_getter=lambda: self._session_manager.session_id,
            wake_mailbox_callback=self._wake_mailbox_if_interrupt_cleared,
        )
        self._coordination_manager = CoordinationManager(self)

    # ------------------------------------------------------------------
    # Properties — delegate to configurator
    # ------------------------------------------------------------------

    @property
    def deep_agent(self) -> Optional[DeepAgent]:
        return self._configurator.deep_agent

    @property
    def deep_config(self) -> Optional["DeepAgentConfig"]:
        if self._configurator.deep_agent is None:
            return None
        return self._configurator.deep_agent.deep_config

    @property
    def spec(self) -> Optional[TeamAgentSpec]:
        return self._configurator.spec

    @property
    def runtime_context(self) -> Optional[TeamRuntimeContext]:
        return self._configurator.ctx

    @property
    def coordination_loop(self) -> Optional[CoordinatorLoop]:
        return self._coordination_loop

    @property
    def role(self) -> TeamRole:
        return self._configurator.role

    @property
    def lifecycle(self) -> str:
        return self._configurator.lifecycle

    @property
    def role_prompt_policy(self) -> str:
        return self._configurator.role_policy

    @property
    def mailbox_transport(self) -> Optional[Messager]:
        return self._configurator.messager

    @property
    def team_spec(self) -> Optional[TeamSpec]:
        return self._configurator.team_spec

    @property
    def member_name(self) -> Optional[str]:
        return self._configurator.member_name

    @property
    def message_manager(self):
        return self._configurator.message_manager

    @property
    def task_manager(self):
        return self._configurator.task_manager

    @property
    def team_backend(self) -> Optional[TeamBackend]:
        return self._configurator.team_backend

    @property
    def session_id(self) -> Optional[str]:
        """Return the current session ID."""
        return self._session_manager.session_id

    def set_session_id(self, session_id: Optional[str]) -> None:
        """Set the session ID for this agent."""
        self._session_manager.session_id = session_id

    @property
    def configurator(self) -> AgentConfigurator:
        """Return the agent configurator."""
        return self._configurator

    @property
    def session_manager(self) -> SessionManager:
        """Return the session manager."""
        return self._session_manager

    @property
    def recovery_manager(self) -> RecoveryManager:
        """Return the recovery manager."""
        return self._recovery_manager

    @property
    def spawn_manager(self) -> SpawnManager:
        """Return the spawn manager."""
        return self._spawn_manager

    @property
    def stream_controller(self) -> StreamController:
        """Return the stream controller."""
        return self._stream_controller

    @property
    def event_listeners(self) -> list:
        """Return the registered event listeners."""
        return self._event_listeners

    @property
    def team_member(self) -> Optional[TeamMember]:
        """Return the TeamMember handle for this agent, if set."""
        return self._team_member

    @property
    def pending_user_query(self) -> str:
        """Return the pending user query string."""
        return self._pending_user_query

    @property
    def team_name(self) -> Optional[str]:
        """Return the team name from the runtime context."""
        return self._configurator.team_name

    async def update_status(self, status: MemberStatus) -> None:
        """Update the member status in the database."""
        await self._update_status(status)

    def persist_allocator_state(self) -> None:
        """Persist the model allocator state to the current session."""
        self._persist_allocator_state()

    # ------------------------------------------------------------------
    # Event listeners
    # ------------------------------------------------------------------

    def add_event_listener(self, handler) -> None:
        self._event_listeners.append(handler)

    def remove_event_listener(self, handler) -> None:
        try:
            self._event_listeners.remove(handler)
        except ValueError:
            pass

    async def has_team_member(self, member_name: str) -> bool:
        if self._configurator.team_backend is None:
            return False
        return await self._configurator.team_backend.get_member(member_name) is not None

    def is_agent_ready(self) -> bool:
        return self._configurator.deep_agent is not None

    def is_agent_running(self) -> bool:
        return self._is_agent_running()

    def has_in_flight_round(self) -> bool:
        return self._has_in_flight_round()

    async def deliver_input(self, content: Any, *, use_steer: bool = True) -> None:
        if self._is_agent_running():
            if use_steer:
                await self.steer(content)
            else:
                await self.follow_up(content)
            return
        if self._has_in_flight_round():
            preview = content if isinstance(content, str) else type(content).__name__
            team_logger.info(
                "[{}] queueing input for next round (transition window): {:.60}",
                self._member_name() or "?",
                str(preview),
            )
            self._stream_controller.pending_inputs.append(content)
            return
        await self._start_agent(content)

    def has_pending_interrupt(self) -> bool:
        return self._stream_controller.has_pending_interrupt()

    async def start_agent(self, content: str) -> None:
        await self._start_agent(content)

    async def follow_up(self, content: str) -> None:
        await self._stream_controller.follow_up(content)

    async def cancel_agent(self) -> None:
        team_logger.debug("[{}] cancel_agent requested", self._member_name() or "?")
        await self._cancel_agent()

    async def destroy_team(self, force: bool = True) -> bool:
        try:
            await self.cancel_agent()
        except Exception as e:
            team_logger.warning("[{}] cancel_agent during destroy failed: {}", self._member_name() or "?", e)

        try:
            await self._stop_coordination()
        except Exception as e:
            team_logger.warning("[{}] stop coordination during destroy failed: {}", self._member_name() or "?", e)

        if not self._configurator.team_backend:
            return False

        return await self._configurator.team_backend.force_clean_team(shutdown_members=force)

    async def pause_polls(self) -> None:
        if self._coordination_loop:
            await self._coordination_loop.pause_polls()

    async def resume_polls(self) -> None:
        if self._coordination_loop:
            await self._coordination_loop.resume_polls()

    async def steer(self, content: str) -> None:
        await self._stream_controller.steer(content)

    async def resume_interrupt(self, user_input) -> None:
        if not self._stream_controller.is_valid_interrupt_resume(user_input):
            team_logger.info("[{}] dropping stale interrupt resume input", self._member_name() or "?")
            return
        if self._has_in_flight_round():
            team_logger.info(
                "[{}] queueing interrupt resume until current round completes",
                self._member_name() or "?",
            )
            self._stream_controller.pending_interrupt_resumes.append(user_input)
            return
        await self._start_agent(user_input)

    # ------------------------------------------------------------------
    # BaseAgent abstract method: configure
    # ------------------------------------------------------------------

    # pylint: disable=arguments-differ
    def configure(self, spec: TeamAgentSpec, context: TeamRuntimeContext) -> "TeamAgent":
        self._setup_infra(spec, context)
        self._setup_agent(spec, context)
        return self

    # ------------------------------------------------------------------
    # Team-specific configuration
    # ------------------------------------------------------------------

    def _setup_infra(self, spec: TeamAgentSpec, ctx: TeamRuntimeContext) -> None:
        self._configurator.setup_infra(spec, ctx, on_teammate_created=self._on_teammate_created)

    def _setup_agent(self, spec: TeamAgentSpec, ctx: TeamRuntimeContext) -> None:
        self._configurator.setup_agent(spec, ctx)

        if ctx.role == TeamRole.TEAMMATE and ctx.member_name and self._configurator.team_backend:
            self._team_member = TeamMember(
                member_name=ctx.member_name,
                team_name=self._configurator.team_backend.team_name,
                agent_card=self.card,
                db=self._configurator.team_backend.db,
                messager=self._configurator.messager,
                desc=ctx.persona,
            )

        from openjiuwen.agent_teams.agent.dispatcher import EventDispatcher

        self._dispatcher = EventDispatcher(self)
        self._coordination_loop = CoordinatorLoop(
            role=ctx.role,
            wake_callback=self._dispatcher.dispatch,
        )

    def _resolve_agent_spec(
        self,
        spec: TeamAgentSpec,
        role: TeamRole,
        member_name: Optional[str] = None,
    ):
        return self._configurator.resolve_agent_spec(spec, role, member_name)

    def update_model_pool(self, new_pool: "list[ModelPoolEntry]") -> None:
        self._configurator.update_model_pool(new_pool)
        if self._configurator.spec is None or self.role != TeamRole.LEADER:
            return
        team_session = self._session_manager.team_session
        if team_session is None:
            return
        self._recovery_manager.persist_leader_config(team_session)

    def attach_model_allocator(
        self,
        allocator: "ModelAllocator",
        *,
        leader_allocation: Optional["Allocation"] = None,
    ) -> None:
        self._configurator.attach_model_allocator(allocator, leader_allocation=leader_allocation)

    def restore_allocator_state(self, state: dict) -> None:
        self._configurator.restore_allocator_state(state)

    def _create_workspace_manager(
        self,
        spec: TeamAgentSpec,
        ctx: TeamRuntimeContext,
    ) -> "TeamWorkspaceManager":
        return self._configurator.create_workspace_manager(spec, ctx)

    def _create_worktree_manager(self, spec: TeamAgentSpec) -> "WorktreeManager":
        return self._configurator.create_worktree_manager(spec)

    # ------------------------------------------------------------------
    # Role-based tool registration
    # ------------------------------------------------------------------

    def _register_team_tools(
        self,
        spec: TeamAgentSpec,
        ctx: TeamRuntimeContext,
        messager: Messager,
    ) -> List[ToolCard]:
        return self._configurator.register_team_tools(
            spec, ctx, messager, on_teammate_created=self._on_teammate_created
        )

    @staticmethod
    def _qualify_team_tool_ids(team_tools: list[Tool], *, team_name: str, member_name: str) -> None:
        _qualify_team_tool_ids(team_tools, team_name=team_name, member_name=member_name)

    # ------------------------------------------------------------------
    # BaseAgent abstract methods: invoke / stream
    # ------------------------------------------------------------------

    async def invoke(self, inputs, session=None):
        team_logger.info("[{}] invoke start, role={}", self._member_name() or "?", self.role.value)
        self._stream_controller.stream_queue = asyncio.Queue()
        # Cache the user query so CoordinationManager can pass it to the
        # memory pipeline during start().
        self._pending_user_query = inputs.get("query", "") if isinstance(inputs, dict) else str(inputs)
        await self._coordination_manager.start(session)
        try:
            await self._coordination_manager.enqueue_user_input(inputs)
            await self._coordination_manager.enqueue_mailbox_after_first_iteration()
            last_result = None
            while True:
                chunk = await self._stream_controller.stream_queue.get()
                if chunk is None:
                    break
                last_result = chunk
            return last_result
        finally:
            await self._coordination_manager.finalize_round()

    async def broadcast(self, content: str) -> Optional[str]:
        from openjiuwen.agent_teams.interaction import UserInbox

        if self._configurator.team_backend is None:
            raise RuntimeError("TeamAgent.broadcast requires a configured team backend")
        return await UserInbox(self._configurator.team_backend.message_manager).broadcast(content)

    async def human_agent_say(
        self,
        content: str,
        to: Optional[str] = None,
        *,
        sender: Optional[str] = None,
    ) -> Optional[str]:
        from openjiuwen.agent_teams.interaction import HumanAgentInbox

        if self._configurator.team_backend is None:
            raise RuntimeError("TeamAgent.human_agent_say requires a configured team backend")
        return await HumanAgentInbox(
            self._configurator.team_backend,
            self._configurator.team_backend.message_manager,
        ).send(content, to=to, sender=sender)

    async def stream(self, inputs, session=None, stream_modes=None):
        team_logger.info("[{}] stream start, role={}", self._member_name() or "?", self.role.value)
        self._stream_controller.stream_queue = asyncio.Queue()
        self._pending_user_query = inputs.get("query", "") if isinstance(inputs, dict) else str(inputs)
        await self._coordination_manager.start(session)
        try:
            await self._coordination_manager.enqueue_user_input(inputs)
            await self._coordination_manager.enqueue_mailbox_after_first_iteration()
            while True:
                chunk = await self._stream_controller.stream_queue.get()
                if chunk is None:
                    break
                yield chunk
        finally:
            await self._coordination_manager.finalize_round()

    async def interact(self, message: str) -> None:
        if self._coordination_loop is None:
            return
        await self._coordination_loop.enqueue(
            InnerEventMessage(
                event_type=InnerEventType.USER_INPUT,
                payload={"content": message},
            )
        )

    # ------------------------------------------------------------------
    # Coordination lifecycle (delegates to CoordinationManager; kept as
    # public wrappers because tests drive them by name)
    # ------------------------------------------------------------------

    async def _start_coordination(self, session=None) -> None:
        await self._coordination_manager.start(session)

    async def _pause_coordination(self) -> None:
        await self._coordination_manager.pause()

    async def pause_coordination(self) -> None:
        """Pause coordination without tearing down teammate processes."""
        await self._pause_coordination()

    async def _stop_coordination(self) -> None:
        await self._coordination_manager.stop()

    async def stop_coordination(self) -> None:
        """Stop coordination and shut down all spawned teammates."""
        await self._stop_coordination()

    def _close_stream(self) -> None:
        self._coordination_manager.close_stream()

    @property
    def _subscribed_topics(self) -> list[str]:
        return self._coordination_manager.subscribed_topics

    def _is_agent_running(self) -> bool:
        return self._stream_controller.is_agent_running()

    def _has_in_flight_round(self) -> bool:
        return self._stream_controller.has_in_flight_round()

    async def _cancel_agent(self) -> None:
        await self._stream_controller.cancel_agent()

    async def shutdown_self(self) -> None:
        member_name = self._member_name() or "?"
        team_logger.info("[{}] shutdown_self requested", member_name)
        sc_task = self._stream_controller.agent_task
        if sc_task is not None and not sc_task.done():
            sc_task.cancel()
        if self._team_member is not None:
            try:
                await self._team_member.update_status(MemberStatus.SHUTDOWN)
            except Exception as e:
                team_logger.debug(
                    "[{}] post-clean status update failed (expected): {}",
                    member_name,
                    e,
                )
        self._close_stream()

    async def _start_agent(self, initial_message: Any) -> None:
        await self._stream_controller.start_round(initial_message)

    async def _update_status(self, status: MemberStatus) -> None:
        if self._team_member:
            await self._team_member.update_status(status)

    async def _update_execution(self, status: ExecutionStatus) -> None:
        if self._team_member:
            await self._team_member.update_execution_status(status)

    async def _wake_mailbox_if_interrupt_cleared(self) -> None:
        await self._coordination_manager.wake_mailbox_if_interrupt_cleared()

    def _member_name(self) -> Optional[str]:
        return self._configurator.member_name

    def _team_name(self) -> Optional[str]:
        return self._configurator.team_name

    # ------------------------------------------------------------------
    # Rail / callback proxies to internal DeepAgent
    # ------------------------------------------------------------------

    async def register_rail(self, rail: AgentRail) -> "TeamAgent":
        if self._configurator.deep_agent is not None:
            await self._configurator.deep_agent.register_rail(rail)
        return self

    async def unregister_rail(self, rail: AgentRail) -> "TeamAgent":
        if self._configurator.deep_agent is not None:
            await self._configurator.deep_agent.unregister_rail(rail)
        return self

    # ------------------------------------------------------------------
    # Spawn / clone helpers
    # ------------------------------------------------------------------

    def build_spawn_payload(
        self,
        ctx: TeamRuntimeContext,
        *,
        initial_message: Optional[str] = None,
    ) -> dict[str, Any]:
        return self._configurator.build_spawn_payload(ctx, initial_message=initial_message)

    def build_member_context(self, member_spec: TeamMemberSpec) -> TeamRuntimeContext:
        return self._configurator.build_member_context(member_spec)

    def build_spawn_config(self, ctx: TeamRuntimeContext) -> SpawnAgentConfig:
        return self._configurator.build_spawn_config(ctx)

    @staticmethod
    def _build_member_logging_config(member_name: str, name: str) -> dict[str, Any]:
        return _build_member_logging_config(member_name, name)

    @classmethod
    async def from_spawn_payload(cls, payload: Dict[str, Any]) -> "TeamAgent":
        from openjiuwen.core.single_agent.schema.agent_card import AgentCard

        spec = TeamAgentSpec.model_validate(payload["spec"])
        context = TeamRuntimeContext.model_validate(payload["context"])

        agent_spec = spec.agents.get(context.role.value) or spec.agents["leader"]
        team_name = (context.team_spec.team_name if context.team_spec else None) or spec.team_name
        card_id = f"{team_name}_{context.member_name}" if context.member_name else "unknown"
        card = agent_spec.card or AgentCard(
            id=card_id,
            name=context.member_name or "unknown",
            description=f"Teammate: {context.persona}" if context.persona else "Teammate",
        )
        agent = cls(card)
        agent.configure(spec, context)
        return agent

    def _init_leader_member(self, member_name: str) -> None:
        self._team_member = TeamMember(
            member_name=member_name,
            team_name=self._configurator.team_backend.team_name,
            agent_card=self.card,
            db=self._configurator.team_backend.db,
            messager=self._configurator.messager,
            desc=self._configurator.ctx.persona,
        )

    async def _on_teammate_created(self, teammate_id: str):
        team_logger.info("[{}] on_teammate_created: {}", self._member_name() or "?", teammate_id)
        if teammate_id == self._member_name():
            self._init_leader_member(teammate_id)
            return
        ctx = await self._spawn_manager.build_context_from_db(teammate_id)
        if ctx is None:
            return
        teammate = await self._configurator.team_backend.get_member(teammate_id)
        await self.spawn_teammate(
            ctx,
            initial_message=teammate.prompt if teammate else None,
            session=self._session_manager.session_id,
            spawn_config=SpawnConfig(health_check_timeout=30, health_check_interval=50),
        )

    async def spawn_teammate(
        self,
        ctx: TeamRuntimeContext,
        *,
        initial_message: Optional[str] = None,
        session: Optional[Any] = None,
        spawn_config: Optional[SpawnConfig] = None,
    ):
        return await self._spawn_manager.spawn_teammate(
            ctx,
            initial_message=initial_message,
            session=session,
            spawn_config=spawn_config,
        )

    # ------------------------------------------------------------------
    # Fault tolerance: cleanup, restart, recover
    # ------------------------------------------------------------------

    async def resume_for_new_session(self, session) -> None:
        await self._session_manager.resume_for_new_session(session)

    async def recover_for_existing_session(self, session) -> None:
        """Recover an existing session checkpoint on a running TeamAgent.

        Unlike recover_from_session which constructs a fresh agent, this
        method reuses the current agent and assumes session.pre_run() has
        already restored checkpoint state. Used for session switches that
        should not unwind the entire team.
        """
        await self._stop_coordination()
        await self._session_manager.recover_for_existing_session(session)

    async def recover_team(self) -> list[str]:
        return await self._recovery_manager.recover_team()

    # ------------------------------------------------------------------
    # Leader config persistence / recovery
    # ------------------------------------------------------------------

    def _persist_leader_config(self, session) -> None:
        self._recovery_manager.persist_leader_config(session)

    def _persist_allocator_state(self) -> None:
        self._recovery_manager.persist_allocator_state(self._session_manager.team_session)

    @classmethod
    def recover_from_session(cls, session) -> "TeamAgent":
        from openjiuwen.core.single_agent.schema.agent_card import AgentCard

        state = session.get_state()
        spec_data = state.get("spec")
        if spec_data is None:
            raise ValueError("No leader spec found in session state")
        spec = TeamAgentSpec.model_validate(spec_data)
        context = TeamRuntimeContext.model_validate(state["context"])

        agent_spec = spec.agents.get(context.role.value) or spec.agents["leader"]
        team_name = (context.team_spec.team_name if context.team_spec else None) or spec.team_name
        card_id = f"{team_name}_{context.member_name}" if context.member_name else "leader"
        card = agent_spec.card or AgentCard(
            id=card_id,
            name=context.member_name or "leader",
        )
        agent = cls(card)
        agent.configure(spec, context)
        allocator_state = state.get("model_allocator_state")
        if allocator_state:
            agent.restore_allocator_state(allocator_state)
        agent.set_session_id(session.get_session_id())
        return agent


__all__ = ["TeamAgent"]
