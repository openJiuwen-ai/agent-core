# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Typed window and versioned-material contracts; no storage implementation."""

from typing import Annotated, Literal, Self

from pydantic import Field, model_validator
from pydantic_core import PydanticCustomError

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error

from .protocol import Digest, Identifier, InvocationRef, Nonnegative, Positive, ProtocolModel, Reason


class WindowHead(ProtocolModel):
    """Authoritative active-window pointer returned by the host store."""

    active_window_ref: Identifier
    active_window_revision: Positive
    head_revision: Nonnegative
    authority_epoch: Positive


class WindowRecord(ProtocolModel):
    """Immutable, addressable version of one private context window."""

    window_ref: Identifier
    window_revision: Positive
    predecessor_ref: Identifier | None = None
    content_ref: Identifier
    content_digest: Digest
    source_refs: Annotated[tuple[Identifier, ...], Field(max_length=128)] = ()
    event_refs: Annotated[tuple[Identifier, ...], Field(max_length=128)] = ()


class ContextHeadResult(InvocationRef):
    """Outcome of resolving the authoritative active-window pointer."""

    status: Literal["found", "empty", "unknown", "unavailable"]
    head: WindowHead | None = None
    query_complete: bool = Field(default=False, strict=True)
    watermark: Nonnegative | None = None
    reason: Reason | None = None

    @model_validator(mode="after")
    def validate_head_result(self) -> Self:
        """Keep empty, unknown, and found head outcomes unambiguous."""
        if self.status == "found":
            if self.head is None or self.head.authority_epoch != self.epoch:
                raise PydanticCustomError("persistent_context", "found head must match the execution epoch")
        elif self.head is not None:
            raise PydanticCustomError("persistent_context", "non-found head result cannot carry a head")
        if self.status == "empty" and (not self.query_complete or self.watermark is None):
            raise PydanticCustomError("persistent_context", "empty head requires a complete query and watermark")
        if self.status in ("unknown", "unavailable") and self.reason is None:
            raise PydanticCustomError("persistent_context", "unknown/unavailable head requires a reason")
        if self.query_complete != (self.watermark is not None):
            raise PydanticCustomError("persistent_context", "head completeness and watermark must be paired")
        return self


class WindowReadResult(InvocationRef):
    """Outcome of reading one exact immutable window version."""

    status: Literal["found", "not_found", "forbidden", "unavailable", "unknown"]
    window_ref: Identifier
    window_revision: Positive
    record: WindowRecord | None = None
    query_complete: bool = Field(default=False, strict=True)
    watermark: Nonnegative | None = None
    reason: Reason | None = None

    @model_validator(mode="after")
    def validate_window_read(self) -> Self:
        """Require returned content and completeness to match the requested version."""
        if self.status == "found":
            if (
                self.record is None
                or self.record.window_ref != self.window_ref
                or self.record.window_revision != self.window_revision
            ):
                raise PydanticCustomError("persistent_context", "window result must match the requested version")
        elif self.record is not None:
            raise PydanticCustomError("persistent_context", "non-found window result cannot carry a record")
        if self.status == "not_found" and (not self.query_complete or self.watermark is None):
            raise PydanticCustomError("persistent_context", "not_found requires a complete query and watermark")
        if self.status in ("forbidden", "unavailable", "unknown") and self.reason is None:
            raise PydanticCustomError("persistent_context", "failed window read requires a reason")
        if self.query_complete != (self.watermark is not None):
            raise PydanticCustomError("persistent_context", "window query completeness and watermark must be paired")
        return self


class WindowWrite(ProtocolModel):
    """Idempotent request to persist a versioned private window."""

    operation_id: Identifier
    record: WindowRecord


class HeadAdvance(ProtocolModel):
    """Conditional request to move the active pointer to a saved window."""

    operation_id: Identifier
    expected_head_revision: Nonnegative
    authority_epoch: Positive
    window_ref: Identifier
    window_revision: Positive


class WindowSwitch(ProtocolModel):
    """Atomic request to create and activate a new private window."""

    switch_id: Identifier
    expected_head_revision: Nonnegative
    authority_epoch: Positive
    new_window: WindowRecord


