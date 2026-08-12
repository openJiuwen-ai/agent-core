# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Internal projection helpers for OpenAI-compatible evaluation messages."""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from typing import Protocol

from pydantic import JsonValue

from openjiuwen.symphony.models._base import JsonObject


class _FingerprintIdentity(Protocol):
    capability_id: str
    name: str


@dataclass(frozen=True, slots=True)
class MessageTraceCall:
    """One assistant tool call paired with its optional tool response."""

    tool_call_id: str
    function_name: str
    arguments: str
    assistant_message_index: int
    tool_message_index: int | None = None
    output: JsonValue = None

    @property
    def inputs(self) -> JsonValue:
        """Decode JSON arguments when possible while preserving opaque strings."""

        try:
            return json.loads(self.arguments)
        except (json.JSONDecodeError, TypeError):
            return self.arguments


def project_message_calls(message: tuple[JsonObject, ...]) -> tuple[MessageTraceCall, ...]:
    """Project assistant tool calls and their later tool responses in trace order."""

    calls: list[MessageTraceCall] = []
    call_positions: dict[str, int] = {}

    for message_index, item in enumerate(message):
        role = item.get("role")
        if role == "assistant":
            raw_tool_calls = item.get("tool_calls")
            if not isinstance(raw_tool_calls, list):
                continue
            for raw_call in raw_tool_calls:
                if not isinstance(raw_call, dict):
                    continue
                function = raw_call.get("function")
                if not isinstance(function, dict):
                    continue
                tool_call_id = raw_call.get("id")
                function_name = function.get("name")
                arguments = function.get("arguments")
                if not all(isinstance(value, str) for value in (tool_call_id, function_name, arguments)):
                    continue
                call_positions[tool_call_id] = len(calls)
                calls.append(
                    MessageTraceCall(
                        tool_call_id=tool_call_id,
                        function_name=function_name,
                        arguments=arguments,
                        assistant_message_index=message_index,
                    )
                )
            continue

        if role == "tool":
            tool_call_id = item.get("tool_call_id")
            if not isinstance(tool_call_id, str):
                continue
            call_position = call_positions.get(tool_call_id)
            if call_position is None:
                continue
            calls[call_position] = replace(
                calls[call_position],
                tool_message_index=message_index,
                output=item.get("content"),
            )

    return tuple(calls)


def message_has_user_input(message: tuple[JsonObject, ...]) -> bool:
    """Return whether the trace contains meaningful user content."""

    return any(item.get("role") == "user" and _has_content(item.get("content")) for item in message)


def message_has_assistant_or_tool_evidence(message: tuple[JsonObject, ...]) -> bool:
    """Return whether the trace contains meaningful assistant or tool evidence."""

    for item in message:
        role = item.get("role")
        if role == "assistant":
            if _has_content(item.get("content")):
                return True
            raw_tool_calls = item.get("tool_calls")
            if isinstance(raw_tool_calls, list) and raw_tool_calls:
                return True
        elif role == "tool" and _has_content(item.get("content")):
            return True
    return False


def call_matches_fingerprint(call: MessageTraceCall, fingerprint: _FingerprintIdentity) -> bool:
    """Match an indirect capability reference by the tool function name."""

    function_name = call.function_name.strip().casefold()
    identities = {
        fingerprint.capability_id.strip().casefold(),
        fingerprint.name.strip().casefold(),
    }
    return function_name in identities


def matching_message_calls(
    message: tuple[JsonObject, ...],
    fingerprint: _FingerprintIdentity,
) -> tuple[MessageTraceCall, ...]:
    """Return projected tool calls that reference a fingerprint by ID or name."""

    return tuple(call for call in project_message_calls(message) if call_matches_fingerprint(call, fingerprint))


def message_references_fingerprint(message: tuple[JsonObject, ...], fingerprint: _FingerprintIdentity) -> bool:
    """Return whether any projected tool call references the fingerprint."""

    return any(call_matches_fingerprint(call, fingerprint) for call in project_message_calls(message))


def _has_content(value: JsonValue) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, dict)):
        return bool(value)
    return True
