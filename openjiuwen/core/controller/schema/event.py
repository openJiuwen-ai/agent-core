# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.


"""Event data model definitions.

This module defines data models related to controller events, including:

- EventType: enumeration of event types.
- Event: base event model.
- InputEvent: input event from the user.
- TaskInteractionEvent: interaction event during task execution.
- TaskCompletionEvent: event emitted when a task completes.
- TaskFailedEvent: event emitted when a task fails.

Events are the primary input to the controller and are used to pass
information between internal components.
"""
from enum import Enum
from typing import Optional, Dict, Any, List

from pydantic import BaseModel, Field

from openjiuwen.core.controller.schema.dataframe import DataFrame
from openjiuwen.core.controller.schema.task import Task


class EventType(str, Enum):
    """Event type enumeration.

    Defines all supported event types:
        - INPUT: user input events.
        - TASK_INTERACTION: task interaction events (when a task needs user
          interaction).
        - TASK_COMPLETION: task completion events.
        - TASK_FAILED: task failure events.
    """
    INPUT = "input"
    TASK_INTERACTION = "task_interaction",
    TASK_COMPLETION = "task_completion",
    TASK_FAILED = "task_failed"


class Event(BaseModel):
    """Base event model.

    All events extend this base model and include an event type, an optional
    event ID and optional metadata.
    """
    event_type: EventType
    event_id: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None

    def __post_init__(self):
        """Post-init hook to ensure metadata is not ``None``."""
        if self.metadata is None:
            self.metadata = {}


class InputEvent(Event):
    """Input event from the user.

    Represents user input into the controller and is the main entrypoint
    for requests.

    Attributes:
        event_type: Event type, fixed to ``EventType.INPUT``.
        input_data: List of input data frames (text, file, JSON).
    """
    event_type: EventType = EventType.INPUT
    input_data: List[DataFrame] = Field(default_factory=list)

    @classmethod
    def from_user_input(cls, user_input: str) -> "Event":
        """Create an ``InputEvent`` from raw user input.

        Convenience constructor that converts a plain string into an
        ``InputEvent`` instance.

        Args:
            user_input: Raw user input string.

        Returns:
            Event: Input event instance.
        """
        ...


class TaskInteractionEvent(Event):
    """Task interaction event.

    Emitted during task execution when user interaction is required (e.g.
    to provide additional information or confirmation).

    Attributes:
        event_type: Event type, fixed to ``EventType.TASK_INTERACTION``.
        interaction: List of interaction payloads that should be surfaced
            to the user.
        task: Associated task instance.
    """
    event_type = EventType.TASK_INTERACTION
    interaction: List[DataFrame] = Field(default_factory=list)
    task: Task = None


class TaskCompletionEvent(Event):
    """Task completion event.

    Emitted when task execution finishes successfully and contains the
    resulting outputs.

    Attributes:
        event_type: Event type, fixed to ``EventType.TASK_COMPLETION``.
        task_result: List of result data frames produced by the task.
        task: Associated task instance.
    """
    event_type = EventType.TASK_COMPLETION
    task_result: List[DataFrame] = Field(default_factory=list)
    task: Task = None


class TaskFailedEvent(Event):
    """Task failure event.

    Emitted when task execution fails and includes error information.

    Attributes:
        event_type: Event type, fixed to ``EventType.TASK_FAILED``.
        error_message: Error message describing the failure.
        task: Associated task instance.
    """
    event_type = EventType.TASK_FAILED
    error_message: str = None
    task: Task = None