class WindowMutationResult(InvocationRef):
    """Host receipt or uncertainty result for a window mutation operation."""

    operation_id: Identifier
    status: Literal["applied", "conflict", "rejected", "unknown", "not_found"]
    receipt_ref: Identifier | None = None
    head: WindowHead | None = None
    query_complete: bool = Field(default=False, strict=True)
    watermark: Nonnegative | None = None
    reason: Reason | None = None

    @model_validator(mode="after")
    def validate_mutation(self) -> Self:
        """Validate receipt, epoch, reason, and query-completeness invariants."""
        if self.status == "applied" and self.receipt_ref is None:
            raise PydanticCustomError("persistent_context", "applied window mutation requires a receipt")
        if self.head is not None and self.head.authority_epoch != self.epoch:
            raise PydanticCustomError("persistent_context", "window head receipt epoch mismatch")
        if self.status in ("conflict", "rejected", "unknown") and self.reason is None:
            raise PydanticCustomError("persistent_context", "failed window mutation requires a reason")
        if self.status == "not_found" and (not self.query_complete or self.watermark is None):
            raise PydanticCustomError("persistent_context", "not_found requires a complete query and watermark")
        if self.query_complete != (self.watermark is not None):
            raise PydanticCustomError("persistent_context", "mutation completeness and watermark must be paired")
        return self

    def assert_operation(self, execution, operation_id: str, *, query: bool = False) -> None:
        """Match a mutation response to its invocation and requested operation."""
        self.assert_matches(execution)
        if self.operation_id != operation_id:
            raise build_error(StatusCode.DEEPAGENT_INPUT_PARAM_ERROR, error_msg="window operation receipt mismatch")
        if self.status == "not_found" and not query:
            raise build_error(
                StatusCode.DEEPAGENT_INPUT_PARAM_ERROR, error_msg="not_found is a window operation query result only"
            )


class MaterialQuery(ProtocolModel):
    """Bounded query for authorized history, knowledge, or group material."""

    query: Annotated[str, Field(min_length=1, max_length=4096)]
    source_ref: Identifier | None = None
    scope_ref: Identifier | None = None
    cursor: Identifier | None = None
    limit: Annotated[int, Field(strict=True, gt=0, le=500)] = 50


class MaterialSnapshot(ProtocolModel):
    """Versioned source evidence; it is a reference, not an authorization grant."""

    material_ref: Identifier
    source_ref: Identifier
    scope_ref: Identifier
    version_ref: Identifier
    content_ref: Identifier
    content_digest: Digest
    policy_revision: Identifier
    completeness: Literal["complete", "partial"]
    source_cursor: Identifier | None = None


class MaterialReadResult(InvocationRef):
    """Search/read outcome with explicit source versions and completeness."""

    status: Literal["found", "empty", "forbidden", "partial", "unavailable", "unknown"]
    items: Annotated[tuple[MaterialSnapshot, ...], Field(max_length=500)] = ()
    next_cursor: Identifier | None = None
    query_complete: bool = Field(default=False, strict=True)
    watermark: Nonnegative | None = None
    reason: Reason | None = None

    @model_validator(mode="after")
    def validate_material_result(self) -> Self:
        """Prevent incomplete or unavailable results from appearing empty."""
        if self.status == "found" and not self.items:
            raise PydanticCustomError("persistent_context", "found material result requires items")
        if self.status == "empty" and (self.items or not self.query_complete or self.watermark is None):
            raise PydanticCustomError("persistent_context", "empty material result requires complete absence")
        if (
            self.status == "partial"
            and self.next_cursor is None
            and not any(item.completeness == "partial" for item in self.items)
        ):
            raise PydanticCustomError("persistent_context", "partial material result needs an incomplete reference")
        if self.status in ("forbidden", "unavailable", "unknown") and (self.items or self.reason is None):
            raise PydanticCustomError("persistent_context", "failed material result requires a reason and no items")
        if self.query_complete != (self.watermark is not None):
            raise PydanticCustomError("persistent_context", "material completeness and watermark must be paired")
        return self
