# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Bounded single-consumer observation stream used by the DSH adapter."""

from __future__ import annotations

import asyncio
from collections import deque

from openjiuwen.agent_teams.external.protocol import (
    TERMINAL_TURN_EVENT_KINDS,
    ExternalHarnessProtocolError,
    ExternalHarnessStateError,
    HarnessEvent,
    TurnEventKind,
    TurnLifecycleEvent,
)


class EventBufferClosed(RuntimeError):
    """Internal signal used to wake a blocked producer during shutdown."""


class BoundedEventBuffer:
    """A closable FIFO whose required events use blocking backpressure."""

    def __init__(self, capacity: int) -> None:
        self._capacity = capacity
        self._items: deque[HarnessEvent] = deque()
        self._condition = asyncio.Condition()
        self._closed = False
        self._consumer_active = False

    async def put(self, event: HarnessEvent) -> None:
        """Append one event, blocking while the bounded buffer is full."""

        async with self._condition:
            while len(self._items) >= self._capacity and not self._closed:
                await self._condition.wait()
            if self._closed:
                raise EventBufferClosed("DSH event buffer is closed")
            self._items.append(event)
            self._condition.notify_all()

    async def get(self) -> HarnessEvent | None:
        """Return the next event, or ``None`` after close and drain."""

        async with self._condition:
            while not self._items and not self._closed:
                await self._condition.wait()
            if not self._items:
                return None
            event = self._items.popleft()
            self._condition.notify_all()
            return event

    async def peek(self) -> HarnessEvent | None:
        """Inspect the next event without freeing producer capacity."""

        async with self._condition:
            while not self._items and not self._closed:
                await self._condition.wait()
            return self._items[0] if self._items else None

    async def close(self) -> None:
        """Close the producer side and wake all blocked readers and writers."""

        async with self._condition:
            self._closed = True
            self._condition.notify_all()

    def cursor(self, *, turn_id: str | None = None, per_turn: bool = False) -> "DshEventCursor":
        """Acquire the single consumer lease and return a cursor."""

        if self._consumer_active:
            raise ExternalHarnessStateError("the DSH observation stream already has an active consumer")
        self._consumer_active = True
        return DshEventCursor(self, expected_turn_id=turn_id, per_turn=per_turn)

    def release_consumer(self) -> None:
        """Release the consumer lease.  The operation is idempotent."""

        self._consumer_active = False


class DshEventCursor:
    """Cycle-long or finite-turn view over one ``BoundedEventBuffer``."""

    def __init__(
        self,
        buffer: BoundedEventBuffer,
        *,
        expected_turn_id: str | None,
        per_turn: bool,
    ) -> None:
        self._buffer = buffer
        self._expected_turn_id = expected_turn_id
        self._per_turn = per_turn
        self._selected_turn_id: str | None = None
        self._closed = False

    def __aiter__(self) -> "DshEventCursor":
        return self

    async def __anext__(self) -> HarnessEvent:
        if self._closed:
            raise StopAsyncIteration

        while True:
            if self._per_turn and self._selected_turn_id is None:
                # Validate a requested Turn against the queue head before
                # removing it.  A pop-then-push-back sequence can race a
                # producer that immediately fills the released slot and lose
                # the STARTED event.
                event = await self._buffer.peek()
            else:
                event = await self._buffer.get()
            if event is None:
                selected_turn_id = self._selected_turn_id
                await self.aclose()
                if self._per_turn and selected_turn_id is not None:
                    raise ExternalHarnessProtocolError(
                        f"DSH event stream closed before turn {selected_turn_id!r} terminated"
                    )
                raise StopAsyncIteration

            if not self._per_turn:
                return event

            payload = event.event
            if self._selected_turn_id is None:
                is_started = isinstance(payload, TurnLifecycleEvent) and payload.kind is TurnEventKind.STARTED
                if not is_started:
                    await self._buffer.get()
                    continue
                if self._expected_turn_id is not None and event.turn_id != self._expected_turn_id:
                    await self.aclose()
                    raise ExternalHarnessStateError(
                        "requested DSH turn is not the next unconsumed turn: "
                        f"expected {self._expected_turn_id!r}, found {event.turn_id!r}"
                    )
                consumed = await self._buffer.get()
                if consumed is None:
                    await self.aclose()
                    raise ExternalHarnessProtocolError("DSH event stream changed while selecting a turn")
                event = consumed
                payload = event.event
                self._selected_turn_id = event.turn_id

            selected_turn_id = self._selected_turn_id
            is_terminal = (
                event.turn_id == selected_turn_id
                and isinstance(payload, TurnLifecycleEvent)
                and payload.kind in TERMINAL_TURN_EVENT_KINDS
            )
            if is_terminal:
                await self.aclose()
            return event

    async def aclose(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._buffer.release_consumer()


__all__ = ["BoundedEventBuffer", "DshEventCursor", "EventBufferClosed"]
