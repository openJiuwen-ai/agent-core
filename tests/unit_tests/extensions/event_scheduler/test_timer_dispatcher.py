# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Tests for TimerDispatcher."""

from __future__ import annotations

import asyncio
import uuid
import pytest

from openjiuwen.core.controller.schema.task import TaskStatus
from openjiuwen.extensions.event_scheduler.schema import (
    ScheduleType,
    ScheduledTaskMixin,
    EventSchedulerConfig,
)


@pytest.mark.asyncio
class TestTimerDispatcher:
    """Tests for TimerDispatcher delayed execution logic."""

    async def test_schedule_delayed_task(self, timer_dispatcher):
        """Test scheduling a delayed task."""
        task_id = str(uuid.uuid4())
        mixin = ScheduledTaskMixin(
            schedule_type=ScheduleType.DELAYED,
            delay_seconds=5.0,
        )

        result = await timer_dispatcher.schedule_task(task_id, mixin)
        assert result is True
        assert timer_dispatcher.pending_count == 1

    async def test_schedule_immediate_task_returns_false(self, timer_dispatcher):
        """Test that immediate tasks are not accepted by the dispatcher."""
        task_id = str(uuid.uuid4())
        mixin = ScheduledTaskMixin(schedule_type=ScheduleType.IMMEDIATE)

        result = await timer_dispatcher.schedule_task(task_id, mixin)
        assert result is False
        assert timer_dispatcher.pending_count == 0

    async def test_cancel_scheduled_task(self, timer_dispatcher):
        """Test cancelling a scheduled task."""
        task_id = str(uuid.uuid4())
        mixin = ScheduledTaskMixin(
            schedule_type=ScheduleType.DELAYED,
            delay_seconds=60.0,
        )

        await timer_dispatcher.schedule_task(task_id, mixin)
        assert timer_dispatcher.pending_count == 1

        result = await timer_dispatcher.cancel_scheduled_task(task_id)
        assert result is True
        assert timer_dispatcher.pending_count == 0

    async def test_cancel_nonexistent_task(self, timer_dispatcher):
        """Test cancelling a task that doesn't exist."""
        result = await timer_dispatcher.cancel_scheduled_task("nonexistent")
        assert result is False

    async def test_delayed_task_transitions_to_submitted(
            self, timer_dispatcher, mock_task_manager
    ):
        """Test that a delayed task transitions to SUBMITTED after delay."""
        task_id = str(uuid.uuid4())
        mixin = ScheduledTaskMixin(
            schedule_type=ScheduleType.DELAYED,
            delay_seconds=0.0,  # Zero delay for immediate transition
        )

        await timer_dispatcher.schedule_task(task_id, mixin)

        # Manually trigger the check
        await timer_dispatcher._check_pending_tasks()

        mock_task_manager.update_task_status.assert_called_once_with(
            task_id, TaskStatus.SUBMITTED
        )
        assert timer_dispatcher.pending_count == 0

    async def test_max_scheduled_tasks_limit(self, mock_task_manager):
        """Test that max_scheduled_tasks limit is enforced."""
        from openjiuwen.extensions.event_scheduler.core.timer_dispatcher import TimerDispatcher

        config = EventSchedulerConfig(max_scheduled_tasks=2)
        dispatcher = TimerDispatcher(config=config, task_manager=mock_task_manager)

        mixin = ScheduledTaskMixin(
            schedule_type=ScheduleType.DELAYED,
            delay_seconds=60.0,
        )

        await dispatcher.schedule_task("task-1", mixin)
        await dispatcher.schedule_task("task-2", mixin)
        result = await dispatcher.schedule_task("task-3", mixin)

        assert result is False
        assert dispatcher.pending_count == 2

    async def test_start_and_stop(self, timer_dispatcher):
        """Test starting and stopping the dispatcher."""
        await timer_dispatcher.start()
        assert timer_dispatcher._running is True

        await timer_dispatcher.stop()
        assert timer_dispatcher._running is False
