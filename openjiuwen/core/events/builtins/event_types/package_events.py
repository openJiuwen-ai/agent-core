# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""``package.*`` management event types and the shared CORE forwarder.

The events package must not import extension while it loads. The CORE handler
imports the orchestration only when a caller triggers the event.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from openjiuwen.core.events.event_next import EventNext
from openjiuwen.core.events.ops import EventHandlerOps
from openjiuwen.core.events.types import Event, EventInput, EventOutput, EventType, MatchPattern

if TYPE_CHECKING:
    from openjiuwen.core.events.bus import ExtensionEventBus
    from openjiuwen.core.events.protocol import EventHandler

PACKAGE_EVENT_TYPES: tuple[EventType, ...] = (
    "package.parse",
    "package.resolve",
    "package.query",
    "package.install",
    "package.uninstall",
    "package.reload",
)


def match_pattern_func(event_input: EventInput, match_pattern: MatchPattern) -> bool:
    """Accept the management request object. These events do not use a pattern."""
    del event_input
    return not match_pattern


def make_core_handler(event_type: EventType) -> EventHandler:
    """Forward one management event to extension orchestration."""

    async def core_handler(
        event: Event,
        ops: EventHandlerOps | None,
        event_next: EventNext,
    ) -> EventOutput | None:
        del ops, event_next
        from openjiuwen.core.extension.management import dispatch_management_event

        return await dispatch_management_event(event_type, event.event_input)

    core_handler.__name__ = f"core_handler_{event_type.replace('.', '_')}"
    return core_handler


def register_management_event(bus: ExtensionEventBus, event_type: EventType) -> None:
    """Register one management event type and its CORE handler."""
    bus.register_event_type(event_type, match_pattern_func, make_core_handler(event_type))


def register(bus: ExtensionEventBus) -> None:
    """Register every ``package.*`` management event."""
    for event_type in PACKAGE_EVENT_TYPES:
        register_management_event(bus, event_type)
