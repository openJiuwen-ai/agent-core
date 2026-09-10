# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Provider-neutral terminal results emitted by external harness turns."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum

from openjiuwen.agent_teams.external.protocol.models import (
    JsonObject,
    JsonValue,
    freeze_json_object,
    freeze_json_value,
)


class TurnStatus(str, Enum):
    """Terminal status of one external-input-driven turn."""

    COMPLETED = "completed"
    INTERRUPTED = "interrupted"
    FAILED = "failed"


class MessageRole(str, Enum):
    """Portable role of a message retained in a terminal turn result."""

    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"
    TOOL = "tool"


class TurnTerminationKind(str, Enum):
    """Reason a turn ended without reaching a normal completion."""

    USER_ABORT = "user_abort"
    TIMEOUT = "timeout"
    POLICY = "policy"
    PROVIDER = "provider"
    HARNESS_STOP = "harness_stop"


@dataclass(frozen=True, slots=True)
class ContentBlock:
    """One ordered, provider-neutral message content block."""

    block_id: str
    kind: str
    content: JsonValue
    data: JsonObject = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.block_id or not self.kind:
            raise ValueError("content block id and kind must not be empty")
        object.__setattr__(self, "content", freeze_json_value(self.content))
        object.__setattr__(self, "data", freeze_json_object(self.data))


@dataclass(frozen=True, slots=True)
class TurnMessage:
    """One normalized message retained by the terminal turn result."""

    message_id: str
    role: MessageRole
    content: tuple[ContentBlock, ...]
    data: JsonObject = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.message_id:
            raise ValueError("turn message_id must not be empty")
        object.__setattr__(self, "content", tuple(self.content))
        object.__setattr__(self, "data", freeze_json_object(self.data))


@dataclass(frozen=True, slots=True)
class TurnTermination:
    """Structured cause for an interrupted turn."""

    kind: TurnTerminationKind
    message: str | None = None
    code: str | None = None
    provider_data: JsonObject = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "provider_data", freeze_json_object(self.provider_data))


@dataclass(frozen=True, slots=True)
class MonetaryAmount:
    """Exact monetary amount represented in one-millionth currency units."""

    micros: int
    currency: str = "USD"

    def __post_init__(self) -> None:
        if self.micros < 0:
            raise ValueError("monetary micros must be non-negative")
        currency = self.currency.upper()
        if len(currency) != 3 or not currency.isalpha():
            raise ValueError("monetary currency must be a three-letter code")
        object.__setattr__(self, "currency", currency)


@dataclass(frozen=True, slots=True)
class TurnUsage:
    """Normalized token usage with room for provider-specific counters."""

    input_tokens: int | None = None
    output_tokens: int | None = None
    cached_input_tokens: int | None = None
    reasoning_output_tokens: int | None = None
    total_tokens: int | None = None
    provider_data: JsonObject = field(default_factory=dict)

    def __post_init__(self) -> None:
        counters = (
            self.input_tokens,
            self.output_tokens,
            self.cached_input_tokens,
            self.reasoning_output_tokens,
            self.total_tokens,
        )
        if any(counter is not None and counter < 0 for counter in counters):
            raise ValueError("turn usage counters must be non-negative")
        object.__setattr__(self, "provider_data", freeze_json_object(self.provider_data))


@dataclass(frozen=True, slots=True)
class TurnError:
    """Normalized failure information for a turn."""

    message: str
    code: str | None = None
    category: str | None = None
    retryable: bool | None = None
    provider_data: JsonObject = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.message:
            raise ValueError("turn error message must not be empty")
        object.__setattr__(self, "provider_data", freeze_json_object(self.provider_data))


@dataclass(frozen=True, slots=True)
class TurnResult:
    """Complete provider-neutral terminal result for one turn.

    ``messages`` is the lossless normalized output. ``final_output`` and
    ``structured_output`` are convenience projections for simple consumers.
    """

    status: TurnStatus
    messages: tuple[TurnMessage, ...] = ()
    final_output: JsonValue = None
    structured_output: JsonValue = None
    stop_reason: str | None = None
    termination: TurnTermination | None = None
    error: TurnError | None = None
    usage: TurnUsage | None = None
    cost: MonetaryAmount | None = None
    started_at: float | None = None
    completed_at: float | None = None
    duration_ms: int | None = None
    provider_data: JsonObject = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "messages", tuple(self.messages))
        object.__setattr__(self, "final_output", freeze_json_value(self.final_output))
        object.__setattr__(self, "structured_output", freeze_json_value(self.structured_output))
        object.__setattr__(self, "provider_data", freeze_json_object(self.provider_data))
        if self.status is TurnStatus.FAILED and self.error is None:
            raise ValueError("failed turn result requires error")
        if self.status is not TurnStatus.FAILED and self.error is not None:
            raise ValueError("only failed turn result may contain error")
        if self.status is TurnStatus.INTERRUPTED and self.termination is None:
            raise ValueError("interrupted turn result requires termination")
        if self.status is not TurnStatus.INTERRUPTED and self.termination is not None:
            raise ValueError("only interrupted turn result may contain termination")
        for field_name, timestamp in (("started_at", self.started_at), ("completed_at", self.completed_at)):
            if timestamp is not None and (not math.isfinite(timestamp) or timestamp < 0):
                raise ValueError(f"turn {field_name} must be a finite Unix timestamp")
        if self.started_at is not None and self.completed_at is not None and self.completed_at < self.started_at:
            raise ValueError("turn completed_at must not precede started_at")
        if self.duration_ms is not None and self.duration_ms < 0:
            raise ValueError("turn duration_ms must be non-negative monotonic elapsed time")


__all__ = [
    "ContentBlock",
    "MessageRole",
    "MonetaryAmount",
    "TurnError",
    "TurnMessage",
    "TurnResult",
    "TurnStatus",
    "TurnTermination",
    "TurnTerminationKind",
    "TurnUsage",
]
