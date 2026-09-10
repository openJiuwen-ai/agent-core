# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Stable JSON codec for external harness observation events."""

from __future__ import annotations

from collections.abc import Mapping
from typing import cast

from openjiuwen.agent_teams.external.protocol.errors import ExternalHarnessProtocolError
from openjiuwen.agent_teams.external.protocol.events import (
    DiagnosticEvent,
    DiagnosticLevel,
    HarnessEvent,
    HarnessEventPayload,
    HookEventPhase,
    HookObservedEvent,
    ItemEventKind,
    ItemLifecycleEvent,
    OutputChannel,
    OutputEvent,
    OutputKind,
    OutputOperation,
    ProviderEvent,
    StateChangedEvent,
    TurnEventKind,
    TurnLifecycleEvent,
    UnknownEvent,
    UsageUpdatedEvent,
    UsageUpdateMode,
)
from openjiuwen.agent_teams.external.protocol.models import JsonObject, JsonValue, json_value_to_builtin
from openjiuwen.agent_teams.external.protocol.results import (
    ContentBlock,
    MessageRole,
    MonetaryAmount,
    TurnError,
    TurnMessage,
    TurnResult,
    TurnStatus,
    TurnTermination,
    TurnTerminationKind,
    TurnUsage,
)
from openjiuwen.agent_teams.harness.state import HarnessState

EVENT_WIRE_SCHEMA_VERSION = "1"


def _json(value: JsonValue) -> object:
    return json_value_to_builtin(value)


def _usage_to_dict(usage: TurnUsage) -> dict[str, object]:
    return {
        "input_tokens": usage.input_tokens,
        "output_tokens": usage.output_tokens,
        "cached_input_tokens": usage.cached_input_tokens,
        "reasoning_output_tokens": usage.reasoning_output_tokens,
        "total_tokens": usage.total_tokens,
        "provider_data": _json(usage.provider_data),
    }


def _usage_from_dict(data: Mapping[str, object]) -> TurnUsage:
    return TurnUsage(
        input_tokens=cast(int | None, data.get("input_tokens")),
        output_tokens=cast(int | None, data.get("output_tokens")),
        cached_input_tokens=cast(int | None, data.get("cached_input_tokens")),
        reasoning_output_tokens=cast(int | None, data.get("reasoning_output_tokens")),
        total_tokens=cast(int | None, data.get("total_tokens")),
        provider_data=_json_object(data.get("provider_data", {}), "usage.provider_data"),
    )


def _block_to_dict(block: ContentBlock) -> dict[str, object]:
    return {
        "block_id": block.block_id,
        "kind": block.kind,
        "content": _json(block.content),
        "data": _json(block.data),
    }


def _block_from_dict(data: Mapping[str, object]) -> ContentBlock:
    return ContentBlock(
        block_id=_string(data, "block_id"),
        kind=_string(data, "kind"),
        content=cast(JsonValue, data.get("content")),
        data=_json_object(data.get("data", {}), "content_block.data"),
    )


def _message_to_dict(message: TurnMessage) -> dict[str, object]:
    return {
        "message_id": message.message_id,
        "role": message.role.value,
        "content": [_block_to_dict(block) for block in message.content],
        "data": _json(message.data),
    }


def _message_from_dict(data: Mapping[str, object]) -> TurnMessage:
    content = _list(data.get("content", []), "turn_message.content")
    return TurnMessage(
        message_id=_string(data, "message_id"),
        role=_enum(MessageRole, data.get("role"), "turn_message.role"),
        content=tuple(_block_from_dict(_mapping(block, "content_block")) for block in content),
        data=_json_object(data.get("data", {}), "turn_message.data"),
    )


