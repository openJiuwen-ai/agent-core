# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

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
import contextlib
from enum import Enum
from typing import (
    Any,
    Awaitable,
    Callable,
    Dict,
    Optional,
    Union,
)

from pydantic import (
    BaseModel,
    Field,
)

from openjiuwen.agent_teams.schema.events import EventMessage
from openjiuwen.agent_teams.schema.team import TeamRole
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


class EventBus:
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
        mailbox_poll_interval: float = 30.0,
        task_poll_interval: float = 30.0,
    ) -> None:
        self._role = role
        self._mailbox_poll_interval = mailbox_poll_interval
        self._task_poll_interval = task_poll_interval
        # ``wake_callback`` is bound at ``start()``; constructing the bus
        # before the dispatcher exists is intentional, so the kernel can
        # hand the bus to the dispatcher as a poll controller and only
        # wire ``dispatcher.dispatch`` back in at start time.
        self._wake_callback: Optional[WakeCallback] = None
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

    async def start(self, *, wake_callback: Optional[WakeCallback] = None) -> None:
        """Start the event loop and polling timer.

        The wake callback is bound here rather than at construction so
        the coordination kernel can break the bus ↔ dispatcher
        circular dependency: build bus → build dispatcher (with bus as
        poll controller) → start bus with dispatcher.dispatch.
        Passing ``None`` keeps any previously bound callback (or
        ``None`` for tests that exercise the bus without a dispatcher).
        """
        if self._running:
            return
        if wake_callback is not None:
            self._wake_callback = wake_callback
        team_logger.info("EventBus[{}] starting", self._role.value)
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
        team_logger.info("EventBus[{}] stopping", self._role.value)
        self._running = False
        # Reset pause flag before touching the poll tasks so partial failures
        # below still leave the state machine consistent: a subsequent start()
        # must not inherit a stale ``_polls_paused = True`` from a prior
        # pause_polls().
        self._polls_paused = False

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
                with contextlib.suppress(asyncio.CancelledError):
                    await self._loop_task
            self._loop_task = None

    async def pause_polls(self) -> None:
        """Stop periodic poll tasks but keep the main event loop running.

        The loop can still receive transport events; only the
        periodic mailbox/task polling is suspended.
        """
        if self._polls_paused:
            return
        team_logger.info("EventBus[{}] pausing polls", self._role.value)
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
        team_logger.info("EventBus[{}] resuming polls", self._role.value)
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
        # team_logger.debug("received message {}", event)
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

            if isinstance(event, InnerEventMessage) and event.event_type == InnerEventType.SHUTDOWN:
                self._event_queue.task_done()
                break

            try:
                if self._wake_callback:
                    await self._wake_callback(event)
            except Exception:
                event_type = getattr(event, "event_type", "unknown")
                team_logger.exception(
                    "EventBus: error in wake_callback for %s",
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
    "EventBus",
    "InnerEventMessage",
    "InnerEventType",
    "WakeCallback",
]
