# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Event dispatcher for TeamAgent coordination events.

Trigger rules (coarse filters: agent_ready, inner-vs-transport, role
gating) live here; behavior lives in scenario-scoped handlers under
:mod:`coordination.handlers`. Each handler exposes ``get_callbacks()``
yielding the ``event_key -> bound method`` mapping that we feed to a
private :class:`AsyncCallbackFramework` instance. Multiple handlers
may register the same ``event_key`` — the framework fans out callbacks
in registration order (sorted stably by ``priority=0``).

Note on exception semantics: ``AsyncCallbackFramework.trigger()``
swallows callback exceptions (logs + continues), unlike the previous
fail-fast dispatch. Handlers must log their own errors via
``team_logger.error(..., exc_info=True)`` rather than relying on
framework swallowing as silent error handling.
"""

from __future__ import annotations

from typing import (
    Any,
    ClassVar,
    Protocol,
    runtime_checkable,
)

from openjiuwen.agent_teams.agent.blueprint import TeamAgentBlueprint
from openjiuwen.agent_teams.agent.coordination.event_bus import (
    CoordinationEvent,
    InnerEventMessage,
    InnerEventType,
)
from openjiuwen.agent_teams.agent.coordination.handlers import (
    AgentLifecycleHandler,
    MemberHandler,
    MessageHandler,
    ScheduledStaleTaskHandler,
    ScheduledTaskBoardHandler,
    StaleTaskHandler,
    TaskBoardHandler,
    TeamCompletionHandler,
    WorkflowHandler,
)
from openjiuwen.agent_teams.agent.infra import TeamInfra
from openjiuwen.agent_teams.schema.events import TeamEvent
from openjiuwen.agent_teams.schema.team import TeamRole
from openjiuwen.core.common.logging import team_logger
from openjiuwen.core.runner.callback import AsyncCallbackFramework


@runtime_checkable
class AgentRoundController(Protocol):
    """Round-level control surface against the owning agent.

    Everything dispatched by this protocol ultimately drives the
    underlying ``TeamHarness`` / ``DeepAgent``: status queries on the
    current round, plus the mutation primitives that feed input into
    or tear down the round.

    Only the entry points that handlers actually call are declared.
    ``start_agent`` / ``follow_up`` / ``steer`` are intentionally not
    part of the public surface — handlers go through ``deliver_input``
    and let the host pick the right primitive based on round state.
    """

    def is_agent_ready(self) -> bool:
        """Return whether the agent has been fully initialized."""
        ...

    def is_agent_running(self) -> bool:
        """Return whether the agent is in an active round."""
        ...

    def idle_seconds(self) -> float | None:
        """Return seconds since this member last settled into runtime IDLE.

        ``None`` while the member is mid-round or has never settled. Measured
        off a process-local monotonic clock re-based on resume, so a paused
        team never inflates it.
        """
        ...

    def has_in_flight_round(self) -> bool:
        """Return whether an agent round is scheduled and not yet finalized."""
        ...

    def has_pending_interrupt(self) -> bool:
        """Return whether an unresolved tool interrupt is pending."""
        ...

    async def cancel_agent(self) -> None:
        """Cancel the running agent task."""
        ...

    async def deliver_input(self, content: Any, *, use_steer: bool = True) -> None:
        """Guarantee that content reaches the DeepAgent regardless of state."""
        ...

    async def resume_interrupt(self, user_input: Any) -> None:
        """Resume a pending HITL interrupt with structured input."""
        ...


@runtime_checkable
class TeamLifecycleController(Protocol):
    """TeamAgent-level lifecycle effects that span multiple managers.

    ``shutdown_self`` is invoked when the team has been dissolved and a
    non-leader member must abandon its loop. ``conclude_completed_round``
    ends a completed persistent team's leader stream so the Runner finally
    can pause it. Both coordinate across stream / session / member state,
    so they belong here rather than on the round controller.
    """

    async def shutdown_self(self) -> None:
        """Force-shutdown this agent in response to team dissolution."""
        ...

    async def conclude_completed_round(self, member_count: int, task_count: int) -> None:
        """Emit a team-completed marker chunk, then close the leader stream."""
        ...

    async def finalize_non_contributing_worktrees(self) -> None:
        """Remove current-session teammate worktrees that did not contribute commits."""
        ...


@runtime_checkable
class PollController(Protocol):
    """Periodic-poll control surface owned by the coordination's own
    event bus.

    Handlers reach into this protocol directly instead of bouncing the
    pause/resume call through the host; the bus already knows how to
    suspend its mailbox/task poll tasks, no TeamAgent indirection
    needed.
    """

    async def pause_polls(self) -> None:
        """Pause periodic polling on the event bus."""
        ...

    async def resume_polls(self) -> None:
        """Resume periodic polling on the event bus."""
        ...


@runtime_checkable
class DispatcherHost(AgentRoundController, TeamLifecycleController, Protocol):
    """Composite host contract used by the kernel and the dispatcher.

    Combines the round controller and the team lifecycle controller —
    the two surfaces the host has to expose for coordination to drive
    behavior on the owning agent. Poll control no longer goes through
    the host; handlers receive a :class:`PollController` directly.
    """


class EventDispatcher:
    """Routes coordination events to scenario-scoped handler methods.

    Owns trigger rules plus a private :class:`AsyncCallbackFramework`
    instance. Per-team registry isolation: framework is local to this
    dispatcher so coordination events never enter the global
    ``Runner.callback_framework``.

    The seven scenario handlers are exposed as public attributes
    (``lifecycle`` / ``member`` / ``message`` / ``task_board`` /
    ``stale_task`` / ``team_completion`` / ``workflow``) for direct
    access in tests.
    """

    # Dispatch-mode handler variants (F_62). Selected here, at assembly —
    # never inside a handler method. Literal tables in the F_57 spirit: the
    # mode set is closed, an unknown mode is a loud KeyError, not a silent
    # autonomous fallback.
    _TASK_BOARD_CLASS: ClassVar[dict[str, type[TaskBoardHandler]]] = {
        "autonomous": TaskBoardHandler,
        "scheduled": ScheduledTaskBoardHandler,
    }
    _STALE_TASK_CLASS: ClassVar[dict[str, type[StaleTaskHandler]]] = {
        "autonomous": StaleTaskHandler,
        "scheduled": ScheduledStaleTaskHandler,
    }

    def __init__(
        self,
        host: DispatcherHost,
        blueprint: TeamAgentBlueprint,
        infra: TeamInfra,
        poll_ctrl: PollController,
        *,
        dispatch_mode: str = "autonomous",
    ) -> None:
        # dispatch() only needs the round-readiness query; the lifecycle
        # surface is the handlers' concern, not ours. Alias under
        # ``_round`` to document the actual dependency.
        self._round: AgentRoundController = host
        self._blueprint = blueprint
        self._infra = infra
        self._dispatch_mode = dispatch_mode
        # Real wakes that arrive before the harness exists used to be
        # task_done'd and permanently lost (only POLL_* would retry later).
        # Buffer them and flush once ready.
        self._deferred_wakes: list[CoordinationEvent] = []

        self.lifecycle = AgentLifecycleHandler(host, blueprint, infra, poll_ctrl)
        self.member = MemberHandler(host, blueprint, infra, poll_ctrl)
        self.message = MessageHandler(host, blueprint, infra, poll_ctrl)
        # task_board / stale_task are the dispatch-mode-owned handler pair
        # (F_62): the mode is static spec configuration, so the variant is
        # chosen right here at construction, for every role alike.
        self.task_board = self._TASK_BOARD_CLASS[dispatch_mode](host, blueprint, infra, poll_ctrl)
        self.stale_task = self._STALE_TASK_CLASS[dispatch_mode](host, blueprint, infra, poll_ctrl)
        self.team_completion = TeamCompletionHandler(host, blueprint, infra, poll_ctrl)
        # Swarmflow spectator narration. Listens only for WORKFLOW_PROGRESS
        # (a dedicated event_key, no fan-out overlap), so its registration
        # position is not load-bearing.
        self.workflow = WorkflowHandler(host, blueprint, infra, poll_ctrl)

        # The reliability handler (leader-side remediation + team-level
        # ping-pong) mounts only when the team opts into the reliability
        # framework. It is appended after team_completion but does not listen
        # on POLL_TASK, so the completion ordering below is unaffected; on
        # MESSAGE / BROADCAST it fans out after MessageHandler.
        self.reliability = None
        reliability_cfg = getattr(blueprint.spec, "reliability", None)
        if reliability_cfg is not None and reliability_cfg.enabled:
            from openjiuwen.agent_teams.reliability.factory import (
                build_pingpong_detector,
                build_remediation_policy,
            )
            from openjiuwen.agent_teams.reliability.handler import ReliabilityHandler

            self.reliability = ReliabilityHandler(
                host,
                blueprint,
                infra,
                poll_ctrl,
                policy=build_remediation_policy(reliability_cfg),
                pingpong=build_pingpong_detector(reliability_cfg),
            )

        self._framework = AsyncCallbackFramework(
            enable_metrics=False,
            enable_logging=True,
        )
        # Register order matters for fan-out on shared event_keys.
        # MEMBER_SHUTDOWN is the example: MemberHandler.on_member_event
        # processes the lifecycle state, then
        # MessageHandler.on_member_shutdown_drain flushes the mailbox.
        # Same priority (default 0) → Python's stable sort keeps this
        # registration order.
        # team_completion is registered last so its POLL_TASK callback
        # fans out after the stale_task handler's: a stale sweep may deliver
        # input and make the leader non-idle, and the completion check
        # must see that before deciding the team is done.
        handlers = [
            self.lifecycle,
            self.member,
            self.message,
            self.task_board,
            self.stale_task,
            self.team_completion,
            self.workflow,
        ]
        if self.reliability is not None:
            handlers.append(self.reliability)

        for handler in handlers:
            for event_key, callback in handler.get_callbacks().items():
                self._framework.register_sync(event_key, callback)

    @property
    def dispatch_mode(self) -> str:
        """The dispatch mode this handler set was assembled for."""
        return self._dispatch_mode

    # Periodic / teardown signals: safe to drop while not ready (POLL_*
    # re-fire on a timer; SHUTDOWN is handled by the bus loop itself).
    _DROP_WHEN_NOT_READY: ClassVar[frozenset[InnerEventType]] = frozenset(
        {
            InnerEventType.POLL_TASK,
            InnerEventType.POLL_MAILBOX,
            InnerEventType.SHUTDOWN,
        }
    )
    _MAX_DEFERRED_WAKES: ClassVar[int] = 64

    def _should_defer_when_not_ready(self, event: CoordinationEvent) -> bool:
        if isinstance(event, InnerEventMessage):
            return event.event_type not in self._DROP_WHEN_NOT_READY
        return True

    def _defer_wake(self, event: CoordinationEvent) -> None:
        if len(self._deferred_wakes) >= self._MAX_DEFERRED_WAKES:
            dropped = self._deferred_wakes.pop(0)
            team_logger.warning(
                "deferred wake buffer full; dropping oldest event type={}",
                getattr(dropped, "event_type", type(dropped).__name__),
            )
        self._deferred_wakes.append(event)
        team_logger.debug(
            "agent not ready, deferring coordination wake: type={}",
            getattr(event, "event_type", type(event).__name__),
        )

    async def flush_deferred(self) -> None:
        """Replay wakes buffered while the harness was not ready."""
        if not self._round.is_agent_ready() or not self._deferred_wakes:
            return
        pending = self._deferred_wakes
        self._deferred_wakes = []
        team_logger.info(
            "flushing {} deferred coordination wake(s)",
            len(pending),
        )
        for event in pending:
            await self._dispatch_ready(event)

    async def dispatch(self, event: CoordinationEvent) -> None:
        """Wake-up entry. Applies coarse rules, then triggers framework."""
        if not self._round.is_agent_ready():
            if self._should_defer_when_not_ready(event):
                self._defer_wake(event)
            else:
                team_logger.debug("agent not ready, skipping coordination wake")
            return
        # Prefer any buffered wake before a newly arrived one so ordering
        # across the not-ready window stays FIFO.
        if self._deferred_wakes:
            await self.flush_deferred()
        await self._dispatch_ready(event)

    async def _dispatch_ready(self, event: CoordinationEvent) -> None:
        """Dispatch after the readiness gate (and any deferred flush)."""
        role = self._blueprint.role

        if isinstance(event, InnerEventMessage):
            # Human agents must never autonomously poll, but USER_INPUT is
            # legitimate: it is the avatar's controller driving its LLM,
            # enqueued by ``HumanAgentInbox`` (``$<name>`` drive →
            # ``TeamAgent.interact`` → USER_INPUT) so the avatar consumes
            # it inside its own loop after the harness has started — never
            # a reach-in to a not-yet-started harness. Only the autonomous
            # POLL_* branches stay muted for a human agent.
            if role == TeamRole.HUMAN_AGENT and event.event_type in (
                InnerEventType.POLL_TASK,
                InnerEventType.POLL_MAILBOX,
            ):
                return
            team_logger.debug("inner event received: type={}, payload={}", event.event_type, event.payload)
            await self._framework.trigger(event.event_type.value, event)
            return

        # --- Transport events (cross-process EventMessage) ---
        if not self._blueprint.member_name:
            team_logger.debug("no member_name, skipping transport event")
            return

        # Human agents are user avatars. Their LLM is driven by two
        # input sources: (1) their controller's instructions via
        # HumanAgentInbox, enqueued as a USER_INPUT inner event (handled
        # in the InnerEventMessage branch above), and (2) team events
        # that concern the avatar, delivered into the harness and
        # rendered as "notification for the controller" (see F_14). The
        # avatar prompt strictly forbids autonomous action on the
        # latter. The transport-event whitelist below has two groups:
        #
        # Lifecycle (drive the avatar's own teardown / standby, no
        # autonomous round involved): CLEANED tears the agent down with
        # the team; MEMBER_SHUTDOWN tears the avatar down on its own
        # shutdown request (a human agent has no autonomous round to
        # consume the shutdown message the way a teammate does, so it
        # must collapse directly); MEMBER_CANCELED routes to
        # cancel_agent; STANDBY stays whitelisted for parity with the
        # teammate path — its on_standby pause_polls is a no-op for a
        # human agent, whose bus never starts periodic poll timers in
        # the first place (see EventBus._start_poll_tasks).
        #
        # Team events rendered for the controller: MESSAGE / BROADCAST
        # reach MessageHandler (role-aware hitt.msg_received_for_human),
        # TASK_CLAIMED reaches TaskBoardHandler's self-assignment branch
        # (hitt.task_assigned_to_self_human). Everything else stays muted
        # — notably the task-board survey events (TASK_CREATED /
        # TASK_UPDATED / ... → _nudge_idle_agent) and stale-claim /
        # member-status nudges, which would push the avatar to
        # autonomously scan the board for claimable work.
        if role == TeamRole.HUMAN_AGENT:
            if event.event_type not in (
                TeamEvent.CLEANED,
                TeamEvent.MEMBER_SHUTDOWN,
                TeamEvent.MEMBER_CANCELED,
                TeamEvent.STANDBY,
                TeamEvent.MESSAGE,
                TeamEvent.BROADCAST,
                TeamEvent.TASK_CLAIMED,
            ):
                return

        await self._framework.trigger(event.event_type, event)
