# coding: utf-8
"""Coordination loop driven by events.

The loop is a thin wake-up layer — NOT a decision engine.
Events are wake-up signals; the DeepAgent handles all
behavior via team tools. The loop manages:
- Its own lifecycle (start / stop)
- Wake-up callback when events arrive
- Periodic polling timer as fallback for idle agents
"""

from __future__ import annotations

import asyncio
from enum import Enum
from typing import (
    Any,
    Awaitable,
    Callable,
    Dict,
    Optional,
    Union,
)

from pydantic import BaseModel, Field

from openjiuwen.agent_teams.schema.team import TeamRole
from openjiuwen.agent_teams.tools.team_events import EventMessage
from openjiuwen.core.common.logging import team_logger


# ------------------------------------------------------
# Inner event types (local to the coordination layer)
# ------------------------------------------------------

class InnerEventType(str, Enum):
    """Event types generated inside the coordination layer."""

    USER_INPUT = "user_input"
    POLL_MAILBOX = "coordination_poll_mailbox"
    POLL_TASK = "coordination_poll_task"
    SHUTDOWN = "shutdown"


class InnerEventMessage(BaseModel):
    """Internal event message, isolated from cross-process EventMessage."""

    event_type: InnerEventType
    payload: Dict[str, Any] = Field(default_factory=dict)


CoordinationEvent = Union[InnerEventMessage, EventMessage]
"""Union type for all events handled by the coordination loop."""

WakeCallback = Callable[[CoordinationEvent], Awaitable[None]]
"""Called with the full event when the loop is woken up."""


class CoordinatorLoop:
    """Event-driven wake-up loop for team coordination.

    Two wake-up paths, same callback:
    1. Event-driven: transport/MessageBus events
       trigger immediate wake-up.
    2. Polling timer: periodic fallback for idle
       agents, catches missed events.

    All decision logic lives in the DeepAgent —
    this class only manages lifecycle and wake-up.
    """

    def __init__(
        self,
        *,
        role: TeamRole,
        wake_callback: Optional[WakeCallback] = None,
        mailbox_poll_interval: float = 3000.0,
        task_poll_interval: float = 3000.0,
    ) -> None:
        self._role = role
        self._wake_callback = wake_callback
        self._mailbox_poll_interval = mailbox_poll_interval
        self._task_poll_interval = task_poll_interval
        self._running = False
        self._polls_paused = False
        self._event_queue: asyncio.Queue[CoordinationEvent] = asyncio.Queue()
        self._loop_task: Optional[asyncio.Task[None]] = None
        self._mailbox_poll_task: Optional[asyncio.Task[None]] = None
        self._task_poll_task: Optional[asyncio.Task[None]] = None

    # ------------------------------------------------------
    # Properties
    # ------------------------------------------------------

    @property
    def role(self) -> TeamRole:
        """Return the role that owns this loop."""
        return self._role

    @property
    def is_running(self) -> bool:
        """Whether the background loop is active."""
        return self._running

    @property
    def polls_paused(self) -> bool:
        """Whether periodic polling is paused."""
        return self._polls_paused

    # ------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------

    async def start(self) -> None:
        """Start the event loop and polling timer."""
        if self._running:
            return
        team_logger.info("CoordinatorLoop[{}] starting", self._role.value)
        self._running = True
        self._loop_task = asyncio.create_task(self._run_loop())
        self._mailbox_poll_task = asyncio.create_task(
            self._poll_loop(InnerEventType.POLL_MAILBOX, self._mailbox_poll_interval),
        )
        self._task_poll_task = asyncio.create_task(
            self._poll_loop(InnerEventType.POLL_TASK, self._task_poll_interval),
        )

    async def stop(self) -> None:
        """Stop loops, cancel poll timer, unsubscribe."""
        if not self._running:
            return
        team_logger.info("CoordinatorLoop[{}] stopping", self._role.value)
        self._running = False

        # Cancel polling timers
        for task in (self._mailbox_poll_task, self._task_poll_task):
            if task is not None:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        self._mailbox_poll_task = None
        self._task_poll_task = None

        # Stop event loop
        if self._loop_task is not None:
            await self._event_queue.put(
                InnerEventMessage(event_type=InnerEventType.SHUTDOWN),
            )
            try:
                await asyncio.wait_for(
                    self._loop_task,
                    timeout=5.0,
                )
            except (
                    asyncio.TimeoutError,
                    asyncio.CancelledError,
            ):
                self._loop_task.cancel()
            self._loop_task = None

    async def pause_polls(self) -> None:
        """Stop periodic poll tasks but keep the main event loop running.

        The loop can still receive transport events; only the
        periodic mailbox/task polling is suspended.
        """
        if self._polls_paused:
            return
        team_logger.info("CoordinatorLoop[{}] pausing polls", self._role.value)
        for task in (self._mailbox_poll_task, self._task_poll_task):
            if task is not None:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        self._mailbox_poll_task = None
        self._task_poll_task = None
        self._polls_paused = True

    async def resume_polls(self) -> None:
        """Restart periodic poll tasks after a pause."""
        if not self._polls_paused or not self._running:
            return
        team_logger.info("CoordinatorLoop[{}] resuming polls", self._role.value)
        self._mailbox_poll_task = asyncio.create_task(
            self._poll_loop(InnerEventType.POLL_MAILBOX, self._mailbox_poll_interval),
        )
        self._task_poll_task = asyncio.create_task(
            self._poll_loop(InnerEventType.POLL_TASK, self._task_poll_interval),
        )
        self._polls_paused = False

    # ------------------------------------------------------
    # Event ingress
    # ------------------------------------------------------

    async def enqueue(
        self,
        event: CoordinationEvent,
    ) -> None:
        team_logger.debug("received message {}", event)
        """Push an event into the processing queue."""
        await self._event_queue.put(event)

    # ------------------------------------------------------
    # Internal
    # ------------------------------------------------------

    async def _run_loop(self) -> None:
        """Background task: drain queue, invoke callback."""
        while self._running:
            try:
                event = await asyncio.wait_for(
                    self._event_queue.get(),
                    timeout=1.0,
                )
            except asyncio.TimeoutError:
                continue

            if (
                isinstance(event, InnerEventMessage)
                and event.event_type == InnerEventType.SHUTDOWN
            ):
                self._event_queue.task_done()
                break

            try:
                if self._wake_callback:
                    await self._wake_callback(event)
            except Exception:
                event_type = getattr(event, "event_type", "unknown")
                team_logger.exception(
                    "CoordinatorLoop: error in wake_callback for %s",
                    event_type,
                )
            finally:
                self._event_queue.task_done()

    async def _poll_loop(
        self,
        event_type: InnerEventType,
        interval: float,
    ) -> None:
        """Periodic fallback: enqueue a poll event
        every ``interval`` seconds.

        Each poll concern (mailbox, task) runs its own
        instance with independent interval and event type.
        """
        while self._running:
            await asyncio.sleep(interval)
            if not self._running:
                break
            await self.enqueue(
                InnerEventMessage(event_type=event_type),
            )


__all__ = [
    "CoordinationEvent",
    "CoordinatorLoop",
    "InnerEventMessage",
    "InnerEventType",
    "WakeCallback",
]
