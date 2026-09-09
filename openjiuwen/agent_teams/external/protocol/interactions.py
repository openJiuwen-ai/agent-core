# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Awaited host interactions requested by a third-party agent harness."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Protocol, TypeAlias, overload, runtime_checkable

from openjiuwen.agent_teams.external.protocol.errors import ExternalHarnessProtocolError
from openjiuwen.agent_teams.external.protocol.models import (
    JsonObject,
    JsonValue,
    freeze_json_object,
    freeze_json_value,
)


def _validate_request_common(request: object) -> None:
    request_id = getattr(request, "request_id")
    if not request_id:
        raise ValueError("interaction request_id must not be empty")
    for field_name in ("session_id", "turn_id"):
        if getattr(request, field_name) == "":
            raise ValueError(f"interaction {field_name} must not be empty")
    deadline_at = getattr(request, "deadline_at")
    if deadline_at is not None and (not math.isfinite(deadline_at) or deadline_at < 0):
        raise ValueError("interaction deadline_at must be a finite Unix timestamp")


def _validate_response_id(response: object) -> None:
    if not getattr(response, "request_id"):
        raise ValueError("interaction response request_id must not be empty")


class InteractionResponseStatus(str, Enum):
    """Completion status for an interaction that is not an approval."""

    COMPLETED = "completed"
    DECLINED = "declined"
    CANCELLED = "cancelled"
    FAILED = "failed"


class ToolApprovalDecision(str, Enum):
    """Host decision for a provider-requested tool execution."""

    ALLOW = "allow"
    ALLOW_FOR_SESSION = "allow_for_session"
    DENY = "deny"
    ABORT = "abort"


class InteractionCancelReason(str, Enum):
    """Reason a pending provider-to-host interaction was cancelled."""

    PROVIDER_WITHDREW = "provider_withdrew"
    TURN_ABORTED = "turn_aborted"
    HARNESS_STOPPED = "harness_stopped"
    DEADLINE_EXCEEDED = "deadline_exceeded"


@dataclass(frozen=True, slots=True)
class ToolApprovalRequest:
    """Request authorization before a provider executes a tool call."""

    request_id: str
    call_id: str
    tool_name: str
    arguments: JsonObject = field(default_factory=dict)
    session_id: str | None = None
    turn_id: str | None = None
    title: str | None = None
    description: str | None = None
    reason: str | None = None
    deadline_at: float | None = None
    provider_data: JsonObject = field(default_factory=dict)

    def __post_init__(self) -> None:
        _validate_request_common(self)
        if not self.call_id or not self.tool_name:
            raise ValueError("tool approval call_id and tool_name must not be empty")
        object.__setattr__(self, "arguments", freeze_json_object(self.arguments))
        object.__setattr__(self, "provider_data", freeze_json_object(self.provider_data))


@dataclass(frozen=True, slots=True)
class UserInputRequest:
    """Request additional input from the user while a turn is active."""

    request_id: str
    prompt: str
    session_id: str | None = None
    turn_id: str | None = None
    choices: tuple[str, ...] = ()
    deadline_at: float | None = None
    provider_data: JsonObject = field(default_factory=dict)

    def __post_init__(self) -> None:
        _validate_request_common(self)
        if not self.prompt:
            raise ValueError("user input prompt must not be empty")
        object.__setattr__(self, "choices", tuple(self.choices))
        object.__setattr__(self, "provider_data", freeze_json_object(self.provider_data))


@dataclass(frozen=True, slots=True)
class McpElicitationRequest:
    """Request structured user input for an MCP elicitation."""

    request_id: str
    server_name: str
    prompt: str
    schema: JsonObject | None = None
    session_id: str | None = None
    turn_id: str | None = None
    deadline_at: float | None = None
    provider_data: JsonObject = field(default_factory=dict)

    def __post_init__(self) -> None:
        _validate_request_common(self)
        if not self.server_name or not self.prompt:
            raise ValueError("MCP elicitation server_name and prompt must not be empty")
        if self.schema is not None:
            object.__setattr__(self, "schema", freeze_json_object(self.schema))
        object.__setattr__(self, "provider_data", freeze_json_object(self.provider_data))


