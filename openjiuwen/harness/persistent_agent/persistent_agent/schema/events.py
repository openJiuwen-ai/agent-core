# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Execution proposals, not chat records, durable commits, or delivery receipts."""

import hashlib
import json
from datetime import timezone
from enum import StrEnum
from typing import Annotated, Literal, Self

from pydantic import AwareDatetime, Field, ValidationInfo, field_validator, model_validator
from pydantic_core import PydanticCustomError

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error

from .execution import ExecutionContext
from .protocol import Digest, Identifier, InvocationRef, ModelUsage, Positive, ProtocolModel, Reason, Text

References = Annotated[tuple[Identifier, ...], Field(max_length=128)]


class EventType(StrEnum):
    """Stable kinds of private execution proposals and candidate outputs."""

    PARTICIPATION_DECIDED = "participation.decided"
    EXECUTION_STARTED = "execution.started"
    PROGRESS_UPDATED = "progress.updated"
    REPLY_DELTA = "reply.delta"
    MODEL_COMPLETED = "model.completed"
    TOOL_INTENT = "tool.intent"
    TOOL_COMPLETED = "tool.completed"
    WINDOW_SWITCHED = "window.switched"
    OUTPUT_PROPOSED = "output.proposed"
    EXECUTION_PAUSED = "execution.paused"
    EXECUTION_FINAL = "execution.final"


class ParticipationPayload(ProtocolModel):
    """Reasoned decision about whether to respond to an incoming message."""

    decision: Literal["ignore", "respond", "intervene"]
    reason: Reason
    evidence_refs: References = ()
    model_call_id: Identifier | None = None


class StartedPayload(ProtocolModel):
    """Goal that begins an execution; direct tasks have no participation decision."""

    goal: Text
    decision_event_id: Identifier | None = None


class TextPayload(ProtocolModel):
    """Plain text used for progress updates."""

    text: Text


class ReplyDeltaPayload(ProtocolModel):
    """Nonempty stream fragments preserve whitespace, unlike complete prose."""

    text: Annotated[str, Field(min_length=1, max_length=65536)]


class OutputPayload(ProtocolModel):
    """Candidate content or controlled file reference for host handoff."""

    output_id: Identifier
    target_ref: Identifier
    share_mode: Literal["message", "attachment", "upload", "link"]
    text: Text | None = None
    file_ref: Identifier | None = None
    dependency_refs: References = ()

    @model_validator(mode="after")
    def validate_output(self) -> Self:
        """Ensure each sharing mode carries the appropriate output reference."""
        if self.share_mode == "message":
            if self.text is None or self.file_ref is not None:
                raise PydanticCustomError("persistent_protocol", "message output needs text, not a file reference")
        elif self.file_ref is None:
            raise PydanticCustomError("persistent_protocol", "file/link output requires a controlled reference")
        return self


class ModelCompletedPayload(ProtocolModel):
    """Completed model-call identity, configuration, and usage proposal."""

    model_call_id: Identifier
    config_version: Identifier
    usage: ModelUsage


class ToolIntentPayload(ProtocolModel):
    """Write intent recorded before execution; arguments appear only as a redacted summary."""

    operation_id: Identifier
    tool_call_id: Identifier
    tool_name: Identifier
    target_ref: Identifier | None = None
    params_digest: Digest
    args_summary: Text


class ToolCompletedPayload(ProtocolModel):
    """Outcome of any tool call; only writes carry the operation_id of a prior intent."""

    tool_call_id: Identifier
    tool_name: Identifier
    args_summary: Text
    status: Literal["succeeded", "failed", "unknown"]
    result_summary: Text
    result_refs: References = ()
    operation_id: Identifier | None = None
    reason: Reason | None = None

    @model_validator(mode="after")
    def validate_result(self) -> Self:
        """Require a reason for failed/unknown outcomes in addition to their summary."""
        if self.status != "succeeded" and self.reason is None:
            raise PydanticCustomError("persistent_protocol", "failed/unknown tool result requires a reason")
        return self


class WindowSwitchedPayload(ProtocolModel):
    """Archive of the replaced window and the sequence number of the new one."""

    archive_ref: Identifier
    window_seq: Positive


class InterceptedToolCall(ProtocolModel):
    """One tool call awaiting the initiator's confirmation."""

    tool_call_id: Identifier
    tool_name: Identifier
    # Readable arguments for the confirmation card, with sensitive values redacted.
    arguments: Text


class PausedPayload(ProtocolModel):
    """Why an Execution ended without a final outcome; the Request can still continue."""

    reason: Literal["awaiting_confirmation", "stopped", "budget_denied", "context_unfit", "error"]
    intercepted: Annotated[tuple[InterceptedToolCall, ...], Field(max_length=64)] = ()
    detail: Reason | None = None

    @model_validator(mode="after")
    def validate_pause(self) -> Self:
        """Only confirmation pauses carry tool calls; condition pauses must explain themselves."""
        if (self.reason == "awaiting_confirmation") != bool(self.intercepted):
            raise PydanticCustomError(
                "persistent_protocol", "awaiting_confirmation requires intercepted calls; other pauses carry none"
            )
        if len({call.tool_call_id for call in self.intercepted}) != len(self.intercepted):
            raise PydanticCustomError("persistent_protocol", "duplicate intercepted tool_call_id")
        if self.reason in ("budget_denied", "context_unfit", "error") and self.detail is None:
            raise PydanticCustomError("persistent_protocol", f"{self.reason} pause requires a detail")
        return self


