# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from __future__ import annotations

from typing import Any, Dict

from pydantic import BaseModel, Field, model_serializer

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.core.common.utils.pydantic_compat import RestoreFieldDefaultsOnUnpickle

_sentinel = object()


# Restores defaults on unpickle: a paused sub-agent call persists its resume arguments, which carry the input.
class InteractiveInput(RestoreFieldDefaultsOnUnpickle, BaseModel):
    # key is id of interaction, value is input for the id
    user_inputs: Dict[str, Any] = Field(default_factory=dict)

    # input not bind to any id, used for the first interaction
    raw_inputs: Any = Field(default=None)

    # key is the interrupt_id of a paused tool-call interrupt, value is its answer
    interrupt_answers: Dict[str, Any] = Field(default_factory=dict)

    def __init__(self, raw_inputs: Any = _sentinel, **data: Any):
        if data:
            # Validating serialized data (model_validate / model_validate_json).
            if raw_inputs is not _sentinel:
                data["raw_inputs"] = raw_inputs
            super().__init__(**data)
            return
        super().__init__(**{})
        if raw_inputs is None:
            raise build_error(StatusCode.INTERACTION_INPUT_INVALID, reason="value of raw_inputs is none")
        if raw_inputs is _sentinel:
            self.raw_inputs = None
            return
        self.raw_inputs = raw_inputs

    @model_serializer(mode="wrap")
    def _omit_empty_interrupt_answers(self, handler):
        # Inputs without interrupt answers serialize exactly as before the field existed.
        data = handler(self)
        if not data.get("interrupt_answers"):
            data.pop("interrupt_answers", None)
        return data

    def update(self, node_id: str, value: Any):
        """Answer by interaction id (a workflow node id, or the id of a paused tool call).

        For a paused tool call the agent translates this, when it resumes, into
        an answer to the interrupt currently pending on that call.
        """
        self._check_addressed_answer(node_id, value)
        self.user_inputs[node_id] = value

    def answer(self, interrupt_id: str, value: Any) -> None:
        """Answer one tool-call interrupt by its ``interrupt_id``.

        The id is ``InterruptRequest.interrupt_id`` of the paused request.
        Only the rail that raised that interrupt receives the value.
        """
        if not isinstance(interrupt_id, str) or not interrupt_id:
            raise build_error(
                StatusCode.INTERACTION_INPUT_INVALID,
                reason="interrupt_id must be a non-empty string",
            )
        self._check_addressed_answer(interrupt_id, value)
        self.interrupt_answers[interrupt_id] = value

    def _check_addressed_answer(self, key: str, value: Any) -> None:
        if self.raw_inputs is not None:
            raise build_error(StatusCode.INTERACTION_INPUT_INVALID, reason="raw_inputs existed, update is invalid")
        if key is None or value is None:
            raise build_error(StatusCode.INTERACTION_INPUT_INVALID, reason="value is none or node_id is none")
