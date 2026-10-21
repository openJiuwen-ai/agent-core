# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Chain advancement handle passed to every event handler."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from types import TracebackType

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import raise_error
from openjiuwen.core.events.types import Event, EventOutput, EventType, HandlerId, HandlerLevel


class EventNext:
    """Advances the current handler chain.

    Created by the event bus for one handler invocation and valid only while
    that handler runs; do not construct it directly.
    """

    __slots__ = ("_event_type", "_handler_level", "_call_next", "_matched_handler_ids", "_owner_task", "_active")

    def __init__(
        self,
        event_type: EventType,
        handler_level: HandlerLevel,
        call_next: Callable[..., Awaitable[EventOutput | None]] | None,
        matched_handler_ids: frozenset[HandlerId],
    ) -> None:
        self._event_type = event_type
        self._handler_level = handler_level
        self._call_next = call_next
        self._matched_handler_ids = matched_handler_ids
        self._owner_task = asyncio.current_task()
        self._active = True

    def __enter__(self) -> EventNext:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
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
            allowed_target = (
                self._handler_level is HandlerLevel.USER and target_level in (HandlerLevel.BUILTIN, HandlerLevel.CORE)
            ) or (self._handler_level is HandlerLevel.BUILTIN and target_level is HandlerLevel.CORE)
            if not isinstance(target_level, HandlerLevel) or not allowed_target:
                raise_error(StatusCode.EVENTS_NEXT_INVALID, reason="invalid handler level jump")

    async def _advance(self, event: Event, target_level: HandlerLevel | None) -> EventOutput | None:
        self._validate(event, target_level)
        if self._call_next is None:
            return None
        return await self._call_next(
            event,
            matched_handler_ids=self._matched_handler_ids,
            target_level=target_level,
        )
