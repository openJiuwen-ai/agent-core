# coding: utf-8
"""Coordination lifecycle and transport wiring for TeamAgent."""

from __future__ import annotations

from typing import (
    TYPE_CHECKING,
    Any,
)

from openjiuwen.agent_teams.agent.coordinator import (
    InnerEventMessage,
    InnerEventType,
)
from openjiuwen.agent_teams.schema.status import MemberStatus
from openjiuwen.agent_teams.schema.team import TeamRole
from openjiuwen.core.common.logging import team_logger
from openjiuwen.core.session.agent_team import Session as AgentTeamSession

if TYPE_CHECKING:
    from openjiuwen.agent_teams.agent.team_agent import TeamAgent


class CoordinationManager:
    """Owns the coordination loop's start/pause/stop lifecycle and the
    messager transport (subscribe/unsubscribe) wiring.

    Holds a back-reference to the owning TeamAgent because the lifecycle
    coordinates across most of TeamAgent's collaborators (configurator,
    stream_controller, session_manager, recovery_manager, spawn_manager,
    coordination_loop, team_member). Centralizing them here keeps
    TeamAgent focused on the public BaseAgent contract.
    """

    def __init__(self, host: "TeamAgent") -> None:
        self._host = host
        self._subscribed_topics: list[str] = []

    @property
    def subscribed_topics(self) -> list[str]:
        return self._subscribed_topics

    async def start(self, session: Any = None) -> None:
        host = self._host
        if host._coordination_loop is None:
            return
        member_name = host._member_name() or "?"
        team_logger.info("[{}] coordination starting", member_name)

        sess_mgr = host._session_manager
        rm = host._recovery_manager
        configurator = host._configurator

        sess_mgr.session_id = session.get_session_id() if session else None
        if sess_mgr.session_id:
            from openjiuwen.agent_teams.spawn.context import set_session_id

            set_session_id(sess_mgr.session_id)
        from openjiuwen.core.common.logging.utils import set_member_id

        set_member_id(member_name)
        if session is not None:
            if isinstance(session, AgentTeamSession):
                sess_mgr.team_session = session
            else:
                team_logger.warning(
                    "[{}] TeamAgent expects AgentTeamSession; got {}. "
                    "Please invoke via Runner.run_agent_team_streaming.",
                    member_name,
                    type(session).__name__,
                )
        if session and configurator.spec and host.role == TeamRole.LEADER:
            rm.persist_leader_config(session)
        if configurator.team_backend:
            await configurator.team_backend.db.initialize()
            await configurator.team_backend.db.create_cur_session_tables()

        if host.role == TeamRole.LEADER and configurator.team_backend:
            existing = await configurator.team_backend.db.team.get_team(configurator.team_backend.team_name)
            if existing is not None:
                non_leader_members = await configurator.team_backend.list_members()
                if non_leader_members and all(m.status == MemberStatus.SHUTDOWN.value for m in non_leader_members):
                    team_logger.warning(
                        "[{}] team {} found with all teammates in SHUTDOWN — finalizing prior incomplete cleanup",
                        member_name,
                        configurator.team_backend.team_name,
                    )
                    await configurator.team_backend.clean_team()
                else:
                    await host.recover_team()

        if configurator.workspace_manager and not configurator.workspace_initialized:
            remote_url = (
                configurator.spec.workspace.remote_url if configurator.spec and configurator.spec.workspace else None
            )
            await configurator.workspace_manager.initialize(remote_url=remote_url)
            configurator.workspace_initialized = True

        # Wire up the team memory toolkit once the DeepAgent and workspace
        # are ready. init_toolkit is idempotent; calling it on every start
        # is safe.
        memory_manager = configurator.memory_manager
        if memory_manager and configurator.deep_agent:
            success = await memory_manager.init_toolkit()
            if success:
                memory_manager.register_tools(configurator.deep_agent)
                if memory_manager.extraction_model is None and configurator.deep_agent.deep_config:
                    memory_manager.set_extraction_model(configurator.deep_agent.deep_config.model)
                await memory_manager.load_and_inject(
                    configurator.deep_agent,
                    query=host._pending_user_query or "",
                )

        await host._update_status(MemberStatus.READY)
        if not host._coordination_loop.is_running:
            await host._coordination_loop.start()
        if configurator.messager:
            team_name = host._team_name()
            if team_name and not self._subscribed_topics:
                await self.subscribe_transport(team_name)

    async def pause(self) -> None:
        host = self._host
        team_logger.info("[{}] coordination pausing (persistent)", host._member_name() or "?")
        await self.drain_agent_task()
        host._persist_allocator_state()
        if host._configurator.messager and host.role == TeamRole.LEADER:
            from openjiuwen.agent_teams.schema.events import (
                EventMessage,
                TeamStandbyEvent,
                TeamTopic,
            )
            from openjiuwen.agent_teams.spawn.context import get_session_id

            team_name = host._team_name()
            if team_name:
                try:
                    await host._configurator.messager.publish(
                        topic_id=TeamTopic.TEAM.build(get_session_id(), team_name),
                        message=EventMessage.from_event(TeamStandbyEvent(team_name=team_name)),
                    )
                except Exception as e:
                    team_logger.error("Failed to publish TEAM_STANDBY: {}", e)
        await self.unsubscribe_transport()
        if host._coordination_loop:
            await host._coordination_loop.stop()
        self.close_stream()
        host._session_manager.team_session = None

    async def stop(self) -> None:
        host = self._host
        team_logger.info("[{}] coordination stopping", host._member_name() or "?")
        await self.drain_agent_task()
        host._persist_allocator_state()
        await self.unsubscribe_transport()
        await host._spawn_manager.cancel_recovery_tasks()
        await host._spawn_manager.shutdown_all_handles()
        memory_manager = host._configurator.memory_manager
        if memory_manager:
            await memory_manager.close()
        if host._coordination_loop is None:
            return
        await host._coordination_loop.stop()
        self.close_stream()
        host._session_manager.team_session = None

    async def subscribe_transport(self, team_name: str) -> None:
        host = self._host
        if not host._configurator.messager or not host._coordination_loop:
            return
        from openjiuwen.agent_teams.schema.events import EventMessage, TeamTopic
        from openjiuwen.agent_teams.spawn.context import get_session_id

        local_member_name = host._member_name() or ""

        async def _filter_self(event: EventMessage) -> None:
            for listener in host._event_listeners:
                try:
                    await listener(event)
                except Exception as e:
                    team_logger.error("Event listener error: {}", e)
            if local_member_name and event.sender_id == local_member_name:
                team_logger.debug("ignoring self-published event: {}", event.event_type)
                return
            await host._coordination_loop.enqueue(event)

        session_id = get_session_id()
        await host._configurator.messager.register_direct_message_handler(
            host._coordination_loop.enqueue,
        )
        for topic in TeamTopic:
            topic_str = topic.build(session_id, team_name)
            await host._configurator.messager.subscribe(topic_str, _filter_self)
            self._subscribed_topics.append(topic_str)

    async def unsubscribe_transport(self) -> None:
        host = self._host
        if not host._configurator.messager:
            return
        try:
            await host._configurator.messager.unregister_direct_message_handler()
        except Exception:
            team_logger.debug("failed to unregister direct message handler during cleanup")
        for topic in self._subscribed_topics:
            try:
                await host._configurator.messager.unsubscribe(topic)
            except Exception:
                team_logger.debug("failed to unsubscribe topic {} during cleanup", topic)
        self._subscribed_topics.clear()

    async def enqueue_user_input(self, inputs: Any) -> None:
        host = self._host
        if host._coordination_loop is None:
            return
        query = inputs.get("query", "") if isinstance(inputs, dict) else inputs
        await host._coordination_loop.enqueue(
            InnerEventMessage(
                event_type=InnerEventType.USER_INPUT,
                payload={"content": query},
            )
        )

    async def enqueue_mailbox_after_first_iteration(self) -> None:
        host = self._host
        if host.role == TeamRole.LEADER:
            return
        if host._first_iter_gate is None or host._coordination_loop is None:
            return
        await host._first_iter_gate.wait()
        await host._coordination_loop.enqueue(
            InnerEventMessage(event_type=InnerEventType.POLL_MAILBOX),
        )

    async def drain_agent_task(self) -> None:
        await self._host._stream_controller.drain_agent_task()

    def close_stream(self) -> None:
        self._host._stream_controller.close_stream()

    async def wake_mailbox_if_interrupt_cleared(self) -> None:
        host = self._host
        if host.role != TeamRole.TEAMMATE:
            return
        if host.has_pending_interrupt():
            return
        if host._coordination_loop is None:
            return
        await host._coordination_loop.enqueue(
            InnerEventMessage(event_type=InnerEventType.POLL_MAILBOX),
        )

    async def finalize_round(self) -> None:
        host = self._host
        team_member = host._team_member
        shutdown_requested = team_member is not None and await team_member.status() == MemberStatus.SHUTDOWN_REQUESTED
        memory_manager = host._configurator.memory_manager
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
        host._stream_controller.stream_queue = None
