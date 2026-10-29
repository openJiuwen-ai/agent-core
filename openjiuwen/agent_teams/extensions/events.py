# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Team extension event types, CORE handlers, and the host registration entry.

The host registers the team events during ``Registry.init`` through the
``host_event_types`` callback::

    await registry.init(
        event_bus,
        host_event_types=register_team_event_types,
        host_handlers=...,
    )

Registration is idempotent per process only in the sense that each bus is
fresh; registering the same event type twice on one bus raises, which is
the framework's guard against double wiring.

The ``team.build_policy`` CORE reads the effective-config snapshot through
the call-scoped binding (``current_policy_context``) rather than through
``ops.team.policy_context()``: both read one identical snapshot, and the
direct read keeps the CORE usable even when the team Ops namespace has not
been registered on this bus.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

from openjiuwen.agent_teams.extensions.matching import (
    match_team_build_policy,
    match_team_monitor_event,
)
from openjiuwen.agent_teams.extensions.models import (
    TeamMonitorEventInput,
    TeamMonitorEventOutput,
    TeamPolicyBuildInput,
    TeamPolicyBuildOutput,
    TeamPolicyContext,
)
from openjiuwen.agent_teams.extensions.monitor_event_bridge import MonitorEventDispatcher
from openjiuwen.agent_teams.extensions.ops import (
    bind_policy_context_scope,
    bind_policy_loader_scope,
    bind_team_scope,
    current_policy_context,
    current_policy_loader,
)
from openjiuwen.agent_teams.extensions.policy import build_default_policy_sections
from openjiuwen.agent_teams.monitor.models import MonitorEvent
from openjiuwen.agent_teams.prompts.loader import TemplateLoader
from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import raise_error
from openjiuwen.core.common.logging import team_logger
from openjiuwen.core.events.event_next import EventNext
from openjiuwen.core.events.ops import EventHandlerOps
from openjiuwen.core.events.types import Event, EventType, SessionContext

if TYPE_CHECKING:
    from openjiuwen.core.events.protocol import ExtensionEventBusProtocol

TEAM_MONITOR_EVENT: Final[EventType] = "team.monitor_event"
"""Public monitor notifications delivered to the extension chain."""

TEAM_BUILD_POLICY: Final[EventType] = "team.build_policy"
"""Policy section assembly for one member build target."""


def _monitor_event_input_validator(event_input: object) -> bool:
    """Accept only a typed monitor-event input — an empty pattern cannot bypass this."""
    return isinstance(event_input, TeamMonitorEventInput)


def _monitor_event_output_validator(event_output: object | None) -> bool:
    """Accept the chain marker or a short-circuited None."""
    return event_output is None or isinstance(event_output, TeamMonitorEventOutput)


def _build_policy_input_validator(event_input: object) -> bool:
    """Accept only a typed policy build target."""
    return isinstance(event_input, TeamPolicyBuildInput)


def _build_policy_output_validator(event_output: object | None) -> bool:
    """Accept only a typed policy build output."""
    return isinstance(event_output, TeamPolicyBuildOutput)


async def team_monitor_event_core(
    _event: Event,
    _ops: EventHandlerOps | None,
    _event_next: EventNext,
) -> TeamMonitorEventOutput:
    """Finish the notification chain with no business side effect.

    The originating team state change has already happened and its result
    never participates in this chain; the CORE exists so the chain has a
    defined tail whether or not handlers matched.
    """
    return TeamMonitorEventOutput()


async def _team_build_policy_core(
    event: Event,
    _ops: EventHandlerOps | None,
    _event_next: EventNext,
) -> TeamPolicyBuildOutput:
    """Build the default sections from the build's bound context and loader.

    Both bindings come from the build entry: the effective-config snapshot
    and the caller's template loader (the team's workspace cache). A missing
    binding is an explicit error, never a silent fallback — a build that
    skipped the entry must fail loudly rather than render evolved templates
    from the static framework defaults.

    Raises:
        BaseError: ``AGENT_TEAM_STATE_INVALID`` when no policy context or no
            policy loader is bound — a build must always run inside the
            build entry's scope.
    """
    context = current_policy_context()
    if context is None:
        raise_error(
            StatusCode.AGENT_TEAM_STATE_INVALID,
            reason="team.build_policy requires a bound policy context",
        )
    build_loader = current_policy_loader()
    if build_loader is None:
        raise_error(
            StatusCode.AGENT_TEAM_STATE_INVALID,
            reason="team.build_policy requires a bound policy loader",
        )
    sections = build_default_policy_sections(event.event_input, context, loader=build_loader)
    return TeamPolicyBuildOutput(sections=sections)


