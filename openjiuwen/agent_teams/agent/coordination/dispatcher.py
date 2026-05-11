# coding: utf-8
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
    StaleTaskHandler,
    TaskBoardHandler,
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

    Currently only ``shutdown_self`` — invoked when the team has been
    dissolved and a non-leader member must abandon its loop. Future
    cross-manager orchestration (e.g. ``recover_team``) belongs here
    rather than on the round controller.
    """

    async def shutdown_self(self) -> None:
        """Force-shutdown this agent in response to team dissolution."""
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

    The five scenario handlers are exposed as public attributes
    (``lifecycle`` / ``member`` / ``message`` / ``task_board`` /
    ``stale_task``) for direct access in tests.
    """

    def __init__(
        self,
        host: DispatcherHost,
        blueprint: TeamAgentBlueprint,
        infra: TeamInfra,
        poll_ctrl: PollController,
    ) -> None:
        # dispatch() only needs the round-readiness query; the lifecycle
        # surface is the handlers' concern, not ours. Alias under
        # ``_round`` to document the actual dependency.
        self._round: AgentRoundController = host
        self._blueprint = blueprint
        self._infra = infra
        # Throttle dict shared by reference between MemberHandler
        # (status-change path) and StaleTaskHandler (poll path) so the
        # same task cannot be nudged twice within one stale window
        # regardless of trigger source.
        stale_claim_throttle: dict[str, float] = {}

        self.lifecycle = AgentLifecycleHandler(host, blueprint, infra, poll_ctrl)
        self.member = MemberHandler(host, blueprint, infra, poll_ctrl, stale_claim_throttle)
        self.message = MessageHandler(host, blueprint, infra, poll_ctrl)
        self.task_board = TaskBoardHandler(host, blueprint, infra, poll_ctrl)
        self.stale_task = StaleTaskHandler(host, blueprint, infra, poll_ctrl, stale_claim_throttle)

        self._framework = AsyncCallbackFramework(
            enable_metrics=False,
            enable_logging=False,
        )
        # Register order matters for fan-out on shared event_keys.
        # MEMBER_SHUTDOWN is the example: MemberHandler.on_member_event
        # processes the lifecycle state, then
        # MessageHandler.on_member_shutdown_drain flushes the mailbox.
        # Same priority (default 0) → Python's stable sort keeps this
        # registration order.
        for handler in (
            self.lifecycle,
            self.member,
            self.message,
            self.task_board,
            self.stale_task,
        ):
            for event_key, callback in handler.get_callbacks().items():
                self._framework.register_sync(event_key, callback)

    async def dispatch(self, event: CoordinationEvent) -> None:
        """Wake-up entry. Applies coarse rules, then triggers framework."""
        if not self._round.is_agent_ready():
            team_logger.debug("agent not ready, skipping coordination wake")
            return

        role = self._blueprint.role

        if isinstance(event, InnerEventMessage):
            # Human agents must never autonomously poll. USER_INPUT
            # through this path comes from coordination bootstrap (the
            # leader's god-view input); a human agent never reaches
            # that path because the leader is the one whose invoke()
            # carries it. Defensively short-circuit polling branches.
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

        # Human agents are user avatars: only their corresponding user
        # (via HumanAgentInbox) drives their LLM. All other team-side
        # events (incoming messages, task assignments, stale-claim
        # nudges) must NOT autonomously poke the LLM. The whitelist:
        # CLEANED tears the agent down with the team; MEMBER_CANCELED
        # routes to cancel_agent (no autonomous nudge); STANDBY pauses
        # the periodic poll timers so a paused leader does not leave
        # human-agent avatars polling forever. Everything else stays
        # muted.
        if role == TeamRole.HUMAN_AGENT:
            if event.event_type not in (
                TeamEvent.CLEANED,
                TeamEvent.MEMBER_CANCELED,
                TeamEvent.STANDBY,
            ):
                return

        await self._framework.trigger(event.event_type, event)