def _result_to_dict(result: TurnResult) -> dict[str, object]:
    error = None
    if result.error is not None:
        error = {
            "message": result.error.message,
            "code": result.error.code,
            "category": result.error.category,
            "retryable": result.error.retryable,
            "provider_data": _json(result.error.provider_data),
        }
    termination = None
    if result.termination is not None:
        termination = {
            "kind": result.termination.kind.value,
            "message": result.termination.message,
            "code": result.termination.code,
            "provider_data": _json(result.termination.provider_data),
        }
    cost = None
    if result.cost is not None:
        cost = {"micros": result.cost.micros, "currency": result.cost.currency}
    return {
        "status": result.status.value,
        "messages": [_message_to_dict(message) for message in result.messages],
        "final_output": _json(result.final_output),
        "structured_output": _json(result.structured_output),
        "stop_reason": result.stop_reason,
        "termination": termination,
        "error": error,
        "usage": _usage_to_dict(result.usage) if result.usage is not None else None,
        "cost": cost,
        "started_at": result.started_at,
        "completed_at": result.completed_at,
        "duration_ms": result.duration_ms,
        "provider_data": _json(result.provider_data),
    }


def _result_from_dict(data: Mapping[str, object]) -> TurnResult:
    error_data = data.get("error")
    error = None
    if error_data is not None:
        error_object = _mapping(error_data, "turn_result.error")
        error = TurnError(
            message=_string(error_object, "message"),
            code=_optional_string(error_object.get("code"), "turn_result.error.code"),
            category=_optional_string(error_object.get("category"), "turn_result.error.category"),
            retryable=cast(bool | None, error_object.get("retryable")),
            provider_data=_json_object(error_object.get("provider_data", {}), "turn_result.error.provider_data"),
        )
    termination_data = data.get("termination")
    termination = None
    if termination_data is not None:
        termination_object = _mapping(termination_data, "turn_result.termination")
        termination = TurnTermination(
            kind=_enum(
                TurnTerminationKind,
                termination_object.get("kind"),
                "turn_result.termination.kind",
            ),
            message=_optional_string(termination_object.get("message"), "turn_result.termination.message"),
            code=_optional_string(termination_object.get("code"), "turn_result.termination.code"),
            provider_data=_json_object(
                termination_object.get("provider_data", {}),
                "turn_result.termination.provider_data",
            ),
        )
    cost_data = data.get("cost")
    cost = None
    if cost_data is not None:
        cost_object = _mapping(cost_data, "turn_result.cost")
        cost = MonetaryAmount(
            micros=_integer(cost_object, "micros"),
            currency=_string(cost_object, "currency"),
        )
    usage_data = data.get("usage")
    messages_data = _list(data.get("messages", []), "turn_result.messages")
    return TurnResult(
        status=_enum(TurnStatus, data.get("status"), "turn_result.status"),
        messages=tuple(_message_from_dict(_mapping(message, "turn_message")) for message in messages_data),
        final_output=cast(JsonValue, data.get("final_output")),
        structured_output=cast(JsonValue, data.get("structured_output")),
        stop_reason=_optional_string(data.get("stop_reason"), "turn_result.stop_reason"),
        termination=termination,
        error=error,
        usage=_usage_from_dict(_mapping(usage_data, "turn_result.usage")) if usage_data is not None else None,
        cost=cost,
        started_at=cast(float | None, data.get("started_at")),
        completed_at=cast(float | None, data.get("completed_at")),
        duration_ms=cast(int | None, data.get("duration_ms")),
        provider_data=_json_object(data.get("provider_data", {}), "turn_result.provider_data"),
    )


