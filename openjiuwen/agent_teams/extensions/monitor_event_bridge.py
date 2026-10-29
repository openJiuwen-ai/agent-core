# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Bridge internal team events into a best-effort monitor-event queue.

Team state changes surface as internal ``EventMessage`` traffic on the
coordination bus. The bridge converts each message into the public
``MonitorEvent`` model (reusing ``MonitorEvent.from_event_message``, which
silently drops internal event types) and enqueues it — non-blocking, so
the coordination listener callback never waits on downstream consumers.

A dedicated consumer task drains the queue in arrival order and hands each
monitor event to the injected ``dispatcher`` — the entry that triggers the
``team.monitor_event`` extension chain. Without a dispatcher, queued
monitor events are simply discarded (the queue carries best-effort notices
only — it is not a reliable audit store, and a full queue drops new events
with a counter rather than blocking coordination).

Lifecycle: ``start()`` creates the consumer task; ``stop()`` stops
accepting, cancels and awaits the consumer, and counts every unprocessed
monitor event as dropped — nothing is replayed across sessions or restarts.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Awaitable, Callable

from openjiuwen.agent_teams.monitor.models import MonitorEvent
from openjiuwen.core.common.logging import team_logger

if TYPE_CHECKING:
    from openjiuwen.agent_teams.schema.events import EventMessage

# Default bridge queue capacity: enough slack for bursts of task/member
# churn without unbounded memory growth on a stuck consumer.
DEFAULT_MONITOR_EVENT_QUEUE_CAPACITY = 1024

# Type of the injected monitor event dispatcher: receives the public event
# and the session id it belongs to. The extension framework's trigger entry
# implements this; a slow dispatcher only delays this bridge's queue.
MonitorEventDispatcher = Callable[[MonitorEvent, str], Awaitable[None]]


class TeamMonitorEventBridge:
    """Bounded queue + consumer task bridging internal events to monitor events.

    One bridge serves one team runtime instance in one session; independent
    instances keep different teams or sessions from blocking each other.
    The listener method (:meth:`on_internal_event`) matches the
    ``TeamAgent.add_event_listener`` signature and never awaits anything —
    enqueueing is ``put_nowait`` and a full queue drops with a counter.
    """

    def __init__(
        self,
        *,
        team_name: str,
        session_id: str,
        capacity: int = DEFAULT_MONITOR_EVENT_QUEUE_CAPACITY,
        dispatcher: MonitorEventDispatcher | None = None,
    ) -> None:
        """Initialize the bridge without starting the consumer.

        Only the queue and counters are set up here; call :meth:`start` to
        create the consumer task that drains them.

        Args:
            team_name: Team whose events this bridge collects.
            session_id: Session the runtime instance runs under; handed to
                the dispatcher with every monitor event.
            capacity: Queue capacity; excess monitor events are dropped and
                counted, never blocking the coordination callback.
            dispatcher: Optional async consumer of dequeued monitor events.
                ``None`` (the default before host wiring lands) discards
                dequeued events after logging.
        """
        self._team_name = team_name
        self._session_id = session_id
        self._dispatcher = dispatcher
        self._queue: asyncio.Queue[MonitorEvent | None] = asyncio.Queue(maxsize=capacity)
        self._consumer: asyncio.Task[None] | None = None
        self._dropped = 0
        self._closing = False

    @property
    def team_name(self) -> str:
        """Team whose events this bridge collects."""
        return self._team_name

    @property
    def session_id(self) -> str:
        """Session the runtime instance runs under."""
        return self._session_id

    @property
    def dropped_count(self) -> int:
        """Monitor events dropped so far (queue-full plus stop-time backlog).

        A gauge for noticing monitor-event backlog, not a business signal: drops
        never mark the originating team operation failed.
        """
        return self._dropped

    @property
    def is_running(self) -> bool:
        """Whether the consumer task is alive (False before start / after stop)."""
        return self._consumer is not None and not self._consumer.done()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Create the consumer task (idempotent; no-op after :meth:`stop`).

        The consumer processes monitor events strictly in arrival order; a
        slow dispatcher delays later monitor events in this queue only.
        """
        if self._closing or self.is_running:
            return
        self._consumer = asyncio.create_task(self._consume())
        team_logger.info("TeamMonitorEventBridge started for team {}", self._team_name)

    async def stop(self) -> None:
        """Stop accepting, cancel and await the consumer, tally the backlog.

        After this returns no further monitor events are accepted (they drop
        with a counter), the consumer task has exited, and every
        unprocessed monitor event counts as dropped — none are replayed on
        the next session.
        """
        self._closing = True
        consumer = self._consumer
        self._consumer = None
        if consumer is not None:
            consumer.cancel()
            try:
                await consumer
            except asyncio.CancelledError:
                # Swallow only the cancellation requested for the consumer
                # above. When the cancel hit stop() itself instead (this
                # task carries a pending cancel request), the error is not
                # ours to absorb — propagate it so the caller's
                # cancellation is not lost.
                if asyncio.current_task().cancelling():
                    raise
        backlog = 0
        while True:
            try:
                self._queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            backlog += 1
        self._dropped += backlog
        team_logger.info(
            "TeamMonitorEventBridge stopped for team {}, dropped {} backlog monitor event(s)",
            self._team_name,
            backlog,
        )

    # ------------------------------------------------------------------
    # Event listener
    # ------------------------------------------------------------------

    async def on_internal_event(self, event: "EventMessage") -> None:
        """Internal-event listener registered via ``add_event_listener``.

        Converts to the public ``MonitorEvent`` (internal event types drop
        silently inside the conversion) and enqueues without ever awaiting
        capacity, so the coordination delivery loop cannot block on this
        bridge. Events arriving after :meth:`stop` are dropped and counted.
        """
        if self._closing:
            return
        monitor_event = MonitorEvent.from_event_message(event)
        if monitor_event is None:
            return
        try:
            self._queue.put_nowait(monitor_event)
        except asyncio.QueueFull:
            self._dropped += 1
            team_logger.warning(
                "TeamMonitorEventBridge queue full for team {} (dropped so far: {})",
                self._team_name,
                self._dropped,
            )

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def _consume(self) -> None:
        """Drain the queue in order, one monitor event at a time.

        A failing dispatcher is logged and the loop moves on to the next
        monitor event — one bad consumer call never stalls the queue.
        Cancellation (``stop``) propagates: the in-flight dispatcher call
        and the remaining backlog are abandoned, the latter counted by
        ``stop``.
        """
        while True:
            event = await self._queue.get()
            if self._dispatcher is None:
                team_logger.debug(
                    "TeamMonitorEventBridge has no dispatcher; dropping monitor event {} for team {}",
                    event.event_type,
                    self._team_name,
                )
                continue
            try:
                await self._dispatcher(event, self._session_id)
            except Exception as exc:
                # Cancellation is not caught here: CancelledError derives
                # from BaseException, so a stop-time cancel of this task
                # propagates straight through instead of being logged as a
                # dispatcher failure.
                team_logger.error(
                    "TeamMonitorEventBridge dispatcher error for team {}: {}",
                    self._team_name,
                    exc,
                )


__all__ = [
    "DEFAULT_MONITOR_EVENT_QUEUE_CAPACITY",
    "MonitorEventDispatcher",
    "TeamMonitorEventBridge",
]
