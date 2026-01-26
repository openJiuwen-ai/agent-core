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

from openjiuwen.core.controller.schema import (
    TextDataFrame,
    FileDataFrame,
    JsonDataFrame,
    DataFrame,
    EventType,
    InputEvent,
    TaskInteractionEvent,
    TaskCompletionEvent,
    TaskFailedEvent,
    ControllerOutputPayload,
    ControllerOutputChunk,
    ControllerOutput,
    IntentType,
    Intent,
    TaskStatus,
    Task
)
from openjiuwen.core.controller.modules import (
    EventHandlerInput,
    EventHandler,
    EventQueue,
    TaskManagerState,
    TaskManager,
    TaskFilter,
    TaskExecutor,
    TaskExecutorRegistry,
    TaskScheduler,
)
from openjiuwen.core.controller.config import ControllerConfig
from openjiuwen.core.controller.base import Controller

_NEW_CLASS = [
    # ========================= 数据模型定义 =============================
    # 数据单元
    "TextDataFrame",
    "FileDataFrame",
    "JsonDataFrame",
    "DataFrame",
    # 事件（控制器输入）
    "EventType",
    "Event",
    "InputEvent",
    "TaskInteractionEvent",
    "TaskCompletionEvent",
    "TaskFailedEvent",
    # 控制器输出
    "ControllerOutputPayload",
    "ControllerOutputChunk",
    "ControllerOutput",
    # 意图
    "IntentType",
    "Intent",
    # 任务
    "TaskStatus",
    "Task",
    # ========================= 控制器内部模块 =============================
    # 事件队列和事件处理
    "EventHandlerInput",
    "EventHandler",
    "EventQueue",
    # 任务管理
    "TaskManagerState",
    "TaskManager",
    "TaskFilter",
    # 任务执行调度
    "TaskExecutor",
    "TaskExecutorRegistry",
    "TaskScheduler",
    # =========================     控制器   =============================
    "ControllerConfig",
    "Controller"
]


__all__ = (
        _CONTROLLER_CLASSES +
        _INTENT_CLASSES +
        _TASK_CLASSES +
        _REASONER_CLASSES +
        _EVENT_CLASSES +
        _CONFIG_CLASSES +
        _NEW_CLASS
)
