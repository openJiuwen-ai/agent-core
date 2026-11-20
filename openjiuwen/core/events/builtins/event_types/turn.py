# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Public turn lifecycle event contracts."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, cast

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error, raise_error
from openjiuwen.core.events.event_next import EventNext
from openjiuwen.core.events.ops import EventHandlerOps
from openjiuwen.core.events.types import Event, MatchPattern
from openjiuwen.core.foundation.llm import AssistantMessage, ToolCall, UsageMetadata

if TYPE_CHECKING:
    from openjiuwen.core.events.bus import ExtensionEventBus

TURN_START = "turn.start"
TURN_STEP = "turn.step"
TURN_COMPLETE = "turn.complete"
_TURN_REASONS = ("completed", "aborted", "error")
_UNSET = object()
TurnStepAnswer = str | list[str | dict[str, object]]


@dataclass(frozen=True, slots=True)
class TurnStartInput:
    """Public input emitted after the host creates a turn."""

    turn_id: str
    text: str


@dataclass(frozen=True, slots=True)
class TurnStartOutput:
    """Public output preserving the host-created turn identity."""

    turn_id: str


@dataclass(frozen=True, slots=True)
class TurnCompleteInput:
    """Public terminal turn result exposed to extension handlers."""

    turn_id: str
    reason: Literal["completed", "aborted", "error"]
    answer: str
    usage: Mapping[str, object] | None = None


@dataclass(frozen=True, slots=True)
class TurnCompleteOutput:
    """Public terminal text and usage returned by extension handlers."""

    text: str
    usage: Mapping[str, object] | None = None


@dataclass(frozen=True, slots=True)
class TurnStepInput:
    """Read-only identity and model metadata for one complete model step."""

    turn_id: str
    index: int
    model: str
    agent_id: str | None = None


@dataclass(frozen=True, slots=True)
class TurnStepToolUse:
    """Provider-neutral tool use exposed by a completed model step."""

    tool_call_id: str | None
    tool_name: str
    arguments: object


@dataclass(frozen=True, slots=True)
class TurnStepOutput:
    """Public model result fields that extension handlers may rewrite."""

    turn_id: str
    index: int
    answer: TurnStepAnswer
    tool_uses: tuple[TurnStepToolUse, ...]
    stop_reason: str | None
    usage: Mapping[str, object] | None = None


@dataclass(slots=True)
class TurnStepTriggerContext:
    """Private executor and raw result for one ``turn.step`` trigger."""

    execute_step: Callable[[TurnStepInput], Awaitable[object]]
    original_result: object = _UNSET


def match_pattern_func(event_input: object, match_pattern: MatchPattern) -> bool:
    """Match every valid turn event with an empty pattern."""
    del event_input
    return not match_pattern


def _is_valid_start_input(value: object) -> bool:
    return (
        isinstance(value, TurnStartInput)
        and isinstance(value.turn_id, str)
        and isinstance(value.text, str)
    )


def _is_valid_start_output(value: object) -> bool:
    return isinstance(value, TurnStartOutput) and isinstance(value.turn_id, str)


def _is_valid_complete_input(value: object) -> bool:
    return (
        isinstance(value, TurnCompleteInput)
        and isinstance(value.turn_id, str)
        and value.reason in _TURN_REASONS
        and isinstance(value.answer, str)
        and (value.usage is None or isinstance(value.usage, Mapping))
    )


def _is_valid_complete_output(value: object) -> bool:
    return (
        isinstance(value, TurnCompleteOutput)
        and isinstance(value.text, str)
        and (value.usage is None or isinstance(value.usage, Mapping))
    )


def _is_valid_step_answer(value: object) -> bool:
    return isinstance(value, str) or (
        isinstance(value, list)
        and all(isinstance(item, (str, dict)) for item in value)
    )


