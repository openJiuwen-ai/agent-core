# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Group-message and knowledge-material contracts; no storage or retrieval implementation."""

from datetime import timezone
from typing import Annotated, Literal, Self

from pydantic import AwareDatetime, Field, field_validator, model_validator
from pydantic_core import PydanticCustomError

from .protocol import Digest, Identifier, InvocationRef, Nonnegative, ProtocolModel, Reason


class GroupMessage(ProtocolModel):
    """One group message as authorized for this Session; recalled or unavailable messages carry no body."""

    message_id: Identifier
    sender_id: Identifier
    sender_name: Annotated[str, Field(min_length=1, max_length=512)]
    sender_kind: Literal["human", "agent"]
    received_at: AwareDatetime
    text: Annotated[str, Field(max_length=65536)] | None = None
    mention_ids: Annotated[tuple[Identifier, ...], Field(max_length=128)] = ()
    reply_to: Identifier | None = None
    attachment_refs: Annotated[tuple[Identifier, ...], Field(max_length=128)] = ()
    # "unavailable": the message exists but its body cannot be read now; unlike "recalled", it may return.
    status: Literal["normal", "edited", "recalled", "unavailable"] = "normal"
    # Background only: a queued request must never become the current task's instruction.
    queued_request: bool = Field(default=False, strict=True)

    @field_validator("received_at")
    @classmethod
    def normalize_time(cls, value):
        """Normalize server receive time to UTC."""
        return value.astimezone(timezone.utc)

    @model_validator(mode="after")
    def validate_recall(self) -> Self:
        """Never expose the body of a recalled message, nor invent one for an unavailable message."""
        if self.status in ("recalled", "unavailable") and self.text is not None:
            raise PydanticCustomError("persistent_context", f"{self.status} message cannot carry text")
        return self


class GroupMessageQuery(ProtocolModel):
    """Bounded search over the Session's group history."""

    query: Annotated[str, Field(min_length=1, max_length=4096)] | None = None
    since: AwareDatetime | None = None
    until: AwareDatetime | None = None
    sender_id: Identifier | None = None
    cursor: Identifier | None = None
    limit: Annotated[int, Field(strict=True, gt=0, le=500)] = 50

    @model_validator(mode="after")
    def validate_query(self) -> Self:
        """Require at least one criterion and an ordered time range."""
        if self.query is None and self.since is None and self.until is None and self.sender_id is None:
            raise PydanticCustomError("persistent_context", "group search requires a query, time range, or sender")
        if self.since is not None and self.until is not None and self.since > self.until:
            raise PydanticCustomError("persistent_context", "group search time range is reversed")
        return self


class GroupMessagesResult(InvocationRef):
    """Found, confirmed absent, or incomplete; incomplete never means "no messages"."""

    status: Literal["found", "empty", "incomplete"]
    messages: Annotated[tuple[GroupMessage, ...], Field(max_length=500)] = ()
    # Paging position for the next read; not a statement about how far the host scanned.
    next_cursor: Identifier | None = None
    # Message position the host confirms it scanned through, including messages whose body is unavailable.
    # None means no trusted progress: a reader keeps its previous cursor.
    scanned_through: Identifier | None = None
    reason: Reason | None = None

    @model_validator(mode="after")
    def validate_messages(self) -> Self:
        """Keep confirmed absence distinct from an unfinished or failed lookup."""
        if self.status == "found" and not self.messages:
            raise PydanticCustomError("persistent_context", "found group result requires messages")
        if self.status == "empty" and (self.messages or self.next_cursor is not None):
            raise PydanticCustomError("persistent_context", "empty group result must be a complete absence")
        if self.status == "incomplete" and self.reason is None:
            raise PydanticCustomError("persistent_context", "incomplete group result requires a reason")
        return self


class MaterialQuery(ProtocolModel):
    """Bounded query for authorized knowledge material."""

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
