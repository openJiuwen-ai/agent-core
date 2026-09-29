# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Host responses with explicit uncertainty; none of these DTOs perform commits."""

from typing import Annotated, Literal, Self

from pydantic import Field, model_validator
from pydantic_core import PydanticCustomError

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error

from .events import AgentEvent
from .execution import ExecutionContext
from .protocol import Digest, Identifier, InvocationRef, Nonnegative, Positive, Reason, Text


class CommitResult(InvocationRef):
    """Host response for submitting or querying one event proposal."""

    event_id: Identifier
    local_seq: Positive
    status: Literal["confirmed", "rejected", "conflict", "unknown", "not_found"]
    payload_digest: Digest | None = None
    request_event_seq: Positive | None = None
    query_complete: bool = Field(default=False, strict=True)
    watermark: Nonnegative | None = None
    reason: Reason | None = None

    @model_validator(mode="after")
    def validate_commit(self) -> Self:
        """Validate confirmation evidence and preserve unknown outcomes."""
        if self.status == "confirmed":
            if self.request_event_seq is None or self.payload_digest is None:
                raise PydanticCustomError("persistent_protocol", "confirmed event requires sequence and digest")
        elif self.request_event_seq is not None:
            raise PydanticCustomError("persistent_protocol", "unconfirmed event cannot carry a success sequence")
        if self.status in ("rejected", "conflict", "unknown") and self.reason is None:
            raise PydanticCustomError("persistent_protocol", "rejected/conflict/unknown requires a reason")
        if self.status == "not_found" and (not self.query_complete or self.watermark is None):
            raise PydanticCustomError("persistent_protocol", "not_found requires complete query and watermark")
        if self.query_complete != (self.watermark is not None):
            raise PydanticCustomError("persistent_protocol", "query completeness and watermark must be paired")
        if self.status == "confirmed" and self.watermark is not None and self.watermark < self.request_event_seq:
            raise PydanticCustomError("persistent_protocol", "query watermark precedes confirmed sequence")
        return self

    def assert_event(self, event: AgentEvent, *, query: bool = False) -> None:
        """Match the whole queried proposal, not merely its event_id."""
        if not isinstance(event, AgentEvent) or any(
            getattr(self, name) != getattr(event, name) for name in InvocationRef.model_fields
        ):
            raise build_error(StatusCode.DEEPAGENT_CONTEXT_PARAM_ERROR, error_msg="commit invocation/epoch mismatch")
        if self.event_id != event.event_id or self.local_seq != event.local_seq:
            raise build_error(StatusCode.DEEPAGENT_INPUT_PARAM_ERROR, error_msg="commit event mismatch")
        if (
            self.status != "conflict"
            and self.payload_digest is not None
            and self.payload_digest != event.proposal_digest()
        ):
            raise build_error(StatusCode.DEEPAGENT_INPUT_PARAM_ERROR, error_msg="commit payload conflict")
        if self.status == "not_found" and not query:
            raise build_error(StatusCode.DEEPAGENT_INPUT_PARAM_ERROR, error_msg="not_found is a query result only")


class AuthorityResult(InvocationRef):
    """Host decision and scope for one execution authority check."""

    status: Literal["allowed", "denied", "unknown"]
    policy_revision: Identifier | None = None
    scope: frozenset[Identifier] = frozenset()
    reason: Reason | None = None

    @model_validator(mode="after")
    def validate_authority(self) -> Self:
        """Require a policy revision and bounded scope for allowed results."""
        if self.status == "allowed":
            if self.policy_revision is None or not self.scope:
                raise PydanticCustomError("persistent_protocol", "allowed authority requires policy revision and scope")
        elif self.scope or self.reason is None:
            raise PydanticCustomError("persistent_protocol", "denied/unknown requires a reason and no granted scope")
        return self

    def assert_execution(self, execution: ExecutionContext) -> None:
        """Ensure the returned authority does not expand startup scope."""
        self.assert_matches(execution)
        if not self.scope.issubset(execution.startup_scope):
            raise build_error(StatusCode.DEEPAGENT_CONTEXT_PARAM_ERROR, error_msg="authority expands startup scope")


