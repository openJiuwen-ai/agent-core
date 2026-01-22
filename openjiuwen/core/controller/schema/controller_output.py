# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.


"""Controller output data model definitions.

This module defines data models for controller outputs, including:

- ControllerOutputPayload: payload of controller output.
- ControllerOutputChunk: streaming controller output chunk.
- ControllerOutput: batch controller output.

Output types:
- task_completion: task has completed.
- task_interaction: task requires user interaction.
- task_failed: task has failed.
- processing: task is still in progress.
"""
from typing import Optional, Dict, Any, List, Literal

from pydantic import BaseModel, Field

from openjiuwen.core.controller.schema.dataframe import DataFrame

from openjiuwen.core.controller.schema.event import EventType
from openjiuwen.core.session.stream.base import OutputSchema


class ControllerOutputPayload(BaseModel):
    """Payload of a controller output.

    Contains the output type, data, and metadata. This is the core data part
    of controller outputs.

    Attributes:
        type: Output type, one of task completion, interaction, failure or
            processing.
        data: List of data frames representing the actual output content.
        metadata: Optional metadata with additional information about the
            output.
    """
    type: Literal[EventType.TASK_COMPLETION, EventType.TASK_INTERACTION, EventType.TASK_FAILED, "processing"]
    data: List[DataFrame] = Field(default_factory=list)
    metadata: Optional[Dict[str, Any]] = None


class ControllerOutputChunk(OutputSchema):
    """Streaming controller output chunk.

    Represents a single chunk in a streaming response, including index,
    type, payload and whether it is the last chunk.

    Attributes:
        index: Index of the chunk, indicating its order.
        type: Output type, fixed to ``"controller_output"``.
        payload: Output payload with the actual data.
        last_chunk: Whether this is the last chunk of the stream.
    """
    index: int
    type: str = "controller_output"
    payload: ControllerOutputPayload = None
    last_chunk = False


class ControllerOutput(BaseModel):
    """Batch controller output.

    Represents the result of non-streaming execution, containing the type,
    data list and the associated input event ID.

    Attributes:
        type: Output type, one of task completion, interaction, failure or
            processing.
        data: Output data, either a list of ``ControllerOutputChunk`` or a
            dictionary, depending on the use case.
        input_event_id: Identifier of the input event this output is related
            to.
    """
    type: Literal[EventType.TASK_COMPLETION, EventType.TASK_INTERACTION, EventType.TASK_FAILED, "processing"]
    data: List[ControllerOutputChunk] | Dict
    input_event_id: Optional[str] = None

