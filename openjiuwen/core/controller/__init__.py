# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Controller module - Agent controllers"""

from openjiuwen.core.controller.controller import BaseController

from openjiuwen.core.controller.intent_detection_controller import (
    IntentDetectionController,
    IntentType,
    Intent,
    TaskQueue,
)

from openjiuwen.core.controller.task.task import (
    Task,
    TaskInput,
    TaskStatus,
    TaskResult,
)

from openjiuwen.core.controller.reasoner.intent_detector import IntentDetector
from openjiuwen.core.controller.reasoner.planner import Planner

from openjiuwen.core.controller.event.event import (
    Event,
    EventType,
    EventPriority,
    EventSource,
    EventContent,
    EventContext,
    SourceType,
)

from openjiuwen.core.controller.config.reasoner_config import (
    IntentDetectionConfig,
    PlannerConfig,
    ProactiveIdentifierConfig,
    ReflectorConfig,
    ReasonerConfig,
)

_CONTROLLER_CLASSES = [
    "BaseController",
]

_INTENT_CLASSES = [
    "IntentDetectionController",
    "IntentType",
    "Intent",
    "TaskQueue"
]

_TASK_CLASSES = [
    "Task",
    "TaskInput",
    "TaskStatus",
    "TaskResult"
]

_REASONER_CLASSES = [
    "IntentDetector",
    "Planner"
]

_EVENT_CLASSES = [
    "Event",
    "EventType",
    "EventPriority",
    "EventSource",
    "EventContent",
    "EventContext",
    "SourceType",
]

_CONFIG_CLASSES = [
    "IntentDetectionConfig",
    "PlannerConfig",
    "ProactiveIdentifierConfig",
    "ReflectorConfig",
    "ReasonerConfig",
]

__all__ = (
        _CONTROLLER_CLASSES +
        _INTENT_CLASSES +
        _TASK_CLASSES +
        _REASONER_CLASSES +
        _EVENT_CLASSES +
        _CONFIG_CLASSES
)
