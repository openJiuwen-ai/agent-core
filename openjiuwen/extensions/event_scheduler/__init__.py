# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Event Scheduler Extension

Provides event-driven task scheduling capabilities for the openJiuwen agent
framework, including delayed execution, automatic task chaining, and retry
with exponential backoff.

Public API:
    from openjiuwen.extensions.event_scheduler import (
        EventDrivenScheduler,
        EventSchedulerConfig,
        ScheduledTaskMixin,
        EventChainRule,
        RetryPolicy,
        ScheduleType,
    )

Features:
- Delayed task execution: schedule tasks for future execution
- Event-driven chaining: automatically trigger downstream tasks on completion
- Automatic retry: retry failed tasks with configurable exponential backoff
- Non-invasive: uses Task.extensions field, no core schema modifications
"""

from openjiuwen.extensions.event_scheduler.service.event_driven_scheduler import (
    EventDrivenScheduler,
)
from openjiuwen.extensions.event_scheduler.schema import (
    EventSchedulerConfig,
    ScheduledTaskMixin,
    EventChainRule,
    RetryPolicy,
    ScheduleType,
)
from openjiuwen.extensions.event_scheduler.core.timer_dispatcher import (
    TimerDispatcher,
)
from openjiuwen.extensions.event_scheduler.core.event_chain import (
    EventChainHandler,
)
from openjiuwen.extensions.event_scheduler.core.retry_handler import (
    RetryHandler,
)

__all__ = [
    "EventDrivenScheduler",
    "EventSchedulerConfig",
    "ScheduledTaskMixin",
    "EventChainRule",
    "RetryPolicy",
    "ScheduleType",
    "TimerDispatcher",
    "EventChainHandler",
    "RetryHandler",
]
