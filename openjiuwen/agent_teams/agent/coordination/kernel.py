# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

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
from openjiuwen.agent_teams.harness.state import HarnessState
from openjiuwen.agent_teams.schema.status import MemberStatus
from openjiuwen.agent_teams.schema.team import TeamRole
from openjiuwen.core.common.logging import team_logger
from openjiuwen.core.session.agent_team import Session as AgentTeamSession

if TYPE_CHECKING:
    from openjiuwen.agent_teams.agent.coordination.dispatcher import EventDispatcher
    from openjiuwen.agent_teams.agent.scheduling import TeamScheduler
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
        # Leader-only scheduled-dispatch decision engine (F_62). Constructed
        # dormant at setup; armed when the team's effective dispatch mode is
        # "scheduled" (build_team choice / recovery restore).
        self._scheduler: Optional["TeamScheduler"] = None
        # Lifecycle state machine for pause/stop idempotency.
        # Transitions: idle -> running (start) -> paused (pause) -> stopped (stop).
        # ``stopped`` is terminal for this kernel instance; subsequent
        # pause/stop calls become no-ops so the Runner-level finally can
        # safely re-trigger them after an external stop_coordination.
        self._lifecycle_state: str = "idle"

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
        # F_62: the dispatch mode is static spec configuration — every role
        # assembles its mode-owned handler variants directly from it.
        dispatcher = EventDispatcher(
            host,
            blueprint=blueprint,
            infra=infra,
            poll_ctrl=event_bus,
            dispatch_mode=blueprint.spec.dispatch_mode,
        )
        self._event_bus = event_bus
        self._dispatcher = dispatcher
        # The scheduler exists only where it has a job: the leader of a
        # scheduled-dispatch team. It stays dormant until the team
        # materializes (build_team / an existing team row at start).
        if role == TeamRole.LEADER and blueprint.spec.dispatch_mode == "scheduled":
            from openjiuwen.agent_teams.agent.scheduling import TeamScheduler

            self._scheduler = TeamScheduler(host, blueprint=blueprint, infra=infra)

    @property
    def event_bus(self) -> Optional[EventBus]:
        """Return the event bus instance, or None before setup()."""
        return self._event_bus

    @property
    def dispatcher(self) -> Optional["EventDispatcher"]:
        """Return the event dispatcher, or None before setup()."""
        return self._dispatcher

    @property
    def scheduler(self) -> Optional["TeamScheduler"]:
        """Return the leader's scheduled-dispatch engine, or None (non-leader)."""
        return self._scheduler

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
                "[{}] TeamAgent expects AgentTeamSession; got {}. Please invoke via Runner.run_agent_team_streaming.",
                member_name,
                type(session).__name__,
            )
        if infra.team_backend:
            await infra.team_backend.db.initialize()
        if session is not None:
            await sess_mgr.bind_session(session)
            memory_manager = resources.memory_manager
            if memory_manager is not None:
                memory_manager.bind_session_id(session.get_session_id())
            # Start the member runtime (native supervisor + child session) for
            # this run cycle, then attach the StreamController's output forwarder
            # + status mappers. Order matters: the controller consumes the
            # runtime's outputs, so the runtime must be started first.
            if resources.harness is not None:
                await resources.harness.start(team_session=session)
                await host.stream_controller.start()
        else:
            sess_mgr.release_session()
            memory_manager = resources.memory_manager
            if memory_manager is not None:
                memory_manager.bind_session_id(None)

        team_row_present = False
        if host.role == TeamRole.LEADER and infra.team_backend:
            existing = await infra.team_backend.db.team.get_team(infra.team_backend.team_name)
            if existing is not None:
                team_row_present = True
                non_leader_members = await infra.team_backend.list_member_roster()
                if non_leader_members and all(m.status == MemberStatus.SHUTDOWN.value for m in non_leader_members):
                    team_logger.warning(
                        "[{}] team {} found with all teammates in SHUTDOWN — finalizing prior incomplete cleanup",
                        member_name,
                        infra.team_backend.team_name,
                    )
                    await infra.team_backend.clean_team()
                    team_row_present = False
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
        # Re-base the idle clock before the poll timers come back. A member
        # that was already idle when the team paused keeps its idle stamp
        # while the monotonic clock runs through the entire pause window, and
        # — having no suspended round to resume — never re-enters IDLE to
        # re-stamp itself. Without this the first POLL_TASK of this run cycle
        # would read the whole pause as idle time and fire a bogus stall
        # nudge. No-op on a cold start (idle_since is None) and for a member
        # that paused mid-round (idle_since is None while BUSY).
        host.refresh_idle_baseline()
        if not self._event_bus.is_running:
            if self._dispatcher is None:
                raise RuntimeError("CoordinationKernel.start() requires setup() before start()")
            await self._event_bus.start(wake_callback=self._build_wake_callback())
        if infra.messager:
            team_name = host.team_name
            if team_name and not self._subscribed_topics:
                await self.subscribe_transport(team_name)
        # Re-arm the team-completion rising-edge guard on every start (cold
        # start / resume / recover) so each run cycle evaluates completion
        # independently — a resumed persistent team can conclude again.
        if self._dispatcher is not None:
            self._dispatcher.team_completion.rearm()
        # F_62: arm the scheduler when this run cycle starts against an
        # existing team (the scheduler only exists on scheduled-dispatch
        # leaders) — activation runs the recovery sweep (start pending
        # assignments, judge open reviews).
        if self._scheduler is not None and team_row_present:
            await self._scheduler.activate()
        self._lifecycle_state = "running"

    def _build_wake_callback(self):
        """Compose the bus wake callback: coordination first, scheduler second.

        Coordination stays a pure wake-up layer; the scheduler makes its
        decisions strictly after handlers observed the same event. Non-leader
        kernels have no scheduler and keep the bare dispatch path.
        """
        dispatcher = self._dispatcher
        scheduler = self._scheduler
        if scheduler is None:
            return dispatcher.dispatch

        async def _dispatch_then_schedule(event) -> None:
            await dispatcher.dispatch(event)
            await scheduler.on_event(event)

        return _dispatch_then_schedule

    async def notify_team_built(self) -> None:
        """Arm the scheduler once ``build_team`` materialized the team.

        Called by the host's ``on_team_built`` hook inside the build_team tool
        call, so a scheduled-dispatch team's scheduler is live before any
        teammate spawn or task creation — matching the prompt's promise that
        the framework starts assignees. No-op when there is no scheduler
        (autonomous teams, non-leader kernels).
        """
        if self._scheduler is None:
            return
        await self._scheduler.activate()
        # Warm resume: a lifecycle pause left this member's round suspended at a
        # clean boundary. Now that the session, stream and event bus are back,
        # continue it in place instead of idling until a new message arrives.
        await self.resume_paused_round()

    async def pause(self) -> None:
        # Idempotent: ignore if not currently running. Pause is only a valid
        # transition from running; paused/stopped/idle short-circuit so the
        # Runner-level finally can safely call pause even after an external
        # stop_coordination has already torn things down.
        if self._lifecycle_state != "running":
            return
        host = self._host
        team_logger.info("[{}] coordination pausing (persistent)", host.member_name or "?")
        if self._scheduler is not None:
            self._scheduler.deactivate()
        # Pause, do not tear down: the round stops at a clean inner-iteration
        # boundary and stays resumable in place. This used to hard-cancel via
        # ``drain_agent_task`` → ``abort(immediate=True)``, which threw away
        # everything the member had done in the round it interrupted mid-way.
        await self.pause_agent_round()
        host.persist_allocator_state()
        # Extract team memories while the session is still bound and the DB
        # is accessible. Moved from finalize_round so extraction runs once
        # per run cycle instead of on every streaming round.
        memory_manager = host.resources.memory_manager
        if memory_manager:
            await memory_manager.extract_after_round()
        if host.role == TeamRole.LEADER:
            await self._mark_live_teammates(MemberStatus.PAUSED)
            await host.spawn_manager.cancel_recovery_tasks()
            await host.spawn_manager.shutdown_all_handles()
            self._persist_team_lifecycle("paused")
            # Make a later cold start (pause -> stop -> start) continue this
            # round rather than idle waiting for a new message.
            self._persist_pending_resume()
        messager = host.infra.messager
        if messager and host.role == TeamRole.LEADER:
            from openjiuwen.agent_teams.context import get_session_id
            from openjiuwen.agent_teams.schema.events import (
                EventMessage,
                TeamStandbyEvent,
                TeamTopic,
            )

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
        # team_member status update is owned by ``TeamRuntimeManager.finalize_member``
        # so persistence-layer status (lives across restarts) stays decoupled
        # from kernel runtime teardown (volatile). External stop_coordination
        # from leader path must not silently mark teammates SHUTDOWN — that
        # would trip the kernel.start ``all-SHUTDOWN -> clean_team`` guard and
        # delete a team that should be recoverable.
        self._lifecycle_state = "paused"

    async def _mark_live_teammates(self, target_status: MemberStatus) -> None:
        """Persist ``target_status`` for every spawned teammate before tearing down handles.

        Members that were never started (UNSTARTED) or already gone (SHUTDOWN)
        keep their existing status — the mark only applies to runtime that
        was actually live during this round. Used by both pause (writes
        PAUSED — natural round-end idle) and stop (writes STOPPED —
        external teardown without disbanding the team) so the persistence
        layer captures *why* the teammate runtime went away.
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
        members = await team_backend.list_member_roster()
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
                    target_status.value,
                )
            except Exception as e:
                team_logger.error(
                    "[{}] failed to mark teammate {} {}: {}",
                    leader or "?",
                    member.member_name,
                    target_status.value,
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

    def _persist_pending_resume(self) -> None:
        """Record what a later cold start needs to continue the paused round.

        ``pause`` suspends the round at a clean inner-iteration boundary and the
        run cycle's teardown commits its context. Persisting this marker makes
        ``pause -> stop -> start`` equivalent to ``pause -> resume``: the
        cold-started harness continues that round instead of idling until a new
        message arrives.

        The recorded query is not replayed into the continuation's context (that
        is restored from the checkpoint) — it drives the rounds that *follow* it:
        a task-plan continuation, or a failure retry.

        Leader-only, mirroring ``_persist_team_lifecycle`` — teammates are shut
        down by ``pause`` and re-spawned from the roster by ``recover_team``, so
        they have no in-memory round to carry across a restart.
        """
        host = self._host
        session = host.session_manager.team_session
        team_name = host.team_name
        harness = host.resources.harness
        if session is None or team_name is None or harness is None:
            return
        # Nothing was suspended (no in-flight round), so nothing to continue.
        if harness.state is not HarnessState.PAUSED:
            return
        from openjiuwen.agent_teams.runtime.metadata import merge_pending_resume

        try:
            merge_pending_resume(session, team_name, {"query": harness.paused_query or ""})
        except Exception as e:
            team_logger.warning(
                "[{}] failed to persist pending resume: {}",
                host.member_name or "?",
                e,
            )

    def _read_pending_resume(self) -> dict | None:
        """Return the persisted cold-resume marker, if any."""
        host = self._host
        session = host.session_manager.team_session
        team_name = host.team_name
        if session is None or team_name is None:
            return None
        from openjiuwen.agent_teams.runtime.metadata import read_pending_resume

        try:
            return read_pending_resume(session, team_name)
        except Exception as e:
            team_logger.warning(
                "[{}] failed to read pending resume: {}",
                host.member_name or "?",
                e,
            )
            return None

    def _clear_pending_resume(self) -> None:
        """Drop the cold-resume marker once the round has been continued."""
        host = self._host
        session = host.session_manager.team_session
        team_name = host.team_name
        if session is None or team_name is None:
            return
        from openjiuwen.agent_teams.runtime.metadata import clear_pending_resume

        try:
            clear_pending_resume(session, team_name)
        except Exception as e:
            team_logger.warning(
                "[{}] failed to clear pending resume: {}",
                host.member_name or "?",
                e,
            )

    async def stop(self) -> None:
        # Idempotent: terminal state. Pause -> stop is allowed (resources
        # still need close), running -> stop is the normal path, idle/stopped
        # are no-ops.
        if self._lifecycle_state in ("idle", "stopped"):
            return
        host = self._host
        team_logger.info("[{}] coordination stopping", host.member_name or "?")
        if self._scheduler is not None:
            self._scheduler.deactivate()
        await self.drain_agent_task()
        host.persist_allocator_state()
        # Final memory extraction before permanent teardown. Only extract
        # when transitioning directly from running (session still bound).
        # When coming from paused, extraction already happened in pause()
        # and the session is already released — the DB query would fail.
        memory_manager = host.resources.memory_manager
        if memory_manager and self._lifecycle_state == "running":
            await memory_manager.extract_after_round()
        if host.role == TeamRole.LEADER:
            # Mirror of the pause path: mark every spawned teammate so the
            # persistence layer captures why the runtime went away. STOPPED
            # is a non-disbanding teardown — ``recover_team`` re-spawns from
            # here. Done before ``shutdown_all_handles`` so the in-process
            # task cancellations cannot race with the status write.
            await self._mark_live_teammates(MemberStatus.STOPPED)
        await self.unsubscribe_transport()
        await host.spawn_manager.cancel_recovery_tasks()
        await host.spawn_manager.shutdown_all_handles()
        if memory_manager:
            await memory_manager.close()
        if self._event_bus is not None:
            await self._event_bus.stop()
        self.close_stream()
        # Permanent teardown (not round-end): stop the native and drop its
        # process-global sys_operation so a stopped/discarded member does not
        # leak it. The round-end ``finalize_round`` path only calls
        # ``harness.stop`` (kept for reuse on the same session); this stop is
        # where the runtime goes away. Done before ``release_session`` because
        # ``dispose`` tears the native down over its bound session, and it does
        # not always follow a ``finalize_round`` (e.g. external stop_team).
        if host.resources.harness is not None:
            await host.resources.harness.dispose()
        host.session_manager.release_session()
        # See pause(): team_member status update for the agent's own
        # ``team_member`` handle is owned by
        # ``TeamRuntimeManager.finalize_member`` so stop_coordination on a
        # teammate kernel only tears down runtime and leaves persisted
        # status alone — that is what makes stop -> recover possible.
        self._lifecycle_state = "stopped"

    async def subscribe_transport(self, team_name: str) -> None:
        host = self._host
        messager = host.infra.messager
        if not messager or not self._event_bus:
            return
        from openjiuwen.agent_teams.context import get_session_id
        from openjiuwen.agent_teams.schema.events import EventMessage, TeamTopic

        local_member_name = host.member_name or ""

        async def _filter_self(event: EventMessage) -> None:
            for listener in host.state.event_listeners:
                try:
                    await listener(event)
                except Exception as e:
                    team_logger.error("Event listener error: {}", e)
            if local_member_name and event.sender_id == local_member_name:
                team_logger.debug("ignoring self-published event: {}", event.event_type)
                # F_62: the scheduler must observe board changes the leader
                # process performed itself (create_task, settle). Coordination
                # must not re-process the echo, so the dropped event degrades
                # to a bare "board changed" scan hint on the same bus loop.
                if (
                    self._scheduler is not None
                    and self._scheduler.is_active
                    and str(event.event_type).startswith("task_")
                ):
                    await self._event_bus.enqueue(
                        InnerEventMessage(event_type=InnerEventType.SCHEDULER_SCAN)
                    )
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

    async def enqueue_initial_mailbox_poll(self) -> None:
        host = self._host
        if host.role == TeamRole.LEADER:
            return
        if self._event_bus is None:
            return
        # The member runtime is already started by ``start`` before coordination
        # runs, so the first mailbox poll is enqueued directly — the old
        # FirstIterationGate wait is gone with the single-supervisor model.
        await self._event_bus.enqueue(
            InnerEventMessage(event_type=InnerEventType.POLL_MAILBOX),
        )

    async def drain_agent_task(self) -> None:
        """Hard-cancel the in-flight round. Stop / teardown paths only."""
        await self._host.stream_controller.drain_agent_task()

    async def pause_agent_round(self) -> None:
        """Pause the in-flight round at its nearest inner iteration boundary."""
        await self._host.stream_controller.pause_agent()

    async def resume_paused_round(self) -> None:
        """Continue a round that a lifecycle pause suspended, if any.

        Two paths, both reached from the tail of ``start`` (session, stream and
        event bus are back by then):

        - **warm**: the harness is still PAUSED in this process — ``pause`` never
          stops it — so the suspended round is in memory.
        - **cold**: the harness was stopped and rebuilt, its context restored from
          the session checkpoint. The marker ``pause`` persisted names the round's
          originating query, making ``pause -> stop -> start`` behave exactly like
          ``pause -> resume``.

        Without this the member would idle until a new message arrived, silently
        dropping the work it was suspended mid-way through.
        """
        harness = self._host.resources.harness
        if harness is None:
            return

        if harness.state is HarnessState.PAUSED:
            team_logger.info(
                "[{}] resuming the paused round in place",
                self._host.member_name or "?",
            )
            await self._host.stream_controller.resume_agent()
            self._clear_pending_resume()
            return

        pending = self._read_pending_resume()
        if pending is None:
            return
        team_logger.info(
            "[{}] resuming the paused round from the session checkpoint",
            self._host.member_name or "?",
        )
        await self._host.stream_controller.resume_agent(
            query=str(pending.get("query") or ""),
        )
        self._clear_pending_resume()

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
        """Run round-end cleanup; lifecycle decisions live at the Runner layer.

        Purely tears down this cycle's runtime resources (stream controller
        and native harness). Memory extraction has moved to
        :meth:`pause` / :meth:`stop` so it runs once per run cycle
        rather than on every round.
        """
        host = self._host
        # Tear down this cycle's runtime: stop the controller's forwarder/status
        # mappers, then the member runtime (native supervisor). start() rebuilds
        # a fresh native next cycle.
        await host.stream_controller.stop()
        if host.resources.harness is not None:
            await host.resources.harness.stop()
        host.stream_controller.stream_queue = None
