# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Protocols a consumer can implement before an EventBus exists."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Protocol, TypeAlias

from openjiuwen.core.events.event_next import EventNext
from openjiuwen.core.events.ops import EventHandlerOps
from openjiuwen.core.events.types import (
    Event,
    EventOutput,
    EventType,
    HandlerId,
    HandlerLevel,
    MatchPattern,
    MatchPatternFunc,
    PackageMetadata,
    SessionContext,
)

if TYPE_CHECKING:
    from openjiuwen.core.single_agent import Session


EventHandler: TypeAlias = Callable[
    [Event, EventHandlerOps | None, EventNext],
    Awaitable[EventOutput | None],
]


class ExtensionEventBusProtocol(Protocol):
    """Registration and trigger surface for an extension event bus."""

    def register_event_type(
        self,
        event_type: EventType,
        match_pattern_func: MatchPatternFunc,
        core_handler: EventHandler,
    ) -> None:
        """Register an event type and its CORE handler."""
        ...

    def unregister_event_type(self, event_type: EventType) -> None:
        """Remove an event type and its handlers."""
        ...

    def register_event_handler(
        self,
        event_type: EventType,
        handler: EventHandler,
        match_pattern: MatchPattern,
        handler_level: HandlerLevel = HandlerLevel.USER,
        *,
        package_metadata: PackageMetadata | None = None,
    ) -> HandlerId:
        """Register a USER or BUILTIN handler and return its handler id."""
        ...

    def unregister_event_handler(self, handler_id: HandlerId) -> None:
        """Remove a USER or BUILTIN handler."""
        ...

    async def trigger(
        self,
        event: Event,
        session: Session | SessionContext | None = None,
    ) -> EventOutput | None:
        """Dispatch one event and await the handler chain result."""
        ...
