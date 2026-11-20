# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Tests for EventDrivenScheduler."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from openjiuwen.core.controller.schema.task import Task, TaskStatus
from openjiuwen.core.controller.schema.event import TaskCompletionEvent, TaskFailedEvent
from openjiuwen.extensions.event_scheduler.schema import (
    ScheduleType,
    ScheduledTaskMixin,
    EventChainRule,
    RetryPolicy,
    EventSchedulerConfig,
)
from openjiuwen.extensions.event_scheduler.core.timer_dispatcher import TimerDispatcher
from openjiuwen.extensions.event_scheduler.core.event_chain import EventChainHandler
from openjiuwen.extensions.event_scheduler.core.retry_handler import RetryHandler


@pytest.mark.asyncio
class TestEventDrivenScheduler:
    """Tests for EventDrivenScheduler orchestration logic."""

    async def test_submit_immediate_task(self, scheduler, mock_task_manager):
        """Test submitting an immediate task creates it with SUBMITTED status."""
        mixin = ScheduledTaskMixin(schedule_type=ScheduleType.IMMEDIATE)

        task_id = await scheduler.submit_scheduled_task(
            session_id="session-1",
            task_type="data_extraction",
            mixin=mixin,
            description="Extract data",
        )

        assert task_id is not None
        mock_task_manager.add_task.assert_called_once()
        created_task = mock_task_manager.add_task.call_args[0][0]
        assert created_task.status == TaskStatus.SUBMITTED
        assert created_task.task_type == "data_extraction"

    async def test_submit_delayed_task(self, scheduler, mock_task_manager):
        """Test submitting a delayed task creates it with WAITING status."""
        mixin = ScheduledTaskMixin(
            schedule_type=ScheduleType.DELAYED,
            delay_seconds=30.0,
        )

        task_id = await scheduler.submit_scheduled_task(
            session_id="session-1",
            task_type="data_extraction",
            mixin=mixin,
        )

        assert task_id is not None
        mock_task_manager.add_task.assert_called_once()
        created_task = mock_task_manager.add_task.call_args[0][0]
        assert created_task.status == TaskStatus.WAITING
        assert scheduler.timer_dispatcher.pending_count == 1

    async def test_submit_task_stores_extensions(self, scheduler, mock_task_manager):
        """Test that scheduling metadata is stored in task extensions."""
        mixin = ScheduledTaskMixin(
            schedule_type=ScheduleType.DELAYED,
            delay_seconds=10.0,
            retry_policy=RetryPolicy(max_retries=5),
        )

        await scheduler.submit_scheduled_task(
            session_id="session-1",
            task_type="data_extraction",
            mixin=mixin,
        )

        created_task = mock_task_manager.add_task.call_args[0][0]
        assert "event_scheduler" in created_task.extensions
        stored_mixin = ScheduledTaskMixin(**created_task.extensions["event_scheduler"])
        assert stored_mixin.schedule_type == ScheduleType.DELAYED
        assert stored_mixin.delay_seconds == 10.0
        assert stored_mixin.retry_policy.max_retries == 5

    async def test_on_task_completed_triggers_chain(
            self, scheduler, mock_task_manager, sample_task
    ):
        """Test that task completion event triggers chain rules."""
        event = TaskCompletionEvent(task=sample_task)

        await scheduler.on_task_completed(event)

        # sample_task is data_extraction type, which matches rule-1 -> data_validation
        mock_task_manager.add_task.assert_called_once()
        chained_task = mock_task_manager.add_task.call_args[0][0]
        assert chained_task.task_type == "data_validation"

    async def test_on_task_completed_no_task_reference(self, scheduler, mock_task_manager):
        """Test that completion event without task is handled gracefully."""
        event = TaskCompletionEvent(task=None)

        await scheduler.on_task_completed(event)

        mock_task_manager.add_task.assert_not_called()

    async def test_on_task_failed_triggers_retry(
            self, scheduler, mock_task_manager, sample_failed_task
    ):
        """Test that task failure event triggers retry handler."""
        event = TaskFailedEvent(task=sample_failed_task)

        await scheduler.on_task_failed(event)

        mock_task_manager.update_task_status.assert_called_once_with(
            sample_failed_task.task_id, TaskStatus.WAITING
        )

    async def test_on_task_failed_no_task_reference(self, scheduler, mock_task_manager):
        """Test that failure event without task is handled gracefully."""
        event = TaskFailedEvent(task=None)

        await scheduler.on_task_failed(event)

        mock_task_manager.update_task_status.assert_not_called()

    async def test_start_enables_timer_dispatcher(self, scheduler):
        """Test that start() activates the timer dispatcher."""
        await scheduler.start()

        assert scheduler.timer_dispatcher._running is True

        await scheduler.stop()

    async def test_stop_deactivates_timer_dispatcher(self, scheduler):
        """Test that stop() deactivates the timer dispatcher."""
        await scheduler.start()
        await scheduler.stop()

        assert scheduler.timer_dispatcher._running is False

    async def test_add_chain_rule(self, scheduler, mock_task_manager):
        """Test dynamically adding a chain rule."""
        new_rule = EventChainRule(
            rule_id="dynamic-rule",
            source_task_type="processing",
            target_task_type="notification",
        )
        scheduler.add_chain_rule(new_rule)

        completed_task = Task(
            session_id="session-1",
            task_id=str(uuid.uuid4()),
            task_type="processing",
            status=TaskStatus.COMPLETED,
        )
        event = TaskCompletionEvent(task=completed_task)
        await scheduler.on_task_completed(event)

        mock_task_manager.add_task.assert_called_once()
        chained_task = mock_task_manager.add_task.call_args[0][0]
        assert chained_task.task_type == "notification"

    async def test_remove_chain_rule(self, scheduler):
        """Test removing a chain rule by ID."""
        assert scheduler.remove_chain_rule("rule-1") is True
        assert scheduler.remove_chain_rule("nonexistent") is False

    async def test_property_accessors(self, scheduler):
        """Test that property accessors return correct component instances."""
        assert isinstance(scheduler.timer_dispatcher, TimerDispatcher)
        assert isinstance(scheduler.event_chain_handler, EventChainHandler)
        assert isinstance(scheduler.retry_handler, RetryHandler)
        assert isinstance(scheduler.config, EventSchedulerConfig)

    async def test_delayed_scheduling_disabled(self, mock_task_manager, mock_event_queue):
        """Test that timer dispatcher is not started when delayed scheduling is disabled."""
        from openjiuwen.extensions.event_scheduler.service.event_driven_scheduler import (
            EventDrivenScheduler,
        )

        config = EventSchedulerConfig(enable_delayed_scheduling=False)
        sched = EventDrivenScheduler(
            config=config,
            task_manager=mock_task_manager,
            event_queue=mock_event_queue,
        )

        await sched.start()
        assert sched.timer_dispatcher._running is False

        await sched.stop()