def _payload_to_wire(event: HarnessEventPayload) -> tuple[str, str, dict[str, object]]:
    if isinstance(event, OutputEvent):
        return (
            "output",
            EVENT_WIRE_SCHEMA_VERSION,
            {
                "output_id": event.output_id,
                "kind": event.kind.value,
                "content": _json(event.content),
                "operation": event.operation.value,
                "channel": event.channel.value,
                "content_index": event.content_index,
                "content_type": event.content_type,
                "data": _json(event.data),
            },
        )
    if isinstance(event, ItemLifecycleEvent):
        return (
            "item_lifecycle",
            EVENT_WIRE_SCHEMA_VERSION,
            {
                "kind": event.kind.value,
                "item_type": event.item_type,
                "data": _json(event.data),
            },
        )
    if isinstance(event, UsageUpdatedEvent):
        return (
            "usage_updated",
            EVENT_WIRE_SCHEMA_VERSION,
            {
                "usage": _usage_to_dict(event.usage),
                "mode": event.mode.value,
            },
        )
    if isinstance(event, StateChangedEvent):
        return "state_changed", EVENT_WIRE_SCHEMA_VERSION, {"old": event.old.value, "new": event.new.value}
    if isinstance(event, TurnLifecycleEvent):
        return (
            "turn_lifecycle",
            EVENT_WIRE_SCHEMA_VERSION,
            {
                "kind": event.kind.value,
                "result": _result_to_dict(event.result) if event.result is not None else None,
            },
        )
    if isinstance(event, HookObservedEvent):
        return (
            "hook_observed",
            EVENT_WIRE_SCHEMA_VERSION,
            {
                "phase": event.phase.value,
                "hook_name": event.hook_name,
                "hook_id": event.hook_id,
                "data": _json(event.data),
            },
        )
    if isinstance(event, DiagnosticEvent):
        return (
            "diagnostic",
            EVENT_WIRE_SCHEMA_VERSION,
            {
                "level": event.level.value,
                "message": event.message,
                "data": _json(event.data),
            },
        )
    if isinstance(event, ProviderEvent):
        return (
            "provider",
            EVENT_WIRE_SCHEMA_VERSION,
            {
                "provider": event.provider,
                "event_type": event.event_type,
                "schema_version": event.schema_version,
                "payload": _json(event.payload),
            },
        )
    if isinstance(event, UnknownEvent):
        return event.event_type, event.schema_version, cast(dict[str, object], _json(event.payload))
    raise ExternalHarnessProtocolError(f"unsupported event payload type: {type(event).__name__}")


def harness_event_to_dict(event: HarnessEvent) -> dict[str, object]:
    """Encode one event as a JSON-serializable, discriminator-bearing object."""

    event_type, schema_version, payload = _payload_to_wire(event.event)
    return {
        "schema_version": schema_version,
        "event_type": event_type,
        "sequence": event.sequence,
        "timestamp": event.timestamp,
        "team_session_id": event.team_session_id,
        "member_agent_id": event.member_agent_id,
        "session_id": event.session_id,
        "turn_id": event.turn_id,
        "item_id": event.item_id,
        "correlation_id": event.correlation_id,
        "causation_ids": list(event.causation_ids),
        "payload": payload,
    }


def _payload_from_wire(event_type: str, schema_version: str, data: Mapping[str, object]) -> HarnessEventPayload:
    if schema_version != EVENT_WIRE_SCHEMA_VERSION:
        return UnknownEvent(event_type=event_type, schema_version=schema_version, payload=cast(JsonObject, data))
    if event_type == "output":
        return OutputEvent(
            output_id=_string(data, "output_id"),
            kind=_enum(OutputKind, data.get("kind"), "output.kind"),
            content=cast(JsonValue, data.get("content")),
            operation=_enum(OutputOperation, data.get("operation"), "output.operation"),
            channel=_enum(OutputChannel, data.get("channel"), "output.channel"),
            content_index=_integer(data, "content_index"),
            content_type=_optional_string(data.get("content_type"), "output.content_type"),
            data=_json_object(data.get("data", {}), "output.data"),
        )
    if event_type == "item_lifecycle":
        return ItemLifecycleEvent(
            kind=_enum(ItemEventKind, data.get("kind"), "item.kind"),
            item_type=_string(data, "item_type"),
            data=_json_object(data.get("data", {}), "item.data"),
        )
    if event_type == "usage_updated":
        return UsageUpdatedEvent(
            usage=_usage_from_dict(_mapping(data.get("usage"), "usage_updated.usage")),
            mode=_enum(UsageUpdateMode, data.get("mode"), "usage_updated.mode"),
        )
    if event_type == "state_changed":
        return StateChangedEvent(
            old=_enum(HarnessState, data.get("old"), "state_changed.old"),
            new=_enum(HarnessState, data.get("new"), "state_changed.new"),
        )
    if event_type == "turn_lifecycle":
        result_data = data.get("result")
        return TurnLifecycleEvent(
            kind=_enum(TurnEventKind, data.get("kind"), "turn_lifecycle.kind"),
            result=_result_from_dict(_mapping(result_data, "turn_lifecycle.result"))
            if result_data is not None
            else None,
        )
    if event_type == "hook_observed":
        return HookObservedEvent(
            phase=_enum(HookEventPhase, data.get("phase"), "hook.phase"),
            hook_name=_string(data, "hook_name"),
            hook_id=_string(data, "hook_id"),
            data=_json_object(data.get("data", {}), "hook.data"),
        )
    if event_type == "diagnostic":
        return DiagnosticEvent(
            level=_enum(DiagnosticLevel, data.get("level"), "diagnostic.level"),
            message=_string(data, "message"),
            data=_json_object(data.get("data", {}), "diagnostic.data"),
        )
    if event_type == "provider":
        return ProviderEvent(
            provider=_string(data, "provider"),
            event_type=_string(data, "event_type"),
            schema_version=_string(data, "schema_version"),
            payload=_json_object(data.get("payload", {}), "provider.payload"),
        )
    return UnknownEvent(event_type=event_type, schema_version=schema_version, payload=cast(JsonObject, data))


