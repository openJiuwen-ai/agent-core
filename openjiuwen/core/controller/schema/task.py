# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.


"""Task data model definitions.

This module defines data models related to tasks:

- TaskStatus: enumeration of task states.
- Task: task data model.

Task state transitions:
    submitted -> working -> (completed | failed | paused | canceled)
                    |
                    -> input-required -> (continue execution or cancel)
"""
from enum import Enum
from typing import Optional, List, Dict, Any, Union

from pydantic import BaseModel, Field

from openjiuwen.core.controller.schema import InputEvent
from openjiuwen.core.controller.schema.controller_output import ControllerOutputChunk


class TaskStatus(str, Enum):
    """Task status enumeration.

    Defines all possible task states:
        - SUBMITTED: task has been submitted and is waiting to run.
        - WORKING: task is currently running.
        - PAUSED: task has been paused.
        - INPUT_REQUIRED: task requires user input.
        - COMPLETED: task finished successfully.
        - CANCELED: task has been canceled.
        - FAILED: task execution failed.
        - WAITING: waiting (e.g. on dependent tasks).
        - UNKNOWN: unknown state.
    """
    SUBMITTED = "submitted"
    WORKING = "working"
    PAUSED = "paused"
    INPUT_REQUIRED = "input-required"
    COMPLETED = "completed"
    CANCELED = "canceled"
    FAILED = "failed"
    WAITING = "waiting"
    UNKNOWN = "unknown"


class Task(BaseModel):
    """Task model.

    Defines the structure of a task, including basic information, status,
    inputs/outputs, and hierarchical relationships.

    Attributes:
        session_id: Session ID to which this task belongs.
        task_id: Unique identifier of the task.
        task_type: Logical task type, used to find the corresponding
            ``TaskExecutor``.
        description: Human-readable description of the task.
        priority: Task priority. Smaller values indicate higher priority.
            Default is 1.
        inputs: All input events related to this task.
        outputs: Output frames produced during task execution.
        Status: Current task status.
        parent_task_id: Parent task ID, used for building task hierarchies.
        context_id: Context ID, used to associate contextual information.
        input_required_fields: Schema for fields that require user input when
            status is ``INPUT_REQUIRED``.
        error_message: Error message when status is ``FAILED``.
        metadata: Arbitrary metadata associated with the task.
    """
    session_id: str
    task_id: str
    task_type: str
    description: Optional[str]
    priority: int = 1
    inputs: List[InputEvent] = None
    outputs: List[ControllerOutputChunk] = Field(default_factory=list)
    Status: TaskStatus = TaskStatus.UNKNOWN
    parent_task_id: str = None
    context_id: str = None
    input_required_fields: Optional[Union[dict[str, Any], BaseModel]] = Field(default=None)
    error_message: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None

