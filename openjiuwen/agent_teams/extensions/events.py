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
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

from openjiuwen.agent_teams.extensions.matching import match_team_monitor_event
from openjiuwen.agent_teams.extensions.models import (
    TeamMonitorEventInput,
    TeamMonitorEventOutput,
)
from openjiuwen.agent_teams.extensions.monitor_event_bridge import MonitorEventDispatcher
from openjiuwen.agent_teams.extensions.ops import bind_team_scope
from openjiuwen.agent_teams.monitor.models import MonitorEvent
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


def _monitor_event_input_validator(event_input: object) -> bool:
    """Accept only a typed monitor-event input — an empty pattern cannot bypass this."""
    return isinstance(event_input, TeamMonitorEventInput)


def _monitor_event_output_validator(event_output: object | None) -> bool:
    """Accept the chain marker or a short-circuited None."""
    return event_output is None or isinstance(event_output, TeamMonitorEventOutput)


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


__all__ = [
    "TEAM_MONITOR_EVENT",
    "build_monitor_event_dispatcher",
    "register_team_event_types",
    "team_monitor_event_core",
]
