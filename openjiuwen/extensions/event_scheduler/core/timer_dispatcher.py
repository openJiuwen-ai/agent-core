# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Timer Dispatcher Module

This module implements time-based task dispatching, including:
- TimerDispatcher: Manages delayed and scheduled task execution

The TimerDispatcher works alongside the existing TaskScheduler by holding
tasks in a WAITING state until their scheduled execution time arrives,
then transitioning them to SUBMITTED so the TaskScheduler picks them up.

Workflow:
- Tasks with schedule_type=DELAYED are held until delay_seconds elapses
- Tasks with schedule_type=CRON are re-scheduled after each execution
- The dispatcher runs a background polling loop to check for ready tasks
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone, timedelta
from typing import Dict, Optional, TYPE_CHECKING

from openjiuwen.core.common.logging import logger
from openjiuwen.core.controller.schema import TaskStatus
from openjiuwen.extensions.event_scheduler.schema import (
    ScheduleType,
    ScheduledTaskMixin,
    EventSchedulerConfig,
)

if TYPE_CHECKING:
    from openjiuwen.core.controller.modules.task_manager import TaskManager, TaskFilter


EXTENSION_KEY = "event_scheduler"


class TimerDispatcher:
    """Timer Dispatcher for Delayed and Scheduled Task Execution

    Manages the lifecycle of time-based task scheduling. Tasks are initially
    created with WAITING status and held until their scheduled execution time.
    Once ready, they are transitioned to SUBMITTED status for the existing
    TaskScheduler to pick up and execute.

    Attributes:
        _config: Event scheduler configuration
        _task_manager: Reference to the core task manager
        _pending_timers: Map of task_id to scheduled execution timestamp
        _running: Whether the dispatcher is actively polling
        _poll_task: Background asyncio task for the polling loop
        _lock: Lock for synchronized access to pending timers
    """

    def __init__(
            self,
            config: EventSchedulerConfig,
            task_manager: 'TaskManager'
    ):
        """Initialize timer dispatcher

        Args:
            config: Event scheduler configuration
            task_manager: Core task manager instance
        """
        self._config = config
        self._task_manager = task_manager
        self._pending_timers: Dict[str, str] = {}
        self._running = False
        self._poll_task: Optional[asyncio.Task] = None
        self._lock = asyncio.Lock()

    async def schedule_task(self, task_id: str, mixin: ScheduledTaskMixin) -> bool:
        """Schedule a task for delayed execution

        Registers a task with the timer dispatcher. The task should already
        exist in the TaskManager with WAITING status.

        Args:
            task_id: ID of the task to schedule
            mixin: Scheduling metadata for the task

        Returns:
            bool: True if the task was successfully scheduled
        """
        if mixin.schedule_type == ScheduleType.IMMEDIATE:
            return False

        async with self._lock:
            if len(self._pending_timers) >= self._config.max_scheduled_tasks:
                logger.warning(
                    f"Maximum scheduled tasks limit reached ({self._config.max_scheduled_tasks}), "
                    f"cannot schedule task {task_id}"
                )
                return False

        execute_after = self._calculate_execute_time(mixin)
        if not execute_after:
            logger.error(f"Could not calculate execution time for task {task_id}")
            return False

        async with self._lock:
            self._pending_timers[task_id] = execute_after

        logger.info(f"Task {task_id} scheduled for execution after {execute_after}")
        return True

    async def cancel_scheduled_task(self, task_id: str) -> bool:
        """Cancel a scheduled task

        Removes the task from the pending timers. Does not modify the
        task status in TaskManager.

        Args:
            task_id: ID of the task to cancel

        Returns:
            bool: True if the task was found and cancelled
        """
        async with self._lock:
            if task_id in self._pending_timers:
                del self._pending_timers[task_id]
                logger.info(f"Cancelled scheduled timer for task {task_id}")
                return True
        return False

    async def start(self):
        """Start the timer dispatcher polling loop"""
        if self._running:
            logger.warning("TimerDispatcher is already running")
            return

        self._running = True
        self._poll_task = asyncio.create_task(self._poll_loop())
        logger.info("TimerDispatcher started")

    async def stop(self):
        """Stop the timer dispatcher polling loop"""
        if not self._running:
            logger.warning("TimerDispatcher is not running")
            return

        self._running = False

        if self._poll_task:
            self._poll_task.cancel()
            try:
                await self._poll_task
            except asyncio.CancelledError:
                pass

        logger.info("TimerDispatcher stopped")

    async def _poll_loop(self):
        """Background polling loop for checking scheduled tasks

        Periodically checks all pending timers and transitions tasks
        whose execution time has arrived from WAITING to SUBMITTED.
        """
        logger.info("TimerDispatcher poll loop started")
        while self._running:
            try:
                await self._check_pending_tasks()
                await asyncio.sleep(self._config.scheduler_poll_interval)

            except asyncio.CancelledError:
                logger.info("TimerDispatcher poll loop cancelled")
                break

            except Exception as e:
                logger.error(f"Error in timer dispatcher poll loop: {e}", exc_info=True)
                await asyncio.sleep(1)

        logger.info("TimerDispatcher poll loop ended")

    async def _check_pending_tasks(self):
        """Check all pending tasks and submit those that are ready

        Iterates through pending timers and transitions tasks whose
        scheduled time has passed to SUBMITTED status.
        """
        now = datetime.now(timezone.utc).isoformat()
        ready_task_ids = []

        async with self._lock:
            for task_id, execute_after in list(self._pending_timers.items()):
                if now >= execute_after:
                    ready_task_ids.append(task_id)

        for task_id in ready_task_ids:
            try:
                await self._task_manager.update_task_status(task_id, TaskStatus.SUBMITTED)
                async with self._lock:
                    if task_id in self._pending_timers:
                        del self._pending_timers[task_id]
                logger.info(f"Delayed task {task_id} transitioned to SUBMITTED")

            except Exception as e:
                logger.error(
                    f"Failed to submit delayed task {task_id}: {e}",
                    exc_info=True
                )

    @staticmethod
    def _calculate_execute_time(mixin: ScheduledTaskMixin) -> Optional[str]:
        """Calculate the execution timestamp for a scheduled task

        Args:
            mixin: Scheduling metadata containing delay or cron info

        Returns:
            Optional[str]: ISO format timestamp for execution, or None if invalid
        """
        now = datetime.now(timezone.utc)

        if mixin.schedule_type == ScheduleType.DELAYED and mixin.delay_seconds is not None:
            execute_time = now + timedelta(seconds=mixin.delay_seconds)
            return execute_time.isoformat()

        if mixin.execute_after:
            return mixin.execute_after

        return None

    @property
    def pending_count(self) -> int:
        """Get the number of pending scheduled tasks"""
        return len(self._pending_timers)