class FinalPayload(ProtocolModel):
    """Proposed terminal outcome with references to outputs and artifacts."""

    status: Literal["completed", "ignored", "cancelled", "failed"]
    output_refs: References = ()
    artifact_refs: References = ()
    reason: Reason | None = None

    @model_validator(mode="after")
    def validate_final(self) -> Self:
        """Require an explanation whenever execution did not complete normally."""
        if self.status != "completed" and self.reason is None:
            raise PydanticCustomError("persistent_protocol", "non-completed final requires a reason")
        return self


Payload = (
    ParticipationPayload
    | StartedPayload
    | TextPayload
    | ReplyDeltaPayload
    | OutputPayload
    | ModelCompletedPayload
    | ToolIntentPayload
    | ToolCompletedPayload
    | WindowSwitchedPayload
    | PausedPayload
    | FinalPayload
)

_PAYLOAD_TYPES = {
    EventType.PARTICIPATION_DECIDED: ParticipationPayload,
    EventType.EXECUTION_STARTED: StartedPayload,
    EventType.PROGRESS_UPDATED: TextPayload,
    EventType.REPLY_DELTA: ReplyDeltaPayload,
    EventType.MODEL_COMPLETED: ModelCompletedPayload,
    EventType.TOOL_INTENT: ToolIntentPayload,
    EventType.TOOL_COMPLETED: ToolCompletedPayload,
    EventType.WINDOW_SWITCHED: WindowSwitchedPayload,
    EventType.OUTPUT_PROPOSED: OutputPayload,
    EventType.EXECUTION_PAUSED: PausedPayload,
    EventType.EXECUTION_FINAL: FinalPayload,
}


class AgentEvent(InvocationRef):
    """Bounded immutable event proposal; construction does not persist it."""

    schema_version: Annotated[int, Field(ge=1, le=1, strict=True)] = 1
    event_id: str = ""
    event_type: EventType
    local_seq: Positive
    occurred_at: AwareDatetime
    visibility: Literal["private", "progress", "candidate"] = "private"
    correlation_refs: References = ()
    payload: Payload

    @field_validator("occurred_at")
    @classmethod
    def normalize_time(cls, value):
        """Normalize event timestamps to UTC for canonical identity."""
        return value.astimezone(timezone.utc)

    @field_validator("payload", mode="before")
    @classmethod
    def select_payload(cls, value, info: ValidationInfo):
        """Parse each payload using the schema registered for its event kind."""
        kind = info.data.get("event_type")
        if kind not in _PAYLOAD_TYPES:
            raise PydanticCustomError("persistent_protocol", "valid event_type required before payload")
        return _PAYLOAD_TYPES[kind].model_validate(value)

    @model_validator(mode="after")
    def validate_envelope(self) -> Self:
        """Derive stable event identity and enforce the proposal size limit."""
        identity = {name: getattr(self, name) for name in InvocationRef.model_fields}
        identity["local_seq"] = self.local_seq
        stable_id = (
            "evt_" + hashlib.sha256(json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        )
        if self.event_id and self.event_id != stable_id:
            raise PydanticCustomError("persistent_protocol", "event_id does not match invocation and local_seq")
        object.__setattr__(self, "event_id", stable_id)
        try:
            size = len(self._canonical().encode("utf-8"))
        except UnicodeError as exc:
            raise PydanticCustomError("persistent_protocol", "event content must be valid UTF-8") from exc
        if size > 262144:
            raise PydanticCustomError(
                "persistent_protocol", "event proposal exceeds 256 KiB; use controlled references"
            )
        return self

    @classmethod
    def create(cls, execution: ExecutionContext, **fields) -> "AgentEvent":
        """Make one proposal; execution adapter, not this DTO, allocates local_seq."""
        return cls(**InvocationRef.from_execution(execution).model_dump(), **fields)

    def _canonical(self) -> str:
        """Return deterministic JSON used for identity and digest checks."""
        return json.dumps(self.model_dump(mode="json"), sort_keys=True, separators=(",", ":"), ensure_ascii=False)

    def proposal_digest(self) -> str:
        """Hash the canonical proposal for safe retransmission comparison."""
        return hashlib.sha256(self._canonical().encode("utf-8")).hexdigest()

    def assert_same_proposal(self, other: "AgentEvent") -> None:
        """Pure retransmission check, not a durable deduplication store."""
        if (
            not isinstance(other, AgentEvent)
            or self.event_id != other.event_id
            or self.proposal_digest() != other.proposal_digest()
        ):
            raise build_error(StatusCode.DEEPAGENT_INPUT_PARAM_ERROR, error_msg="event proposal conflict")
