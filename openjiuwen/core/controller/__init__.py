# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Controller module - Agent controllers

This module re-exports from legacy submodule for backward compatibility.
"""

from openjiuwen.core.controller.legacy import (
    BaseController,
    IntentDetectionController,
    IntentType,
    Intent,
    TaskQueue,
    Task,
    TaskInput,
    TaskStatus,
    TaskResult,
    IntentDetector,
    Planner,
    Event,
    EventType,
    EventPriority,
    EventSource,
    EventContent,
    EventContext,
    SourceType,
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
