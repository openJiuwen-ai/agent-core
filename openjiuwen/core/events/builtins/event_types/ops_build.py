# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Core-reserved ``ops.build`` event type and its CORE handler."""

from __future__ import annotations

from typing import TYPE_CHECKING

from openjiuwen.core.events.event_next import EventNext
from openjiuwen.core.events.ops import EventHandlerOps, EventHandlerOpsBuilder
from openjiuwen.core.events.types import OPS_BUILD_EVENT_TYPE, Event, EventInput, EventOutput, MatchPattern

if TYPE_CHECKING:
    from openjiuwen.core.events.bus import ExtensionEventBus


def match_pattern_func(event_input: EventInput, match_pattern: MatchPattern) -> bool:
    """Return True only for empty build input and an empty match pattern."""
    return event_input is None and not match_pattern


def input_validator(event_input: EventInput) -> bool:
    """Accept only the empty build input."""
    return event_input is None


def output_validator(event_output: EventOutput | None) -> bool:
    """Accept only an ``EventHandlerOpsBuilder`` as build output."""
    return isinstance(event_output, EventHandlerOpsBuilder)


async def core_handler(event: Event, ops: EventHandlerOps | None, event_next: EventNext) -> EventHandlerOpsBuilder:
    """Create the empty Builder that BUILTIN handlers extend during ``ops.build``."""
    del event, ops, event_next
    return EventHandlerOpsBuilder()


def register(bus: ExtensionEventBus) -> None:
    """Register the Core ``ops.build`` event type on ``bus``."""
    bus.register_event_type(
        OPS_BUILD_EVENT_TYPE,
        match_pattern_func,
        core_handler,
        input_validator=input_validator,
        output_validator=output_validator,
    )
