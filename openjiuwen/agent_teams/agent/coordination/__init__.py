# coding: utf-8
"""TeamAgent coordination subsystem.

Public surface:
    - CoordinationKernel: lifecycle + transport wiring (formerly CoordinationManager)
    - EventBus: queue + polling timer (formerly CoordinatorLoop)
    - EventDispatcher: event-to-behavior mapping (unchanged)
    - DispatcherHost: protocol callbacks the dispatcher needs from the host
    - InnerEventMessage / InnerEventType / CoordinationEvent / WakeCallback
"""

from __future__ import annotations

from openjiuwen.agent_teams.agent.coordination.dispatcher import (
    DispatcherHost,
    EventDispatcher,
)
from openjiuwen.agent_teams.agent.coordination.event_bus import (
    CoordinationEvent,
    EventBus,
    InnerEventMessage,
    InnerEventType,
    WakeCallback,
)
from openjiuwen.agent_teams.agent.coordination.kernel import CoordinationKernel

__all__ = [
    "CoordinationEvent",
    "CoordinationKernel",
    "DispatcherHost",
    "EventBus",
    "EventDispatcher",
    "InnerEventMessage",
    "InnerEventType",
    "WakeCallback",
]