def _is_valid_step_input(value: object) -> bool:
    return (
        isinstance(value, TurnStepInput)
        and isinstance(value.turn_id, str)
        and isinstance(value.index, int)
        and not isinstance(value.index, bool)
        and value.index >= 0
        and isinstance(value.model, str)
        and (value.agent_id is None or isinstance(value.agent_id, str))
    )


def _is_valid_step_output(value: object) -> bool:
    return (
        isinstance(value, TurnStepOutput)
        and isinstance(value.turn_id, str)
        and isinstance(value.index, int)
        and not isinstance(value.index, bool)
        and value.index >= 0
        and _is_valid_step_answer(value.answer)
        and isinstance(value.tool_uses, tuple)
        and all(
            isinstance(item, TurnStepToolUse)
            and (item.tool_call_id is None or isinstance(item.tool_call_id, str))
            and isinstance(item.tool_name, str)
            for item in value.tool_uses
        )
        and (value.stop_reason is None or isinstance(value.stop_reason, str))
        and (value.usage is None or isinstance(value.usage, Mapping))
    )


def _project_usage(usage: UsageMetadata | None) -> Mapping[str, object] | None:
    if usage is None:
        return None
    return usage.model_dump(exclude_defaults=True)


def project_turn_step_output(
    message: AssistantMessage,
    *,
    turn_id: str,
    index: int,
) -> TurnStepOutput:
    """Project provider-neutral public fields from an assistant message."""
    return TurnStepOutput(
        turn_id=turn_id,
        index=index,
        answer=message.content,
        tool_uses=tuple(
            TurnStepToolUse(
                tool_call_id=tool_call.id,
                tool_name=tool_call.name,
                arguments=tool_call.arguments,
            )
            for tool_call in (message.tool_calls or ())
        ),
        stop_reason=message.finish_reason,
        usage=_project_usage(message.usage_metadata),
    )


def validate_turn_step_identity(output: object, turn_id: str, index: int) -> TurnStepOutput:
    """Reject outputs that rewrite the host-owned turn or step identity."""
    if (
        not isinstance(output, TurnStepOutput)
        or output.turn_id != turn_id
        or output.index != index
    ):
        raise_error(
            StatusCode.EVENTS_PAYLOAD_INVALID,
            reason="turn.step output cannot rewrite turn_id or index",
        )
    return output


def turn_step_core_executed(context: TurnStepTriggerContext) -> bool:
    """Return whether CORE invoked the model-step executor."""
    return context.original_result is not _UNSET


def _serialize_tool_arguments(arguments: object) -> str:
    if isinstance(arguments, str):
        return arguments
    try:
        return json.dumps(arguments, ensure_ascii=False)
    except (TypeError, ValueError) as error:
        raise build_error(
            StatusCode.EVENTS_PAYLOAD_INVALID,
            reason="turn.step tool arguments must be JSON serializable",
            cause=error,
        ) from error


def _build_usage(usage: Mapping[str, object] | None) -> UsageMetadata | None:
    if usage is None:
        return None
    try:
        return UsageMetadata.model_validate(dict(usage))
    except (TypeError, ValueError) as error:
        raise build_error(
            StatusCode.EVENTS_PAYLOAD_INVALID,
            reason="turn.step usage is invalid",
            cause=error,
        ) from error


def _build_tool_calls(
    tool_uses: tuple[TurnStepToolUse, ...],
    originals: list[ToolCall] | None = None,
) -> list[ToolCall]:
    original_items = originals or []
    used_indexes: set[int] = set()
    result: list[ToolCall] = []
    for item in tool_uses:
        matched_index = None
        for candidate_index, original in enumerate(original_items):
            if candidate_index in used_indexes:
                continue
            if (original.id, original.name) != (item.tool_call_id, item.tool_name):
                continue
            matched_index = candidate_index
            break
        arguments = _serialize_tool_arguments(item.arguments)
        if matched_index is None:
            result.append(
                ToolCall(
                    id=item.tool_call_id,
                    type="function",
                    name=item.tool_name,
                    arguments=arguments,
                )
            )
            continue
        used_indexes.add(matched_index)
        original = original_items[matched_index]
        result.append(
            original
            if arguments == original.arguments
            else original.model_copy(update={"arguments": arguments})
        )
    return result


