# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Ordered provider-neutral observations emitted by an external harness."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum
from typing import TypeAlias

from openjiuwen.agent_teams.external.protocol.models import (
    JsonObject,
    JsonValue,
    freeze_json_object,
    freeze_json_value,
)
from openjiuwen.agent_teams.external.protocol.results import TurnResult, TurnStatus, TurnUsage
from openjiuwen.agent_teams.harness.state import HarnessState


class TurnEventKind(str, Enum):
    """Lifecycle transition for one external-input-driven turn."""

    STARTED = "started"
    PAUSED = "paused"
    RESUMED = "resumed"
    FINISHED = "finished"
    ABORTED = "aborted"
    FAILED = "failed"


TERMINAL_TURN_EVENT_KINDS = frozenset(
    {
        TurnEventKind.FINISHED,
        TurnEventKind.ABORTED,
        TurnEventKind.FAILED,
    }
)


class OutputKind(str, Enum):
    """Portable representation of output content."""

    TEXT = "text"
    STRUCTURED = "structured"


class OutputChannel(str, Enum):
    """Semantic channel carrying an output block."""

    ANSWER = "answer"
    REASONING = "reasoning"
    SYSTEM = "system"


class OutputOperation(str, Enum):
    """How an output update changes the identified content block."""

    DELTA = "delta"
    SNAPSHOT = "snapshot"
    FINAL = "final"


class UsageUpdateMode(str, Enum):
    """Whether a usage update is incremental or cumulative."""

    DELTA = "delta"
    CUMULATIVE = "cumulative"


class EventRetention(str, Enum):
    """Backpressure retention class derived from an event payload."""

    REQUIRED = "required"
    COALESCIBLE = "coalescible"
    BEST_EFFORT = "best_effort"


class EventOverflowPolicy(str, Enum):
    """Allowed behavior when the bounded observation buffer is full."""

    BLOCK = "block"
    COALESCE_OR_BLOCK = "coalesce_or_block"
    DROP_BEST_EFFORT_OR_BLOCK = "drop_best_effort_or_block"


@dataclass(frozen=True, slots=True)
class EventBufferConfig:
    """Bounded observation-buffer contract advertised by an implementation."""

    capacity: int = 1024
    overflow: EventOverflowPolicy = EventOverflowPolicy.COALESCE_OR_BLOCK

    def __post_init__(self) -> None:
        if self.capacity <= 0:
            raise ValueError("event buffer capacity must be positive")


class ItemEventKind(str, Enum):
    """Lifecycle transition for a provider item such as a tool call."""

    STARTED = "started"
    UPDATED = "updated"
    COMPLETED = "completed"


class HookEventPhase(str, Enum):
    """Observable phase of a hook invocation."""

    STARTED = "started"
    FINISHED = "finished"


class DiagnosticLevel(str, Enum):
    """Severity of a diagnostic event."""

    DEBUG = "debug"
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class OutputEvent:
    """Provider-neutral update for one stable output content block."""

    output_id: str
    kind: OutputKind
    content: JsonValue
    operation: OutputOperation = OutputOperation.SNAPSHOT
    channel: OutputChannel = OutputChannel.ANSWER
    content_index: int = 0
    content_type: str | None = None
    data: JsonObject = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.output_id:
            raise ValueError("output_id must not be empty")
        if self.content_index < 0:
            raise ValueError("output content_index must be non-negative")
        object.__setattr__(self, "content", freeze_json_value(self.content))
        object.__setattr__(self, "data", freeze_json_object(self.data))


@dataclass(frozen=True, slots=True)
class ItemLifecycleEvent:
    """Lifecycle update for a provider item without losing its native shape."""

    kind: ItemEventKind
    item_type: str
    data: JsonObject = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.item_type:
            raise ValueError("item_type must not be empty")
        object.__setattr__(self, "data", freeze_json_object(self.data))


@dataclass(frozen=True, slots=True)
class UsageUpdatedEvent:
    """Cumulative or incremental normalized usage information."""

    usage: TurnUsage
    mode: UsageUpdateMode = UsageUpdateMode.CUMULATIVE


@dataclass(frozen=True, slots=True)
class StateChangedEvent:
    """A high-level harness state transition."""

    old: HarnessState
    new: HarnessState


@dataclass(frozen=True, slots=True)
class TurnLifecycleEvent:
    """A lifecycle transition of one turn."""

    kind: TurnEventKind
    result: TurnResult | None = None

    def __post_init__(self) -> None:
        if self.kind not in TERMINAL_TURN_EVENT_KINDS:
            if self.result is not None:
                raise ValueError(f"non-terminal {self.kind.value} turn event must not contain a result")
            return

        if self.result is None:
            raise ValueError("terminal turn event requires a result")

        expected_status = {
            TurnEventKind.FINISHED: TurnStatus.COMPLETED,
            TurnEventKind.ABORTED: TurnStatus.INTERRUPTED,
            TurnEventKind.FAILED: TurnStatus.FAILED,
        }[self.kind]
        if self.result.status is not expected_status:
            raise ValueError(f"{self.kind.value} turn event requires {expected_status.value} result")


