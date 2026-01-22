# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.


"""Intent schema definitions.

Main classes:
- IntentType: enumeration of intent types.
- Intent: intent data model.

Intent schemas define the structure and types of user intents, which the
controller uses to recognize and handle user requests.
"""
from enum import Enum
from typing import Optional, Dict, Any, List

from pydantic import BaseModel, Field

from openjiuwen.core.controller.schema.event import Event


class IntentType(Enum):
    """Intent type enumeration.

    Defines all possible user intent types, which allow the controller to
    understand requests and route them to the proper logic.

    Intent types:
        - CREATE_TASK: Create a new task. Executes a new task and may interrupt
          currently running tasks.
        - PAUSE_TASK: Pause the currently running task. Status becomes
          ``paused`` and can be resumed later.
        - RESUME_TASK: Resume a previously paused task, changing status from
          ``paused`` to ``submitted``.
        - CONTINUE_TASK: Continue a task based on a completed task, reusing
          its context.
        - SUPPLEMENT_TASK: Provide additional information required by a task
          and then continue execution.
        - CANCEL_TASK: Cancel the currently running task, setting status to
          ``cancelled``.
        - MODIFY_TASK: Modify parameters or configuration of a running task.
          After modification, status becomes ``submitted`` to re-run.
        - SWITCH_TASK: Switch tasks by interrupting all currently running
          tasks and executing a new one.
        - UNKNOWN_TASK: Unknown intent; the system should ask the user for
          clarification.
    """
    CREATE_TASK = "create_task"
    PAUSE_TASK = "pause_task"
    RESUME_TASK = "resume_task"
    CONTINUE_TASK = "continue_task"
    SUPPLEMENT_TASK = "supplement_task"
    CANCEL_TASK = "cancel_task"
    MODIFY_TASK = "modify_task"
    SWITCH_TASK = "switch_task"
    UNKNOWN_TASK = "unknown_task"


class Intent(BaseModel):
    """Intent data model.

    Represents a single user intent, including its type, related event,
    target tasks, and additional information. The intent recognizer converts
    events into ``Intent`` instances, which are then routed to the proper
    handlers.

    Attributes:
        intent_type: Type of intent indicating the requested operation.
        event: Associated event, usually an ``InputEvent`` containing the
            original user input.
        target_task_id: Target task ID, when the intent refers to an existing
            task.
        target_task_description: Description of the target task, used when
            creating a new task.
        depend_task_id: ID of the dependent task for ``CONTINUE_TASK`` intents.
        supplementary_info: Additional information for ``SUPPLEMENT_TASK``
            intents.
        modification_details: Details of modifications for ``MODIFY_TASK``
            intents.
        confidence: Confidence score of recognition, between 0.0 and 1.0
            (default 1.0).
        metadata: Extra metadata related to this intent.
        clarification_prompt: Clarification prompt for ``UNKNOWN_TASK`` intents
            used to ask the user for more details.

    Note:
        ``__post_init__`` automatically runs ``_validate`` after creation.
    """
    intent_type: IntentType
    event: "Event"
    target_task_id: Optional[str]
    target_task_description: Optional[str] = None
    depend_task_id: str = None
    supplementary_info: Optional[Dict[str, Any]] = None
    modification_details: Optional[Dict[str, Any]] = None
    confidence: float = 1.0
    metadata: Optional[Dict[str, Any]] = None
    clarification_prompt: Optional[str] = None

    def __post_init__(self):
        """Post-init hook to normalize metadata and validate the intent."""
        if self.metadata is None:
            self.metadata = {}
        self._validate()

    def _validate(self):
        """Validate that the intent data is consistent.

        Example checks include:
            - Required fields for certain intent types are present.
            - Field values fall within allowed ranges or sets.

        Raises:
            ValueError: If the intent data is invalid.
        """
        ...