class ControlCommand(InvocationRef):
    """One stop, cancel, approval, or supplement command for an execution."""

    control_id: Identifier
    kind: Literal["stop", "cancel", "approve", "supplement"]
    approval_ref: Identifier | None = None
    text: Text | None = None
    reason: Reason | None = None

    @model_validator(mode="after")
    def validate_control(self) -> Self:
        """Bind approval references and supplement text only to matching kinds."""
        if (self.kind == "approve") != (self.approval_ref is not None):
            raise PydanticCustomError(
                "persistent_protocol", "approve requires an approval reference; other controls cannot carry it"
            )
        if (self.kind == "supplement") != (self.text is not None):
            raise PydanticCustomError("persistent_protocol", "supplement requires text; other controls cannot carry it")
        return self


class ControlResult(InvocationRef):
    """Complete, empty, or uncertain poll result for execution controls."""

    status: Literal["available", "empty", "unknown"]
    commands: Annotated[tuple[ControlCommand, ...], Field(max_length=64)] = ()
    query_complete: bool = Field(default=False, strict=True)
    watermark: Nonnegative | None = None
    reason: Reason | None = None

    @model_validator(mode="after")
    def validate_controls(self) -> Self:
        """Require complete empty results, unique commands, and matching targets."""
        if self.status == "available" and not self.commands:
            raise PydanticCustomError("persistent_protocol", "available control response needs commands")
        if self.status != "available" and self.commands:
            raise PydanticCustomError("persistent_protocol", "empty/unknown cannot carry commands")
        if self.status == "empty" and (not self.query_complete or self.watermark is None):
            raise PydanticCustomError("persistent_protocol", "empty requires complete query and watermark")
        if self.status == "unknown" and self.reason is None:
            raise PydanticCustomError("persistent_protocol", "unknown control response requires a reason")
        if self.query_complete != (self.watermark is not None):
            raise PydanticCustomError("persistent_protocol", "control completeness and watermark must be paired")
        if len({command.control_id for command in self.commands}) != len(self.commands):
            raise PydanticCustomError("persistent_protocol", "duplicate control ids")
        for command in self.commands:
            if any(getattr(command, name) != getattr(self, name) for name in InvocationRef.model_fields):
                raise PydanticCustomError("persistent_protocol", "control target invocation mismatch")
        return self


class BudgetAdmission(InvocationRef):
    """Admission decision and reservation reference for one model call."""

    model_call_id: Identifier
    status: Literal["admitted", "denied", "unknown"]
    reservation_ref: Identifier | None = None
    reason: Reason | None = None

    @model_validator(mode="after")
    def validate_admission(self) -> Self:
        """Require reservation evidence only for an admitted model call."""
        if self.status == "admitted":
            if self.reservation_ref is None:
                raise PydanticCustomError("persistent_protocol", "admitted model call requires a reservation")
        elif self.reservation_ref is not None or self.reason is None:
            raise PydanticCustomError(
                "persistent_protocol", "denied/unknown admission needs a reason and no reservation"
            )
        return self

    def assert_call(self, execution: ExecutionContext, model_call_id: str) -> None:
        """Ensure this admission belongs to the requested execution and call."""
        self.assert_matches(execution)
        if self.model_call_id != model_call_id:
            raise build_error(StatusCode.DEEPAGENT_CONTEXT_PARAM_ERROR, error_msg="budget model call mismatch")


class BudgetReport(InvocationRef):
    """Host receipt for reporting actual model usage."""

    model_call_id: Identifier
    status: Literal["confirmed", "rejected", "unknown"]
    receipt_ref: Identifier | None = None
    reason: Reason | None = None

    @model_validator(mode="after")
    def validate_report(self) -> Self:
        """Require a receipt for confirmed usage and reasons otherwise."""
        if self.status == "confirmed":
            if self.receipt_ref is None:
                raise PydanticCustomError("persistent_protocol", "confirmed usage report requires receipt")
        elif self.receipt_ref is not None or self.reason is None:
            raise PydanticCustomError("persistent_protocol", "rejected/unknown report needs a reason and no receipt")
        return self

    def assert_call(self, execution: ExecutionContext, model_call_id: str) -> None:
        """Ensure this report belongs to the requested execution and call."""
        self.assert_matches(execution)
        if self.model_call_id != model_call_id:
            raise build_error(StatusCode.DEEPAGENT_CONTEXT_PARAM_ERROR, error_msg="usage model call mismatch")