@dataclass(frozen=True, slots=True)
class DynamicToolCallRequest:
    """Delegate a provider-originated dynamic tool call to the host."""

    request_id: str
    call_id: str
    tool_name: str
    arguments: JsonObject = field(default_factory=dict)
    namespace: str | None = None
    session_id: str | None = None
    turn_id: str | None = None
    deadline_at: float | None = None
    provider_data: JsonObject = field(default_factory=dict)

    def __post_init__(self) -> None:
        _validate_request_common(self)
        if not self.call_id or not self.tool_name:
            raise ValueError("dynamic tool call_id and tool_name must not be empty")
        object.__setattr__(self, "arguments", freeze_json_object(self.arguments))
        object.__setattr__(self, "provider_data", freeze_json_object(self.provider_data))


@dataclass(frozen=True, slots=True)
class ProviderInteractionRequest:
    """Provider extension request not covered by a shared interaction type."""

    request_id: str
    provider: str
    request_type: str
    schema_version: str
    payload: JsonObject = field(default_factory=dict)
    session_id: str | None = None
    turn_id: str | None = None
    deadline_at: float | None = None

    def __post_init__(self) -> None:
        _validate_request_common(self)
        if not self.provider or not self.request_type or not self.schema_version:
            raise ValueError("provider interaction namespace, type, and version must not be empty")
        object.__setattr__(self, "payload", freeze_json_object(self.payload))


@dataclass(frozen=True, slots=True)
class ToolApprovalResponse:
    """Decision returned for :class:`ToolApprovalRequest`."""

    request_id: str
    decision: ToolApprovalDecision
    updated_arguments: JsonObject | None = None
    reason: str | None = None
    interrupt: bool = False
    provider_data: JsonObject = field(default_factory=dict)

    def __post_init__(self) -> None:
        _validate_response_id(self)
        if self.updated_arguments is not None:
            object.__setattr__(self, "updated_arguments", freeze_json_object(self.updated_arguments))
        object.__setattr__(self, "provider_data", freeze_json_object(self.provider_data))


@dataclass(frozen=True, slots=True)
class UserInputResponse:
    """Response returned for :class:`UserInputRequest`."""

    request_id: str
    status: InteractionResponseStatus
    content: JsonValue = None
    provider_data: JsonObject = field(default_factory=dict)

    def __post_init__(self) -> None:
        _validate_response_id(self)
        object.__setattr__(self, "content", freeze_json_value(self.content))
        object.__setattr__(self, "provider_data", freeze_json_object(self.provider_data))


@dataclass(frozen=True, slots=True)
class McpElicitationResponse:
    """Response returned for :class:`McpElicitationRequest`."""

    request_id: str
    status: InteractionResponseStatus
    values: JsonObject = field(default_factory=dict)
    provider_data: JsonObject = field(default_factory=dict)

    def __post_init__(self) -> None:
        _validate_response_id(self)
        object.__setattr__(self, "values", freeze_json_object(self.values))
        object.__setattr__(self, "provider_data", freeze_json_object(self.provider_data))


@dataclass(frozen=True, slots=True)
class DynamicToolCallResponse:
    """Response returned for :class:`DynamicToolCallRequest`."""

    request_id: str
    status: InteractionResponseStatus
    result: JsonValue = None
    is_error: bool = False
    error_message: str | None = None
    provider_data: JsonObject = field(default_factory=dict)

    def __post_init__(self) -> None:
        _validate_response_id(self)
        object.__setattr__(self, "result", freeze_json_value(self.result))
        object.__setattr__(self, "provider_data", freeze_json_object(self.provider_data))


