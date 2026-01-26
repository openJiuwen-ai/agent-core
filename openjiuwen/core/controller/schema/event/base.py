# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
import uuid

from enum import Enum
from typing import Any, Dict, Optional
from pydantic import BaseModel, Field


class EventType(str, Enum):
    """Event Type Enumeration

    Defines all supported event types:
    - INPUT: User input event
    - TASK_INTERACTION: Task interaction event (requires user interaction during task execution)
    - TASK_COMPLETION: Task completion event
    - TASK_FAILED: Task failed event
    """
    INPUT = "input"
    TASK_INTERACTION = "task_interaction",
    TASK_COMPLETION = "task_completion",
    TASK_FAILED = "task_failed"


class Event(BaseModel):
    """Event Base Class

    Base class for all events, containing event type, event ID, and metadata.
    """
    event_type: EventType
    event_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    metadata: Optional[Dict[str, Any]] = None

    def __post_init__(self):
        """Post-initialization processing

        Ensures metadata is not None.
        """
        if self.metadata is None:
            self.metadata = {}