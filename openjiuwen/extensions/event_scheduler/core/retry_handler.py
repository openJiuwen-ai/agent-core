# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Retry Handler Module

This module implements automatic retry logic for failed tasks, including:
- RetryHandler: Evaluates failed tasks and re-submits them with backoff delays

The RetryHandler works with the TimerDispatcher to schedule retries after
a calculated backoff delay. It tracks retry counts in the Task.extensions
field and respects the configured retry policy limits.

Workflow:
- When a task fails, check if retry is enabled and retries remain
- Calculate the backoff delay based on the retry count
- Increment the retry count in task extensions
- Schedule the task for delayed re-execution via TimerDispatcher
"""

from __future__ import annotations

from typing import Optional, TYPE_CHECKING

from openjiuwen.core.common.logging import logger
from openjiuwen.core.controller.schema import TaskStatus
from openjiuwen.core.controller.schema.task import Task
from openjiuwen.extensions.event_scheduler.schema import (
    RetryPolicy,
    ScheduleType,
    ScheduledTaskMixin,
    EventSchedulerConfig,
)

if TYPE_CHECKING:
    from openjiuwen.core.controller.modules.task_manager import TaskManager
    from openjiuwen.extensions.event_scheduler.core.timer_dispatcher import TimerDispatcher


EXTENSION_KEY = "event_scheduler"


class RetryHandler:
    """Retry Handler for Failed Tasks

    Evaluates whether a failed task should be retried based on its retry
    policy and current retry count. When a retry is warranted, the handler
    schedules the task for delayed re-execution.

    Attributes:
        _config: Event scheduler configuration
        _task_manager: Reference to the core task manager
        _timer_dispatcher: Reference to the timer dispatcher for scheduling retries
    """

    def __init__(
            self,
            config: EventSchedulerConfig,
            task_manager: 'TaskManager',
            timer_dispatcher: 'TimerDispatcher'
    ):
        """Initialize retry handler

        Args:
            config: Event scheduler configuration
            task_manager: Core task manager instance
            timer_dispatcher: Timer dispatcher for scheduling delayed retries
        """
        self._config = config
        self._task_manager = task_manager
        self._timer_dispatcher = timer_dispatcher

    async def handle_task_failure(self, failed_task: Task) -> bool:
        """Handle a task failure by evaluating retry eligibility

        Checks the task's retry policy and count, then schedules a retry
        if eligible.

        Args:
            failed_task: The task that just failed

        Returns:
            bool: True if a retry was scheduled, False if retries exhausted
        """
        if not self._config.enable_retry:
            return False

        mixin = self._get_scheduler_mixin(failed_task)
        retry_policy = self._get_retry_policy(mixin)

        if not retry_policy:
            return False

        retry_count = mixin.retry_count if mixin else 0

        if retry_count >= retry_policy.max_retries:
            logger.info(
                f"Task {failed_task.task_id} exhausted all {retry_policy.max_retries} "
                f"retry attempts, not retrying"
            )
            return False

        # Calculate backoff delay
        delay = retry_policy.get_delay(retry_count)
        new_retry_count = retry_count + 1

        logger.info(
            f"Scheduling retry {new_retry_count}/{retry_policy.max_retries} "
            f"for task {failed_task.task_id} with {delay:.1f}s delay"
        )

        # Update task extensions with new retry count
        retry_mixin = ScheduledTaskMixin(
            schedule_type=ScheduleType.DELAYED,
            delay_seconds=delay,
            retry_policy=retry_policy,
            retry_count=new_retry_count,
            chain_source_task_id=mixin.chain_source_task_id if mixin else None,
        )

        extensions = dict(failed_task.extensions) if failed_task.extensions else {}
        extensions[EXTENSION_KEY] = retry_mixin.model_dump()
        failed_task.extensions = extensions

        # Transition task to WAITING and schedule via timer dispatcher
        await self._task_manager.update_task_status(
            failed_task.task_id, TaskStatus.WAITING
        )
        await self._timer_dispatcher.schedule_task(
            failed_task.task_id, retry_mixin
        )

        return True

    def _get_scheduler_mixin(self, task: Task) -> Optional[ScheduledTaskMixin]:
        """Extract scheduler mixin from task extensions

        Args:
            task: The task to extract the mixin from

        Returns:
            Optional[ScheduledTaskMixin]: The scheduler mixin, or None if not present
        """
        if not task.extensions or EXTENSION_KEY not in task.extensions:
            return None

        try:
            return ScheduledTaskMixin(**task.extensions[EXTENSION_KEY])
        except Exception as e:
            logger.error(
                f"Failed to parse scheduler mixin for task {task.task_id}: {e}",
                exc_info=True
            )
            return None

    def _get_retry_policy(self, mixin: Optional[ScheduledTaskMixin]) -> Optional[RetryPolicy]:
        """Get the retry policy for a task

        Returns the task-specific retry policy if set, otherwise falls back
        to the default retry policy from config.

        Args:
            mixin: The scheduler mixin for the task

        Returns:
            Optional[RetryPolicy]: The retry policy, or None if no policy applies
        """
        if mixin and mixin.retry_policy:
            return mixin.retry_policy
        return self._config.default_retry_policy