@dataclass(frozen=True, slots=True)
class ProviderInteractionResponse:
    """Response returned for :class:`ProviderInteractionRequest`."""

    request_id: str
    status: InteractionResponseStatus
    payload: JsonObject = field(default_factory=dict)

    def __post_init__(self) -> None:
        _validate_response_id(self)
        object.__setattr__(self, "payload", freeze_json_object(self.payload))


HarnessInteractionRequest: TypeAlias = (
    ToolApprovalRequest | UserInputRequest | McpElicitationRequest | DynamicToolCallRequest | ProviderInteractionRequest
)
HarnessInteractionResponse: TypeAlias = (
    ToolApprovalResponse
    | UserInputResponse
    | McpElicitationResponse
    | DynamicToolCallResponse
    | ProviderInteractionResponse
)


_EXPECTED_RESPONSE_TYPE = {
    ToolApprovalRequest: ToolApprovalResponse,
    UserInputRequest: UserInputResponse,
    McpElicitationRequest: McpElicitationResponse,
    DynamicToolCallRequest: DynamicToolCallResponse,
    ProviderInteractionRequest: ProviderInteractionResponse,
}


def validate_interaction_response(
    request: HarnessInteractionRequest,
    response: HarnessInteractionResponse,
) -> HarnessInteractionResponse:
    """Validate request identity and the response shape required by its type."""

    if response.request_id != request.request_id:
        raise ExternalHarnessProtocolError(
            f"interaction response id {response.request_id!r} does not match request {request.request_id!r}"
        )
    expected_type = _EXPECTED_RESPONSE_TYPE[type(request)]
    if not isinstance(response, expected_type):
        raise ExternalHarnessProtocolError(
            f"{type(request).__name__} requires {expected_type.__name__}, got {type(response).__name__}"
        )
    return response


@runtime_checkable
class HarnessInteractionHandler(Protocol):
    """Host service for request/response interactions during a live turn.

    Implementations must return the response type paired with the request and
    the same ``request_id`` before ``deadline_at`` when one is supplied.
    ``cancel`` is idempotent and releases any pending host UI or policy
    operation for the request. Harnesses must cancel every pending request
    before aborting its turn or completing ``stop``.
    """

    @overload
    async def handle(self, request: ToolApprovalRequest) -> ToolApprovalResponse:
        """Wait for and return the host tool approval decision."""
        ...

    @overload
    async def handle(self, request: UserInputRequest) -> UserInputResponse:
        """Wait for and return the host user input."""
        ...

    @overload
    async def handle(self, request: McpElicitationRequest) -> McpElicitationResponse:
        """Wait for and return the host MCP elicitation result."""
        ...

    @overload
    async def handle(self, request: DynamicToolCallRequest) -> DynamicToolCallResponse:
        """Wait for and return the host dynamic tool call result."""
        ...

    @overload
    async def handle(self, request: ProviderInteractionRequest) -> ProviderInteractionResponse:
        """Wait for and return the host provider-specific interaction result."""
        ...

    async def handle(self, request: HarnessInteractionRequest) -> HarnessInteractionResponse:
        """Wait for and return the host response to ``request``."""
        ...

    async def cancel(
        self,
        request_id: str,
        *,
        reason: InteractionCancelReason = InteractionCancelReason.PROVIDER_WITHDREW,
    ) -> None:
        """Cancel a pending interaction when the provider withdraws it."""
        ...


__all__ = [
    "DynamicToolCallRequest",
    "DynamicToolCallResponse",
    "HarnessInteractionHandler",
    "HarnessInteractionRequest",
    "HarnessInteractionResponse",
    "InteractionCancelReason",
    "InteractionResponseStatus",
    "McpElicitationRequest",
    "McpElicitationResponse",
    "ProviderInteractionRequest",
    "ProviderInteractionResponse",
    "ToolApprovalDecision",
    "ToolApprovalRequest",
    "ToolApprovalResponse",
    "UserInputRequest",
    "UserInputResponse",
    "validate_interaction_response",
]
