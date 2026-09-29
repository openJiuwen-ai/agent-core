# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Immutable protocol primitives; validation never grants execution authority."""

from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator
from pydantic_core import PydanticCustomError

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error

from .execution import ExecutionContext

Identifier = Annotated[str, StringConstraints(min_length=1, max_length=512, pattern=r"^[^\s\x00-\x1f\x7f]+$")]
Text = Annotated[str, StringConstraints(min_length=1, max_length=65536, pattern=r"\S")]
Reason = Annotated[str, StringConstraints(min_length=1, max_length=4096, pattern=r"\S")]
Digest = Annotated[str, StringConstraints(pattern=r"^[a-f0-9]{64}$")]
Positive = Annotated[int, Field(gt=0, strict=True)]
Nonnegative = Annotated[int, Field(ge=0, strict=True)]


class ProtocolModel(BaseModel):
    """Frozen strict base for host-facing Persistent protocol values."""

    model_config = ConfigDict(frozen=True, extra="forbid", validate_default=True, revalidate_instances="always")


class InvocationRef(ProtocolModel):
    """Stable identity tuple shared by every request and host response."""

    namespace_id: Identifier
    agent_id: Identifier
    session_id: Identifier
    request_id: Identifier
    execution_id: Identifier
    source_ref: Identifier
    epoch: Positive

    @classmethod
    def from_execution(cls, execution: ExecutionContext) -> "InvocationRef":
        """Copy protocol identity fields from the host-supplied execution."""
        if not isinstance(execution, ExecutionContext):
            raise build_error(StatusCode.DEEPAGENT_CONTEXT_PARAM_ERROR, error_msg="ExecutionContext required")
        return cls(**{name: getattr(execution, name) for name in InvocationRef.model_fields})

    def assert_matches(self, execution: ExecutionContext) -> None:
        """Reject protocol data associated with another invocation or epoch."""
        expected = InvocationRef.from_execution(execution)
        if any(getattr(self, name) != getattr(expected, name) for name in InvocationRef.model_fields):
            raise build_error(StatusCode.DEEPAGENT_CONTEXT_PARAM_ERROR, error_msg="protocol invocation/epoch mismatch")


class ModelUsage(ProtocolModel):
    """Unknown usage has absent counters, never synthetic zero counters."""

    status: Literal["known", "unknown"]
    input_tokens: Nonnegative | None = None
    output_tokens: Nonnegative | None = None
    reason: Reason | None = None

    @model_validator(mode="after")
    def validate_usage(self) -> Self:
        """Keep unknown usage distinct from a measured zero-token call."""
        if self.status == "known":
            if self.input_tokens is None or self.output_tokens is None:
                raise PydanticCustomError("persistent_protocol", "known usage requires both counters")
        elif self.input_tokens is not None or self.output_tokens is not None or self.reason is None:
            raise PydanticCustomError("persistent_protocol", "unknown usage requires a reason and absent counters")
        return self