def build_turn_step_message(
    output: TurnStepOutput,
    *,
    turn_id: str,
    index: int,
) -> AssistantMessage:
    """Build an assistant message when an extension short-circuits CORE."""
    validate_turn_step_identity(output, turn_id, index)
    return AssistantMessage(
        content=output.answer,
        tool_calls=_build_tool_calls(output.tool_uses),
        finish_reason=output.stop_reason or "null",
        usage_metadata=_build_usage(output.usage),
    )


def apply_turn_step_output(
    original: AssistantMessage,
    output: TurnStepOutput,
    *,
    turn_id: str,
    index: int,
) -> AssistantMessage:
    """Apply only rewritten public fields while preserving private provider data."""
    validate_turn_step_identity(output, turn_id, index)
    projected = project_turn_step_output(original, turn_id=turn_id, index=index)
    if output == projected:
        return original

    updates: dict[str, object] = {}
    if output.answer != projected.answer:
        updates["content"] = output.answer
    if output.tool_uses != projected.tool_uses:
        updates["tool_calls"] = _build_tool_calls(output.tool_uses, original.tool_calls)
    if output.stop_reason != projected.stop_reason:
        updates["finish_reason"] = output.stop_reason or "null"
    if output.usage != projected.usage:
        updates["usage_metadata"] = _build_usage(output.usage)
    return original.model_copy(update=updates)


async def turn_start_core_handler(
    event: Event,
    ops: EventHandlerOps | None,
    event_next: EventNext,
) -> TurnStartOutput:
    """Return the host-created turn identity unchanged."""
    del ops, event_next
    event_input = cast(TurnStartInput, event.event_input)
    return TurnStartOutput(turn_id=event_input.turn_id)


async def turn_complete_core_handler(
    event: Event,
    ops: EventHandlerOps | None,
    event_next: EventNext,
) -> TurnCompleteOutput:
    """Return the terminal answer and usage unchanged."""
    del ops, event_next
    event_input = cast(TurnCompleteInput, event.event_input)
    return TurnCompleteOutput(text=event_input.answer, usage=event_input.usage)


async def turn_step_core_handler(
    event: Event,
    ops: EventHandlerOps | None,
    event_next: EventNext,
) -> TurnStepOutput:
    """Execute one complete model step and expose its provider-neutral result."""
    del event_next
    context = getattr(ops, "trigger_context", None)
    if not isinstance(context, TurnStepTriggerContext):
        raise_error(
            StatusCode.EVENTS_STATE_INVALID,
            reason="turn.step requires TurnStepTriggerContext",
        )
    event_input = cast(TurnStepInput, event.event_input)
    result = await context.execute_step(event_input)
    context.original_result = result
    if not isinstance(result, AssistantMessage):
        return TurnStepOutput(
            turn_id=event_input.turn_id,
            index=event_input.index,
            answer="",
            tool_uses=(),
            stop_reason=None,
        )
    return project_turn_step_output(
        result,
        turn_id=event_input.turn_id,
        index=event_input.index,
    )


def register(bus: ExtensionEventBus) -> None:
    """Register turn events and their default CORE handlers."""
    bus.register_event_type(
        TURN_START,
        match_pattern_func,
        turn_start_core_handler,
        input_validator=_is_valid_start_input,
        output_validator=_is_valid_start_output,
    )
    bus.register_event_type(
        TURN_STEP,
        match_pattern_func,
        turn_step_core_handler,
        input_validator=_is_valid_step_input,
        output_validator=_is_valid_step_output,
    )
    bus.register_event_type(
        TURN_COMPLETE,
        match_pattern_func,
        turn_complete_core_handler,
        input_validator=_is_valid_complete_input,
        output_validator=_is_valid_complete_output,
    )
