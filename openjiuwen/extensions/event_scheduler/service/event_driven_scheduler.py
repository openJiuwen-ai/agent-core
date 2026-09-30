# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Event-Driven Scheduler Service

This module implements the main entry point for the event scheduler extension:
- EventDrivenScheduler: Coordinates timer dispatch, event chaining, and retry handling

The EventDrivenScheduler integrates with the existing controller event system
by subscribing to task completion and failure events via the EventQueue. It
orchestrates the TimerDispatcher, EventChainHandler, and RetryHandler to provide:

1. Delayed task execution - tasks can be scheduled for future execution
2. Event-driven chaining - tasks automatically trigger downstream tasks
3. Automatic retry - failed tasks are retried with exponential backoff

Integration:
- Uses Task.extensions['event_scheduler'] for scheduling metadata
- Subscribes to EventQueue for TASK_COMPLETION and TASK_FAILED events
- Creates new tasks via TaskManager.add_task()
- Schedules delayed execution via TimerDispatcher
"""

from __future__ import annotations

import uuid
from typing import Optional, TYPE_CHECKING

from openjiuwen.core.common.logging import logger
from openjiuwen.core.controller.schema import TaskStatus, EventType
from openjiuwen.core.controller.schema.task import Task
from openjiuwen.core.controller.schema.event import TaskCompletionEvent, TaskFailedEvent
from openjiuwen.extensions.event_scheduler.schema import (
    ScheduleType,
    ScheduledTaskMixin,
    EventChainRule,
    EventSchedulerConfig,
)
from openjiuwen.extensions.event_scheduler.core.timer_dispatcher import TimerDispatcher
from openjiuwen.extensions.event_scheduler.core.event_chain import EventChainHandler
from openjiuwen.extensions.event_scheduler.core.retry_handler import RetryHandler

if TYPE_CHECKING:
    from openjiuwen.core.controller.modules.task_manager import TaskManager
    from openjiuwen.core.controller.modules.event_queue import EventQueue
    from openjiuwen.core.session.agent import Session


EXTENSION_KEY = "event_scheduler"


class EventDrivenScheduler:
    """Event-Driven Scheduler

    Main orchestrator for the event scheduler extension. Coordinates between
    the TimerDispatcher (delayed execution), EventChainHandler (task chaining),
    and RetryHandler (automatic retry) to provide comprehensive event-driven
    task scheduling capabilities.

    This service integrates with the existing openJiuwen controller by:
    - Subscribing to EventQueue events for task completion and failure
    - Using TaskManager to create and update tasks
    - Storing scheduling metadata in Task.extensions

    Attributes:
        _config: Event scheduler configuration
        _task_manager: Core task manager instance
        _event_queue: Core event queue for subscribing to events
        _timer_dispatcher: Manages delayed task execution
        _event_chain_handler: Handles automatic task chaining
        _retry_handler: Handles automatic retry of failed tasks
    """

    def __init__(
            self,
            config: EventSchedulerConfig,
            task_manager: 'TaskManager',
            event_queue: 'EventQueue'
    ):
        """Initialize event-driven scheduler

        Args:
            config: Event scheduler configuration
            task_manager: Core task manager instance
            event_queue: Core event queue for event subscriptions
        """
        self._config = config
        self._task_manager = task_manager
        self._event_queue = event_queue

        self._timer_dispatcher = TimerDispatcher(
            config=config,
            task_manager=task_manager
        )
        self._event_chain_handler = EventChainHandler(
            config=config,
            task_manager=task_manager
        )
        self._retry_handler = RetryHandler(
            config=config,
            task_manager=task_manager,
            timer_dispatcher=self._timer_dispatcher
        )

        logger.info("EventDrivenScheduler initialized")

    async def start(self):
        """Start the event-driven scheduler

        Starts the timer dispatcher and registers event subscriptions.
        """
        if self._config.enable_delayed_scheduling:
            await self._timer_dispatcher.start()
            logger.info("Delayed scheduling enabled")

        logger.info("EventDrivenScheduler started")

    async def stop(self):
        """Stop the event-driven scheduler

        Stops the timer dispatcher and cleans up resources.
        """
        await self._timer_dispatcher.stop()
        logger.info("EventDrivenScheduler stopped")

    async def submit_scheduled_task(
            self,
            session_id: str,
            task_type: str,
            mixin: ScheduledTaskMixin,
            description: Optional[str] = None,
            priority: int = 1,
            metadata: Optional[dict] = None,
    ) -> str:
        """Submit a new task with scheduling parameters

        Creates a task with the appropriate initial status based on its
        schedule type. IMMEDIATE tasks start as SUBMITTED, while DELAYED
        and CRON tasks start as WAITING.

        Args:
            session_id: Session ID for the task
            task_type: Task type identifier
            mixin: Scheduling metadata
            description: Optional task description
            priority: Task priority (default 1)
            metadata: Optional task metadata

        Returns:
            str: The ID of the newly created task
        """
        task_id = str(uuid.uuid4())

        # Determine initial status
        if mixin.schedule_type == ScheduleType.IMMEDIATE:
            initial_status = TaskStatus.SUBMITTED
        else:
            initial_status = TaskStatus.WAITING

        # Build extensions
        extensions = {EXTENSION_KEY: mixin.model_dump()}

        # Create task
        task = Task(
            session_id=session_id,
            task_id=task_id,
            task_type=task_type,
            description=description,
            priority=priority,
            status=initial_status,
            metadata=metadata,
            extensions=extensions,
        )

        await self._task_manager.add_task(task)

        # Schedule delayed execution if needed
        if mixin.schedule_type != ScheduleType.IMMEDIATE:
            await self._timer_dispatcher.schedule_task(task_id, mixin)

        logger.info(
            f"Submitted scheduled task {task_id} "
            f"(type={task_type}, schedule={mixin.schedule_type.value})"
        )
        return task_id

    async def on_task_completed(self, event: TaskCompletionEvent):
        """Handle task completion event

        Called when a task completes successfully. Evaluates chain rules
        to determine if downstream tasks should be created.

        Args:
            event: The task completion event
        """
        if not event.task:
            logger.warning("Received TASK_COMPLETION event without task reference")
            return

        created_ids = await self._event_chain_handler.handle_task_completion(event.task)
        if created_ids:
            logger.info(
                f"Task {event.task.task_id} completion triggered "
                f"{len(created_ids)} chained task(s): {created_ids}"
            )

    async def on_task_failed(self, event: TaskFailedEvent):
        """Handle task failure event

        Called when a task fails. Evaluates whether the task should be
        retried based on its retry policy.

        Args:
            event: The task failure event
        """
        if not event.task:
            logger.warning("Received TASK_FAILED event without task reference")
            return

        retried = await self._retry_handler.handle_task_failure(event.task)
        if retried:
            logger.info(f"Task {event.task.task_id} scheduled for retry")
        else:
            logger.info(f"Task {event.task.task_id} will not be retried")

    def add_chain_rule(self, rule: EventChainRule):
        """Add an event chain rule dynamically

        Args:
            rule: The chain rule to add
        """
        self._event_chain_handler.add_rule(rule)

    def remove_chain_rule(self, rule_id: str) -> bool:
        """Remove an event chain rule by ID

        Args:
            rule_id: The ID of the rule to remove

        Returns:
            bool: True if the rule was found and removed
        """
        return self._event_chain_handler.remove_rule(rule_id)

    @property
    def timer_dispatcher(self) -> TimerDispatcher:
        """Get the timer dispatcher instance"""
        return self._timer_dispatcher

    @property
    def event_chain_handler(self) -> EventChainHandler:
        """Get the event chain handler instance"""
        return self._event_chain_handler

    @property
    def retry_handler(self) -> RetryHandler:
        """Get the retry handler instance"""
        return self._retry_handler

    @property
    def config(self) -> EventSchedulerConfig:
        """Get the event scheduler configuration"""
        return self._config
