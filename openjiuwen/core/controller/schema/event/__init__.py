# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""
Event Data Model Definitions

This module defines event-related data models for the controller, including:
- EventType: Event type enumeration
- Event: Event base class
- InputEvent: Input event
- TaskInteractionEvent: Task interaction event during execution
- TaskCompletionEvent: Task completion event
- TaskFailedEvent: Task failed event

Events are the main input form for the controller, used to pass information within the controller.
"""


from openjiuwen.core.controller.schema.event.base import EventType, Event
from openjiuwen.core.controller.schema.event.event import (
    InputEvent,
    TaskInteractionEvent,
    TaskCompletionEvent,
    TaskFailedEvent
)


__all__ = [
    "EventType",
    "Event",
    "InputEvent",
    "TaskInteractionEvent",
    "TaskCompletionEvent",
    "TaskFailedEvent",
    "TaskFailedEvent",
]