def register_team_event_types(bus: "ExtensionEventBusProtocol") -> None:
    """Register the team event types and their CORE handlers.

    Args:
        bus: The extension event bus the host initializes.
    """
    bus.register_event_type(
        TEAM_MONITOR_EVENT,
        match_team_monitor_event,
        team_monitor_event_core,
        input_validator=_monitor_event_input_validator,
        output_validator=_monitor_event_output_validator,
    )
    bus.register_event_type(
        TEAM_BUILD_POLICY,
        match_team_build_policy,
        _team_build_policy_core,
        input_validator=_build_policy_input_validator,
        output_validator=_build_policy_output_validator,
    )


def build_monitor_event_dispatcher(bus: "ExtensionEventBusProtocol") -> MonitorEventDispatcher:
    """Build the bridge dispatcher that triggers ``team.monitor_event``.

    The bridge hands one public monitor event and its session id to the
    returned dispatcher; it wraps them into the typed input and runs the
    extension chain. Handler faults stay the framework's business (a
    failing handler is skipped there); anything that still escapes is
    logged and dropped — a notification failure must never propagate back
    into the bridge's consumer task.

    Args:
        bus: A host-initialized bus (``ops.build`` already run).

    Returns:
        The async dispatcher to hand to :class:`TeamMonitorEventBridge`.
    """

    async def dispatch(monitor_event: MonitorEvent, session_id: str) -> None:
        event_input = TeamMonitorEventInput(
            team_name=monitor_event.team_name,
            member_name=monitor_event.member_name,
            monitor_event=monitor_event,
        )
        try:
            # Bind the event's team for the call so handlers can omit
            # ``team_name`` in team Ops queries; the binding derives from the
            # event itself, never from anything a handler rewrote.
            with bind_team_scope(monitor_event.team_name):
                output = await bus.trigger(
                    Event(TEAM_MONITOR_EVENT, event_input),
                    SessionContext(session_id=session_id),
                )
        except Exception as exc:
            team_logger.error("team.monitor_event trigger failed: {}", exc)
            return
        if output is not None and not isinstance(output, TeamMonitorEventOutput):
            team_logger.error(
                "team.monitor_event returned an unexpected output type: {}",
                type(output).__name__,
            )

    return dispatch


async def build_team_policy_sections(
    build_input: TeamPolicyBuildInput,
    context: TeamPolicyContext,
    *,
    loader: TemplateLoader,
    bus: "ExtensionEventBusProtocol | None" = None,
    session_id: str | None = None,
) -> TeamPolicyBuildOutput:
    """Assemble one member's policy sections through the extension boundary.

    With ``bus`` set (host wiring) this is the build entry: it binds the
    build target's team, the effective-config snapshot and the caller's
    template loader for the call, triggers ``team.build_policy`` and
    validates the final output. Without a bus the default builder runs
    locally with the same loader — the no-extension fast path.

    Args:
        build_input: The build target.
        context: Read-only snapshot of the effective config.
        loader: The caller's template loader (the team's workspace cache);
            required, so a missing loader surfaces at the call site rather
            than silently degrading to static framework templates.
        bus: Optional host-initialized bus; None runs the default builder.
        session_id: Session the build runs under, handed to the trigger.

    Returns:
        The validated policy build output.

    Raises:
        BaseError: ``AGENT_TEAM_STATE_INVALID`` when the triggered chain
            returned an invalid output.
    """
    if bus is None:
        return TeamPolicyBuildOutput(
            sections=build_default_policy_sections(build_input, context, loader=loader),
        )
    session = SessionContext(session_id=session_id) if session_id else None
    # All three bindings derive from the build target and the caller: handlers
    # may omit ``team_name`` in team Ops queries and read the config snapshot
    # through either path, and the CORE builds from the caller's loader (the
    # team's workspace cache) rather than the registration-time fallback —
    # none of them follows anything a handler rewrote.
    with (
        bind_team_scope(build_input.team_name),
        bind_policy_context_scope(context),
        bind_policy_loader_scope(loader),
    ):
        output = await bus.trigger(Event(TEAM_BUILD_POLICY, build_input), session)
    if not isinstance(output, TeamPolicyBuildOutput):
        raise_error(
            StatusCode.AGENT_TEAM_STATE_INVALID,
            reason="team.build_policy returned an invalid output",
        )
    return output


__all__ = [
    "TEAM_BUILD_POLICY",
    "TEAM_MONITOR_EVENT",
    "build_monitor_event_dispatcher",
    "build_team_policy_sections",
    "register_team_event_types",
    "team_monitor_event_core",
]
