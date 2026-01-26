# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.


"""Controller data model definitions.

This package defines all data models related to the controller, including:

- DataFrame: data frames (text, file, JSON).
- Event: events and event types (input, task interaction, completion,
  failure).
- ControllerOutput: controller output (batch and streaming).
- Intent: intent and intent types.
- Task: tasks and task execution status.
"""
from openjiuwen.core.controller.schema.controller_output import (
    ControllerOutputPayload, ControllerOutputChunk, ControllerOutput
)
from openjiuwen.core.controller.schema.dataframe import TextDataFrame, FileDataFrame, JsonDataFrame, DataFrame
from openjiuwen.core.controller.schema.event import (
    EventType, Event, InputEvent, TaskInteractionEvent, TaskCompletionEvent, TaskFailedEvent
)
from openjiuwen.core.controller.schema.intent import IntentType, Intent
from openjiuwen.core.controller.schema.task import TaskStatus, Task


__all__ = [
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

]