@dataclass(frozen=True, slots=True)
class HookObservedEvent:
    """An observational mirror of hook execution, never a control response."""

    phase: HookEventPhase
    hook_name: str
    hook_id: str
    data: JsonObject = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.hook_name or not self.hook_id:
            raise ValueError("hook observation name and id must not be empty")
        object.__setattr__(self, "data", freeze_json_object(self.data))


@dataclass(frozen=True, slots=True)
class DiagnosticEvent:
    """A non-output diagnostic emitted by the harness implementation."""

    level: DiagnosticLevel
    message: str
    data: JsonObject = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.message:
            raise ValueError("diagnostic message must not be empty")
        object.__setattr__(self, "data", freeze_json_object(self.data))


@dataclass(frozen=True, slots=True)
class ProviderEvent:
    """Namespaced provider extension event preserved without core changes."""

    provider: str
    event_type: str
    schema_version: str
    payload: JsonObject = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.provider or not self.event_type or not self.schema_version:
            raise ValueError("provider event namespace, type, and schema version must not be empty")
        object.__setattr__(self, "payload", freeze_json_object(self.payload))


@dataclass(frozen=True, slots=True)
class UnknownEvent:
    """Lossless shared event from a newer schema unknown to this consumer."""

    event_type: str
    schema_version: str
    payload: JsonObject = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.event_type or not self.schema_version:
            raise ValueError("unknown event type and schema version must not be empty")
        object.__setattr__(self, "payload", freeze_json_object(self.payload))


HarnessEventPayload: TypeAlias = (
    OutputEvent
    | ItemLifecycleEvent
    | UsageUpdatedEvent
    | StateChangedEvent
    | TurnLifecycleEvent
    | HookObservedEvent
    | DiagnosticEvent
    | ProviderEvent
    | UnknownEvent
)


def event_retention(event: HarnessEventPayload) -> EventRetention:
    """Return the only retention class a producer may use for ``event``."""

    if isinstance(event, OutputEvent) and event.operation is OutputOperation.SNAPSHOT:
        return EventRetention.COALESCIBLE
    if isinstance(event, UsageUpdatedEvent) and event.mode is UsageUpdateMode.CUMULATIVE:
        return EventRetention.COALESCIBLE
    if isinstance(event, HookObservedEvent):
        return EventRetention.BEST_EFFORT
    if isinstance(event, DiagnosticEvent) and event.level in {DiagnosticLevel.DEBUG, DiagnosticLevel.INFO}:
        return EventRetention.BEST_EFFORT
    return EventRetention.REQUIRED


@dataclass(frozen=True, slots=True)
class HarnessEvent:
    """Ordered event envelope carrying shared correlation metadata."""

    sequence: int
    timestamp: float
    event: HarnessEventPayload
    team_session_id: str
    member_agent_id: str
    session_id: str | None = None
    turn_id: str | None = None
    item_id: str | None = None
    correlation_id: str | None = None
    causation_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.sequence < 0:
            raise ValueError("event sequence must be non-negative")
        if not math.isfinite(self.timestamp) or self.timestamp < 0:
            raise ValueError("event timestamp must be a finite Unix timestamp")
        if not self.team_session_id or not self.member_agent_id:
            raise ValueError("event team_session_id and member_agent_id must not be empty")
        optional_ids = (self.session_id, self.turn_id, self.item_id, self.correlation_id)
        if any(identifier == "" for identifier in optional_ids):
            raise ValueError("optional event IDs must not be empty strings")
        causation_ids = tuple(self.causation_ids)
        if any(not identifier for identifier in causation_ids):
            raise ValueError("event causation_ids must not contain empty values")
        if len(set(causation_ids)) != len(causation_ids):
            raise ValueError("event causation_ids must not contain duplicates")
        object.__setattr__(self, "causation_ids", causation_ids)
        if isinstance(self.event, TurnLifecycleEvent) and self.turn_id is None:
            raise ValueError("turn lifecycle event requires turn_id")


__all__ = [
    "DiagnosticEvent",
    "DiagnosticLevel",
    "EventBufferConfig",
    "EventOverflowPolicy",
    "EventRetention",
    "HarnessEvent",
    "HarnessEventPayload",
    "HookEventPhase",
    "HookObservedEvent",
    "ItemEventKind",
    "ItemLifecycleEvent",
    "OutputEvent",
    "OutputChannel",
    "OutputKind",
    "OutputOperation",
    "ProviderEvent",
    "StateChangedEvent",
    "TERMINAL_TURN_EVENT_KINDS",
    "TurnEventKind",
    "TurnLifecycleEvent",
    "UnknownEvent",
    "UsageUpdateMode",
    "UsageUpdatedEvent",
    "event_retention",
]
