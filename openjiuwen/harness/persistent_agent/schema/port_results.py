# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Budget and execution link host responses. In-process calls raise on failure instead of returning unknown."""

from typing import Literal, Self

from pydantic import model_validator
from pydantic_core import PydanticCustomError

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error

from .execution import ExecutionContext
from .protocol import Identifier, InvocationRef, ProtocolModel, Reason


class BudgetAdmission(InvocationRef):
    """Admission decision and reservation reference for one model call."""

    model_call_id: Identifier
    status: Literal["admitted", "denied"]
    reservation_ref: Identifier | None = None
    reason: Reason | None = None

    @model_validator(mode="after")
    def validate_admission(self) -> Self:
        """Require reservation evidence only for an admitted model call."""
        if self.status == "admitted":
            if self.reservation_ref is None:
                raise PydanticCustomError("persistent_protocol", "admitted model call requires a reservation")
        elif self.reservation_ref is not None or self.reason is None:
            raise PydanticCustomError("persistent_protocol", "denied admission needs a reason and no reservation")
        return self

    def assert_call(self, execution: ExecutionContext, model_call_id: str) -> None:
        """Ensure this admission belongs to the requested execution and call."""
        self.assert_matches(execution)
        if self.model_call_id != model_call_id:
            raise build_error(StatusCode.DEEPAGENT_CONTEXT_PARAM_ERROR, error_msg="budget model call mismatch")


class BudgetReport(InvocationRef):
    """Host receipt for reporting actual model usage."""

    model_call_id: Identifier
    status: Literal["confirmed", "rejected"]
    receipt_ref: Identifier | None = None
    reason: Reason | None = None

    @model_validator(mode="after")
    def validate_report(self) -> Self:
        """Require a receipt for confirmed usage and reasons otherwise."""
        if self.status == "confirmed":
            if self.receipt_ref is None:
                raise PydanticCustomError("persistent_protocol", "confirmed usage report requires receipt")
        elif self.receipt_ref is not None or self.reason is None:
            raise PydanticCustomError("persistent_protocol", "rejected report needs a reason and no receipt")
        return self

    def assert_call(self, execution: ExecutionContext, model_call_id: str) -> None:
        """Ensure this report belongs to the requested execution and call."""
        self.assert_matches(execution)
        if self.model_call_id != model_call_id:
            raise build_error(StatusCode.DEEPAGENT_CONTEXT_PARAM_ERROR, error_msg="usage model call mismatch")


class ExecutionLink(ProtocolModel):
    """The Execution of the same Request one Execution follows, as the host saved it before dispatch.

    ``first`` is the Request's first Execution and has no predecessor; a confirmation resume or a
    continuation always has one, never itself.
    """

    namespace_id: Identifier
    agent_id: Identifier
    session_id: Identifier
    request_id: Identifier
    execution_id: Identifier
    predecessor_id: Identifier | None = None
    relation: Literal["first", "confirmation_resume", "continuation"]

    @model_validator(mode="after")
    def validate_link(self) -> Self:
        """Only the first Execution lacks a predecessor, and no Execution follows itself."""
        if (self.relation == "first") != (self.predecessor_id is None):
            raise PydanticCustomError("persistent_protocol", "only the first Execution of a Request has no predecessor")
        if self.predecessor_id == self.execution_id:
            raise PydanticCustomError("persistent_protocol", "an Execution cannot follow itself")
        return self
