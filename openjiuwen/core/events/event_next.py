# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Chain advancement handle passed to every event handler."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from types import TracebackType

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import raise_error
from openjiuwen.core.events.types import Event, EventOutput, EventType, HandlerLevel


class EventNext:
    """Advances the current handler chain.

    Created by the event bus for one handler invocation and valid only while
    that handler runs. The bus supplies ``forward``, which continues the
    underlying chain; this class only validates the request and delegates.
    """

    __slots__ = ("_event_type", "_handler_level", "_forward", "_owner_task", "_active")

    def __init__(
        self,
        event_type: EventType,
        handler_level: HandlerLevel,
        forward: Callable[[Event, HandlerLevel | None], Awaitable[EventOutput | None]] | None,
    ) -> None:
        """For internal use by the event bus; ``forward`` is ``None`` at the CORE tail."""
        self._event_type = event_type
        self._handler_level = handler_level
        self._forward = forward
        # Bound to the creating task so the handle cannot be driven from
        # another task; ``_active`` is cleared when the handler scope exits.
        self._owner_task = asyncio.current_task()
        self._active = True

    def __enter__(self) -> EventNext:
        """Return this handle for use inside a ``with`` block."""
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Mark this handle inactive when the handler scope ends."""
        self._active = False

    async def __call__(self, event: Event) -> EventOutput | None:
        """Enters the next matched handler, or the core handler when none remain."""
        return await self._advance(event, None)

    async def to(self, event: Event, handler_level: HandlerLevel) -> EventOutput | None:
        """Skips the remaining handlers of the current layer and enters ``handler_level``."""
        return await self._advance(event, handler_level)

    def _validate(self, event: Event, target_level: HandlerLevel | None) -> None:
        if not self._active:
            raise_error(StatusCode.EVENTS_NEXT_INVALID, reason="EventNext is no longer active")
        if asyncio.current_task() is not self._owner_task:
            raise_error(StatusCode.EVENTS_NEXT_INVALID, reason="EventNext cannot be used from another task")
        if not isinstance(event, Event):
            raise_error(StatusCode.EVENTS_NEXT_INVALID, reason="EventNext requires an Event")
        if event.event_type != self._event_type:
            raise_error(StatusCode.EVENTS_NEXT_INVALID, reason="EventNext cannot change the event type")
        if target_level is not None:
            # Jumps are strictly inward: USER -> BUILTIN | CORE, BUILTIN -> CORE.
            # The isinstance check must stay: HandlerLevel is a StrEnum, so a plain
            # string such as "core" would otherwise pass the membership tests.
            allowed_target = (
                self._handler_level is HandlerLevel.USER and target_level in (HandlerLevel.BUILTIN, HandlerLevel.CORE)
            ) or (self._handler_level is HandlerLevel.BUILTIN and target_level is HandlerLevel.CORE)
            if not isinstance(target_level, HandlerLevel) or not allowed_target:
                raise_error(StatusCode.EVENTS_NEXT_INVALID, reason="invalid handler level jump")

    async def _advance(self, event: Event, target_level: HandlerLevel | None) -> EventOutput | None:
        self._validate(event, target_level)
        if self._forward is None:
            return None  # CORE tail: nothing inward of here
        return await self._forward(event, target_level)