def harness_event_from_dict(data: Mapping[str, object]) -> HarnessEvent:
    """Decode an event and preserve unknown shared event types without loss."""

    try:
        event_type = _string(data, "event_type")
        schema_version = _string(data, "schema_version")
        payload = _mapping(data.get("payload"), "payload")
        causation_values = _list(data.get("causation_ids", []), "causation_ids")
        causation_ids = tuple(_required_string(value, "causation_id") for value in causation_values)
        return HarnessEvent(
            sequence=_integer(data, "sequence"),
            timestamp=_number(data, "timestamp"),
            event=_payload_from_wire(event_type, schema_version, payload),
            team_session_id=_string(data, "team_session_id"),
            member_agent_id=_string(data, "member_agent_id"),
            session_id=_optional_string(data.get("session_id"), "session_id"),
            turn_id=_optional_string(data.get("turn_id"), "turn_id"),
            item_id=_optional_string(data.get("item_id"), "item_id"),
            correlation_id=_optional_string(data.get("correlation_id"), "correlation_id"),
            causation_ids=causation_ids,
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ExternalHarnessProtocolError(f"invalid harness event wire object: {exc}") from exc


def _mapping(value: object, field_name: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or any(not isinstance(key, str) for key in value):
        raise ExternalHarnessProtocolError(f"{field_name} must be a JSON object")
    return cast(Mapping[str, object], value)


def _json_object(value: object, field_name: str) -> JsonObject:
    return cast(JsonObject, _mapping(value, field_name))


def _list(value: object, field_name: str) -> list[object]:
    if not isinstance(value, list):
        raise ExternalHarnessProtocolError(f"{field_name} must be a JSON array")
    return value


def _string(data: Mapping[str, object], field_name: str) -> str:
    return _required_string(data[field_name], field_name)


def _required_string(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value:
        raise ExternalHarnessProtocolError(f"{field_name} must be a non-empty string")
    return value


def _optional_string(value: object, field_name: str) -> str | None:
    if value is None:
        return None
    return _required_string(value, field_name)


def _integer(data: Mapping[str, object], field_name: str) -> int:
    value = data[field_name]
    if isinstance(value, bool) or not isinstance(value, int):
        raise ExternalHarnessProtocolError(f"{field_name} must be an integer")
    return value


def _number(data: Mapping[str, object], field_name: str) -> float:
    value = data[field_name]
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ExternalHarnessProtocolError(f"{field_name} must be a number")
    return float(value)


def _enum(enum_type, value: object, field_name: str):
    if not isinstance(value, str):
        raise ExternalHarnessProtocolError(f"{field_name} must be a string enum value")
    try:
        return enum_type(value)
    except ValueError as exc:
        raise ExternalHarnessProtocolError(f"unknown {field_name}: {value!r}") from exc


__all__ = ["EVENT_WIRE_SCHEMA_VERSION", "harness_event_from_dict", "harness_event_to_dict"]
