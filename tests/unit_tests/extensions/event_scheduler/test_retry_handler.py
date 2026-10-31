# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Tests for RetryHandler."""

from __future__ import annotations

import uuid
import pytest

from openjiuwen.core.controller.schema.task import Task, TaskStatus
from openjiuwen.extensions.event_scheduler.schema import (
    RetryPolicy,
    ScheduleType,
    ScheduledTaskMixin,
    EventSchedulerConfig,
)


@pytest.mark.asyncio
class TestRetryHandler:
    """Tests for RetryHandler retry logic."""

    async def test_handle_failure_schedules_retry(
            self, retry_handler, mock_task_manager, sample_failed_task
    ):
        """Test that a failed task with remaining retries gets scheduled."""
        result = await retry_handler.handle_task_failure(sample_failed_task)

        assert result is True
        mock_task_manager.update_task_status.assert_called_once_with(
            sample_failed_task.task_id, TaskStatus.WAITING
        )

    async def test_handle_failure_increments_retry_count(
            self, retry_handler, mock_task_manager, sample_failed_task
    ):
        """Test that retry count is incremented in task extensions."""
        await retry_handler.handle_task_failure(sample_failed_task)

        extensions = sample_failed_task.extensions["event_scheduler"]
        mixin = ScheduledTaskMixin(**extensions)
        assert mixin.retry_count == 1

    async def test_handle_failure_exhausted_retries(
            self, retry_handler, mock_task_manager
    ):
        """Test that a task with exhausted retries is not retried."""
        task = Task(
            session_id="session-1",
            task_id=str(uuid.uuid4()),
            task_type="data_extraction",
            status=TaskStatus.FAILED,
            error_message="Connection timeout",
            extensions={
                "event_scheduler": ScheduledTaskMixin(
                    retry_policy=RetryPolicy(max_retries=3),
                    retry_count=3,
                ).model_dump()
            },
        )

        result = await retry_handler.handle_task_failure(task)

        assert result is False
        mock_task_manager.update_task_status.assert_not_called()

    async def test_handle_failure_retry_disabled(self, mock_task_manager):
        """Test that retry does nothing when disabled in config."""
        from openjiuwen.extensions.event_scheduler.core.retry_handler import RetryHandler
        from openjiuwen.extensions.event_scheduler.core.timer_dispatcher import TimerDispatcher

        config = EventSchedulerConfig(enable_retry=False)
        dispatcher = TimerDispatcher(config=config, task_manager=mock_task_manager)
        handler = RetryHandler(
            config=config,
            task_manager=mock_task_manager,
            timer_dispatcher=dispatcher,
        )

        task = Task(
            session_id="session-1",
            task_id=str(uuid.uuid4()),
            task_type="data_extraction",
            status=TaskStatus.FAILED,
            error_message="Connection timeout",
            extensions={
                "event_scheduler": ScheduledTaskMixin(
                    retry_policy=RetryPolicy(max_retries=3),
                    retry_count=0,
                ).model_dump()
            },
        )

        result = await handler.handle_task_failure(task)
        assert result is False

    async def test_handle_failure_no_retry_policy(
            self, mock_task_manager
    ):
        """Test that a task without retry policy is not retried."""
        from openjiuwen.extensions.event_scheduler.core.retry_handler import RetryHandler
        from openjiuwen.extensions.event_scheduler.core.timer_dispatcher import TimerDispatcher

        config = EventSchedulerConfig(enable_retry=True, default_retry_policy=None)
        dispatcher = TimerDispatcher(config=config, task_manager=mock_task_manager)
        handler = RetryHandler(
            config=config,
            task_manager=mock_task_manager,
            timer_dispatcher=dispatcher,
        )

        task = Task(
            session_id="session-1",
            task_id=str(uuid.uuid4()),
            task_type="data_extraction",
            status=TaskStatus.FAILED,
            error_message="Connection timeout",
        )

        result = await handler.handle_task_failure(task)
        assert result is False

    async def test_handle_failure_uses_default_policy(
            self, mock_task_manager
    ):
        """Test that default retry policy from config is used as fallback."""
        from openjiuwen.extensions.event_scheduler.core.retry_handler import RetryHandler
        from openjiuwen.extensions.event_scheduler.core.timer_dispatcher import TimerDispatcher

        config = EventSchedulerConfig(
            enable_retry=True,
            default_retry_policy=RetryPolicy(max_retries=2, base_delay=5.0),
        )
        dispatcher = TimerDispatcher(config=config, task_manager=mock_task_manager)
        handler = RetryHandler(
            config=config,
            task_manager=mock_task_manager,
            timer_dispatcher=dispatcher,
        )

        # Task without its own retry policy — should fall back to config default
        task = Task(
            session_id="session-1",
            task_id=str(uuid.uuid4()),
            task_type="data_extraction",
            status=TaskStatus.FAILED,
            error_message="Connection timeout",
            extensions={
                "event_scheduler": ScheduledTaskMixin().model_dump()
            },
        )

        result = await handler.handle_task_failure(task)
        assert result is True
        mock_task_manager.update_task_status.assert_called_once_with(
            task.task_id, TaskStatus.WAITING
        )

    async def test_handle_failure_calculates_backoff_delay(
            self, mock_task_manager
    ):
        """Test that backoff delay increases with retry count."""
        from openjiuwen.extensions.event_scheduler.core.retry_handler import RetryHandler
        from openjiuwen.extensions.event_scheduler.core.timer_dispatcher import TimerDispatcher

        config = EventSchedulerConfig(enable_retry=True)
        dispatcher = TimerDispatcher(config=config, task_manager=mock_task_manager)
        handler = RetryHandler(
            config=config,
            task_manager=mock_task_manager,
            timer_dispatcher=dispatcher,
        )

        policy = RetryPolicy(max_retries=5, base_delay=2.0, backoff_multiplier=2.0)
        task = Task(
            session_id="session-1",
            task_id=str(uuid.uuid4()),
            task_type="data_extraction",
            status=TaskStatus.FAILED,
            error_message="Connection timeout",
            extensions={
                "event_scheduler": ScheduledTaskMixin(
                    retry_policy=policy,
                    retry_count=2,
                ).model_dump()
            },
        )

        await handler.handle_task_failure(task)

        # Verify the updated mixin has incremented retry count
        updated_mixin = ScheduledTaskMixin(**task.extensions["event_scheduler"])
        assert updated_mixin.retry_count == 3
        assert updated_mixin.schedule_type == ScheduleType.DELAYED
        # delay = 2.0 * (2.0 ** 2) = 8.0
        assert updated_mixin.delay_seconds == 8.0
