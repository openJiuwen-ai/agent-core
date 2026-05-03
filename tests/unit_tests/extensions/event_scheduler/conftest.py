# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Shared fixtures for event scheduler tests."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio

from openjiuwen.core.controller.schema.task import Task, TaskStatus
from openjiuwen.extensions.event_scheduler.schema import (
    EventSchedulerConfig,
    EventChainRule,
    RetryPolicy,
    ScheduleType,
    ScheduledTaskMixin,
)
from openjiuwen.extensions.event_scheduler.core.timer_dispatcher import TimerDispatcher
from openjiuwen.extensions.event_scheduler.core.event_chain import EventChainHandler
from openjiuwen.extensions.event_scheduler.core.retry_handler import RetryHandler
from openjiuwen.extensions.event_scheduler.service.event_driven_scheduler import EventDrivenScheduler


@pytest.fixture
def mock_task_manager():
    """Create a mock TaskManager."""
    manager = AsyncMock()
    manager.add_task = AsyncMock()
    manager.get_task = AsyncMock(return_value=[])
    manager.update_task_status = AsyncMock()
    return manager


@pytest.fixture
def mock_event_queue():
    """Create a mock EventQueue."""
    queue = AsyncMock()
    queue.publish_event = AsyncMock()
    queue.subscribe = AsyncMock()
    return queue


@pytest.fixture
def sample_chain_rules():
    """Create sample chain rules for testing."""
    return [
        EventChainRule(
            rule_id="rule-1",
            source_task_type="data_extraction",
            target_task_type="data_validation",
            target_description="Validate extracted data",
        ),
        EventChainRule(
            rule_id="rule-2",
            source_task_type="data_validation",
            target_task_type="data_load",
            target_description="Load validated data",
            condition="format=csv",
        ),
    ]


@pytest.fixture
def default_config(sample_chain_rules):
    """Create a default EventSchedulerConfig for testing."""
    return EventSchedulerConfig(
        enable_delayed_scheduling=True,
        enable_event_chaining=True,
        enable_retry=True,
        chain_rules=sample_chain_rules,
        default_retry_policy=RetryPolicy(max_retries=3, base_delay=1.0),
        scheduler_poll_interval=0.1,
    )


@pytest.fixture
def sample_task():
    """Create a sample task for testing."""
    return Task(
        session_id="session-1",
        task_id=str(uuid.uuid4()),
        task_type="data_extraction",
        description="Extract data from source",
        priority=1,
        status=TaskStatus.COMPLETED,
        metadata={"format": "csv"},
    )


@pytest.fixture
def sample_failed_task():
    """Create a sample failed task for testing."""
    return Task(
        session_id="session-1",
        task_id=str(uuid.uuid4()),
        task_type="data_extraction",
        description="Extract data from source",
        priority=1,
        status=TaskStatus.FAILED,
        error_message="Connection timeout",
        extensions={
            "event_scheduler": ScheduledTaskMixin(
                retry_policy=RetryPolicy(max_retries=3, base_delay=1.0),
                retry_count=0,
            ).model_dump()
        },
    )


@pytest_asyncio.fixture
async def timer_dispatcher(default_config, mock_task_manager):
    """Create a TimerDispatcher instance for testing."""
    return TimerDispatcher(
        config=default_config,
        task_manager=mock_task_manager,
    )


@pytest_asyncio.fixture
async def event_chain_handler(default_config, mock_task_manager):
    """Create an EventChainHandler instance for testing."""
    return EventChainHandler(
        config=default_config,
        task_manager=mock_task_manager,
    )


@pytest_asyncio.fixture
async def retry_handler(default_config, mock_task_manager, timer_dispatcher):
    """Create a RetryHandler instance for testing."""
    return RetryHandler(
        config=default_config,
        task_manager=mock_task_manager,
        timer_dispatcher=timer_dispatcher,
    )


@pytest_asyncio.fixture
async def scheduler(default_config, mock_task_manager, mock_event_queue):
    """Create an EventDrivenScheduler instance for testing."""
    return EventDrivenScheduler(
        config=default_config,
        task_manager=mock_task_manager,
        event_queue=mock_event_queue,
    )
