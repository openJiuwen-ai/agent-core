# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Task input and interactive reply values accepted by the execution entries."""

from datetime import timezone
from typing import Annotated, Literal, Self

from pydantic import AwareDatetime, Field, field_validator, model_validator
from pydantic_core import PydanticCustomError

from .context import GroupMessage
from .protocol import Identifier, ProtocolModel, Text


class DirectMessageSource(ProtocolModel):
    """Source facts of the private-chat message that created the request."""

    message_id: Identifier
    sender_id: Identifier
    sender_name: Annotated[str, Field(min_length=1, max_length=512)]
    received_at: AwareDatetime

    @field_validator("received_at")
    @classmethod
    def normalize_time(cls, value):
        """Normalize server receive time to UTC."""
        return value.astimezone(timezone.utc)


class RequestInput(ProtocolModel):
    """Original task input; continuation reuses it with an optional instruction."""

    kind: Literal["group_message", "direct_message", "scheduled"]
    text: Text
    source: GroupMessage | DirectMessageSource | None = None
    reply_target: Identifier
    attachment_refs: Annotated[tuple[Identifier, ...], Field(max_length=128)] = ()
    # Allowed only when the ExecutionContext carries a recovery_ref; the entry checks that.
    continue_instruction: Text | None = None

    @model_validator(mode="after")
    def validate_source(self) -> Self:
        """Group messages keep the stored message; schedules have no trigger message."""
        expected = {"group_message": GroupMessage, "direct_message": DirectMessageSource}.get(self.kind)
        if expected is None:
            if self.source is not None:
                raise PydanticCustomError("persistent_request", "scheduled request has no trigger message")
        elif not isinstance(self.source, expected):
            raise PydanticCustomError("persistent_request", f"{self.kind} requires a {expected.__name__} source")
        return self

    @property
    def trigger_message_id(self) -> str | None:
        """ID used for source correlation, deduplication and excluding the trigger from background."""
        return None if self.source is None else self.source.message_id


class PermissionResponse(ProtocolModel):
    """Initiator decision on one intercepted tool call; there is no permanent allow."""

    kind: Literal["permission"] = "permission"
    tool_call_id: Identifier
    choice: Literal["approve", "approve_for_session", "reject"]
    feedback: Annotated[str, Field(max_length=4096)] | None = None


class AnswerResponse(ProtocolModel):
    """Initiator answers to one ask-user call, keyed by the original question text."""

    kind: Literal["answer"] = "answer"
    tool_call_id: Identifier
    outcome: Literal["answered", "timed_out"] = "answered"
    answers: Annotated[dict[Annotated[str, Field(min_length=1, max_length=4096)], str], Field(max_length=64)] = Field(
        default_factory=dict
    )

    @model_validator(mode="after")
    def validate_answers(self) -> Self:
        """A timeout carries no answers; an answered reply carries at least one."""
        if self.outcome == "timed_out" and self.answers:
            raise PydanticCustomError("persistent_request", "timed_out reply cannot carry answers")
        if self.outcome == "answered" and not self.answers:
            raise PydanticCustomError("persistent_request", "answered reply requires at least one answer")
        return self


class ConfirmReply(ProtocolModel):
    """Replies to the calls a paused Execution is waiting for."""

    responses: Annotated[
        tuple[Annotated[PermissionResponse | AnswerResponse, Field(discriminator="kind")], ...],
        Field(min_length=1, max_length=64),
    ]

    @model_validator(mode="after")
    def validate_unique_calls(self) -> Self:
        """Each waiting call is answered at most once per reply."""
        ids = [response.tool_call_id for response in self.responses]
        if len(set(ids)) != len(ids):
            raise PydanticCustomError("persistent_request", "duplicate tool_call_id in reply")
        return self
