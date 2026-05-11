# coding: utf-8
"""Coordination lifecycle and transport wiring for TeamAgent."""

from __future__ import annotations

from typing import (
    TYPE_CHECKING,
    Any,
    Optional,
)

from openjiuwen.agent_teams.agent.coordination.event_bus import (
    EventBus,
    InnerEventMessage,
    InnerEventType,
)
from openjiuwen.agent_teams.schema.status import MemberStatus
from openjiuwen.agent_teams.schema.team import TeamRole
from openjiuwen.core.common.logging import team_logger
from openjiuwen.core.session.agent_team import Session as AgentTeamSession

if TYPE_CHECKING:
    from openjiuwen.agent_teams.agent.coordination.dispatcher import EventDispatcher
    from openjiuwen.agent_teams.agent.team_agent import TeamAgent


class CoordinationKernel:
    """Owns the coordination subsystem: lifecycle, transport wiring, and
    inner event ingress.

    Holds a back-reference to the owning TeamAgent because the lifecycle
    coordinates across most of TeamAgent's collaborators (stream_controller,
    session_manager, recovery_manager, spawn_manager, event bus,
    team_member). Centralizing them here keeps TeamAgent focused on the
    public BaseAgent contract.

    The internal event bus and dispatcher are constructed and owned here;
    callers go through this kernel instead of poking at the bus directly.
    """

    def __init__(self, host: "TeamAgent") -> None:
        self._host = host
        self._subscribed_topics: list[str] = []
        self._event_bus: Optional[EventBus] = None
        self._dispatcher: Optional["EventDispatcher"] = None

    def setup(self, *, role: TeamRole) -> None:
        """Construct the event bus and dispatcher for the given role.

        Called during TeamAgent.configure() once the role is known.
        The bus is built first so it can be passed into the dispatcher
        as the poll controller; ``dispatcher.dispatch`` is bound back
        to the bus as wake callback at ``start()`` time, not here.
        """
        from openjiuwen.agent_teams.agent.coordination.dispatcher import EventDispatcher

        host = self._host
        blueprint = host.blueprint
        infra = host.infra
        if blueprint is None or infra is None:
            raise RuntimeError("CoordinationKernel.setup() requires configured blueprint and infra")
        event_bus = EventBus(role=role)
        dispatcher = EventDispatcher(
            host,
            blueprint=blueprint,
            infra=infra,
            poll_ctrl=event_bus,
        )
        self._event_bus = event_bus
        self._dispatcher = dispatcher

    @property
    def event_bus(self) -> Optional[EventBus]:
        """Return the event bus instance, or None before setup()."""
        return self._event_bus

    @property
    def dispatcher(self) -> Optional["EventDispatcher"]:
        """Return the event dispatcher, or None before setup()."""
        return self._dispatcher

    @property
    def subscribed_topics(self) -> list[str]:
        return self._subscribed_topics

    async def enqueue(self, event: Any) -> None:
        """Forward an event to the internal event bus."""
        if self._event_bus is None:
            return
        await self._event_bus.enqueue(event)

    @property
    def is_running(self) -> bool:
        """Whether the underlying event bus is running."""
        return self._event_bus is not None and self._event_bus.is_running

    async def start(self, session: Any = None) -> None:
        host = self._host
        if self._event_bus is None:
            return
        member_name = host.member_name or "?"
        team_logger.info("[{}] coordination starting", member_name)

        sess_mgr = host.session_manager
        infra = host.infra
        resources = host.resources
        blueprint = host.blueprint

        from openjiuwen.core.common.logging.utils import set_member_id

        set_member_id(member_name)
        if session is not None and not isinstance(session, AgentTeamSession):
            team_logger.warning(
                "[{}] TeamAgent expects AgentTeamSession; got {}. "
                "Please invoke via Runner.run_agent_team_streaming.",
                member_name,
                type(session).__name__,
            )
        if infra.team_backend:
            await infra.team_backend.db.initialize()
        if session is not None:
            await sess_mgr.bind_session(session)
        else:
            sess_mgr.unbind_session()

        if host.role == TeamRole.LEADER and infra.team_backend:
            existing = await infra.team_backend.db.team.get_team(infra.team_backend.team_name)
            if existing is not None:
                non_leader_members = await infra.team_backend.list_members()
                if non_leader_members and all(m.status == MemberStatus.SHUTDOWN.value for m in non_leader_members):
                    team_logger.warning(
                        "[{}] team {} found with all teammates in SHUTDOWN — finalizing prior incomplete cleanup",
                        member_name,
                        infra.team_backend.team_name,
                    )
                    await infra.team_backend.clean_team()
                else:
                    await host.recover_team()

        if infra.workspace_manager and not infra.workspace_initialized:
            spec = blueprint.spec if blueprint else None
            remote_url = spec.workspace.remote_url if spec and spec.workspace else None
            await infra.workspace_manager.initialize(remote_url=remote_url)
            infra.workspace_initialized = True

        # Wire up the team memory toolkit once the harness and workspace
        # are ready. init_toolkit is idempotent; calling it on every start
        # is safe.
        memory_manager = resources.memory_manager
        harness = resources.harness
        if memory_manager and harness:
            success = await memory_manager.init_toolkit()
            if success:
                harness.register_member_tools(memory_manager)
                if memory_manager.extraction_model is None:
                    memory_manager.set_extraction_model(harness.model)
                await harness.inject_member_memory(
                    memory_manager,
                    query=host.state.pending_user_query or "",
                )

        await host.update_status(MemberStatus.READY)
        if not self._event_bus.is_running:
            if self._dispatcher is None:
                raise RuntimeError("CoordinationKernel.start() requires setup() before start()")
            await self._event_bus.start(wake_callback=self._dispatcher.dispatch)
        if infra.messager:
            team_name = host.team_name
            if team_name and not self._subscribed_topics:
                await self.subscribe_transport(team_name)

    async def pause(self) -> None:
        host = self._host
        team_logger.info("[{}] coordination pausing (persistent)", host.member_name or "?")
        await self.drain_agent_task()
        host.persist_allocator_state()
        if host.role == TeamRole.LEADER:
            await self._mark_live_teammates_paused()
            await host.spawn_manager.cancel_recovery_tasks()
            await host.spawn_manager.shutdown_all_handles()
            self._persist_team_lifecycle("paused")
        messager = host.infra.messager
        if messager and host.role == TeamRole.LEADER:
            from openjiuwen.agent_teams.schema.events import (
                EventMessage,
                TeamStandbyEvent,
                TeamTopic,
            )
            from openjiuwen.agent_teams.context import get_session_id

            team_name = host.team_name
            if team_name:
                try:
                    await messager.publish(
                        topic_id=TeamTopic.TEAM.build(get_session_id(), team_name),
                        message=EventMessage.from_event(TeamStandbyEvent(team_name=team_name)),
                    )
                except Exception as e:
                    team_logger.error("Failed to publish TEAM_STANDBY: {}", e)
        await self.unsubscribe_transport()
        if self._event_bus:
            await self._event_bus.stop()
        self.close_stream()
        host.session_manager.release_session()

    async def _mark_live_teammates_paused(self) -> None:
        """Persist PAUSED status for every spawned teammate before tearing down handles.

        Members that were never started (UNSTARTED) or already gone (SHUTDOWN)
        keep their existing status — PAUSED only applies to runtime that was
        actually live during this round.
        """
        host = self._host
        team_backend = host.infra.team_backend
        team_name = host.team_name
        if not team_backend or not team_name:
            return
        spawned = set(host.spawn_manager.spawned_handles.keys())
        if not spawned:
            return
        leader = host.member_name
        members = await team_backend.list_members()
        for member in members:
            if member.member_name == leader:
                continue
            if member.member_name not in spawned:
                continue
            try:
                current = MemberStatus(member.status)
            except ValueError:
                continue
            if current in {MemberStatus.UNSTARTED, MemberStatus.SHUTDOWN}:
                continue
            try:
                await team_backend.db.member.update_member_status(
                    member.member_name,
                    team_name,
                    MemberStatus.PAUSED.value,
                )
            except Exception as e:
                team_logger.error(
                    "[{}] failed to mark teammate {} PAUSED: {}",
                    leader or "?",
                    member.member_name,
                    e,
                )

    def _persist_team_lifecycle(self, lifecycle: str) -> None:
        """Write the team lifecycle hint into the session's per-team bucket."""
        host = self._host
        session = host.session_manager.team_session
        team_name = host.team_name
        if session is None or team_name is None:
            return
        from openjiuwen.agent_teams.runtime.metadata import merge_team_namespace

        try:
            merge_team_namespace(session, team_name, {"lifecycle": lifecycle})
        except Exception as e:
            team_logger.warning(
                "[{}] failed to persist team lifecycle: {}",
                host.member_name or "?",
                e,
            )

    async def stop(self) -> None:
        host = self._host
        team_logger.info("[{}] coordination stopping", host.member_name or "?")
        await self.drain_agent_task()
        host.persist_allocator_state()
        await self.unsubscribe_transport()
        await host.spawn_manager.cancel_recovery_tasks()
        await host.spawn_manager.shutdown_all_handles()
        memory_manager = host.resources.memory_manager
        if memory_manager:
            await memory_manager.close()
        if self._event_bus is None:
            return
        await self._event_bus.stop()
        self.close_stream()
        host.session_manager.release_session()

    async def subscribe_transport(self, team_name: str) -> None:
        host = self._host
        messager = host.infra.messager
        if not messager or not self._event_bus:
            return
        from openjiuwen.agent_teams.schema.events import EventMessage, TeamTopic
        from openjiuwen.agent_teams.context import get_session_id

        local_member_name = host.member_name or ""

        async def _filter_self(event: EventMessage) -> None:
            for listener in host.state.event_listeners:
                try:
                    await listener(event)
                except Exception as e:
                    team_logger.error("Event listener error: {}", e)
            if local_member_name and event.sender_id == local_member_name:
                team_logger.debug("ignoring self-published event: {}", event.event_type)
                return
            await self._event_bus.enqueue(event)

        session_id = get_session_id()
        await messager.register_direct_message_handler(self._event_bus.enqueue)
        for topic in TeamTopic:
            topic_str = topic.build(session_id, team_name)
            await messager.subscribe(topic_str, _filter_self)
            self._subscribed_topics.append(topic_str)

    async def unsubscribe_transport(self) -> None:
        host = self._host
        messager = host.infra.messager
        if not messager:
            return
        try:
            await messager.unregister_direct_message_handler()
        except Exception:
            team_logger.debug("failed to unregister direct message handler during cleanup")
        for topic in self._subscribed_topics:
            try:
                await messager.unsubscribe(topic)
            except Exception:
                team_logger.debug("failed to unsubscribe topic {} during cleanup", topic)
        self._subscribed_topics.clear()

    async def enqueue_user_input(self, inputs: Any) -> None:
        """Push a user-input inner event onto the bus.

        Accepts either a string (used directly as content) or a dict with
        a 'query' key (used to extract the content). The unified entry
        point used by both invoke()/stream() and interact().
        """
        if self._event_bus is None:
            return
        query = inputs.get("query", "") if isinstance(inputs, dict) else inputs
        await self._event_bus.enqueue(
            InnerEventMessage(
                event_type=InnerEventType.USER_INPUT,
                payload={"content": query},
            )
        )

    async def enqueue_mailbox_after_first_iteration(self) -> None:
        host = self._host
        if host.role == TeamRole.LEADER:
            return
        gate = host.resources.first_iter_gate
        if gate is None or self._event_bus is None:
            return
        await gate.wait()
        await self._event_bus.enqueue(
            InnerEventMessage(event_type=InnerEventType.POLL_MAILBOX),
        )

    async def drain_agent_task(self) -> None:
        await self._host.stream_controller.drain_agent_task()

    def close_stream(self) -> None:
        self._host.stream_controller.close_stream()

    async def wake_mailbox_if_interrupt_cleared(self) -> None:
        host = self._host
        if host.role != TeamRole.TEAMMATE:
            return
        if host.has_pending_interrupt():
            return
        if self._event_bus is None:
            return
        await self._event_bus.enqueue(
            InnerEventMessage(event_type=InnerEventType.POLL_MAILBOX),
        )

    async def finalize_round(self) -> None:
        host = self._host
        team_member = host.state.team_member
        shutdown_requested = team_member is not None and await team_member.status() == MemberStatus.SHUTDOWN_REQUESTED
        memory_manager = host.resources.memory_manager
        if memory_manager:
            await memory_manager.extract_after_round()
        if host.lifecycle == "persistent" and not shutdown_requested:
            await self.pause()
            if team_member:
                await team_member.update_status(MemberStatus.READY)
        else:
            await self.stop()
            if team_member:
                await team_member.update_status(MemberStatus.SHUTDOWN)
        host.stream_controller.stream_queue = None
