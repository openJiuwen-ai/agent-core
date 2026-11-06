# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""The ``tool.call`` event: one AgentLoop tool batch around ``AbilityManager.execute``."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error, raise_error
from openjiuwen.core.events.event_next import EventNext
from openjiuwen.core.events.ops import EventHandlerOps
from openjiuwen.core.events.types import Event, MatchPattern
from openjiuwen.core.foundation.llm import ToolCall, ToolMessage

if TYPE_CHECKING:
    from openjiuwen.core.events.bus import ExtensionEventBus

TOOL_CALL = "tool.call"


@dataclass(frozen=True, slots=True)
class ToolCallEventInput:
    """One tool call of the batch; handlers may only replace ``arguments``."""

    tool_call_id: str
    tool_name: str
    arguments: object
    agent_id: str | None = None


@dataclass(frozen=True, slots=True)
class ToolCallEventOutput:
    """Result of one tool call: ``result`` for the AgentLoop, ``text`` for the model's ToolMessage."""

    tool_call_id: str
    result: object | None = None
    text: str | None = None


ToolCallExecutor = Callable[
    [list[ToolCall]],
    Awaitable[list[tuple[object, ToolMessage | None]]],
]


@dataclass(slots=True)
class ToolCallTriggerContext:
    """Private per-trigger data: the original batch, executor, and raw results written by CORE."""

    tool_calls: tuple[ToolCall, ...]
    invoke_original: ToolCallExecutor
    raw_results: list[tuple[object, ToolMessage | None]] | None = None


def match_pattern_func(event_input: object, match_pattern: MatchPattern) -> bool:
    """Match every batch with an empty pattern, or batches containing ``tool_name``."""
    if any(key != "tool_name" or not isinstance(value, str) for key, value in match_pattern.items()):
        raise build_error(StatusCode.EVENTS_MATCH_INVALID, reason="tool.call patterns only support tool_name")
    if not _is_valid_input(event_input):
        return False
    tool_name = match_pattern.get("tool_name")
    return tool_name is None or any(item.tool_name == tool_name for item in event_input)


def _is_valid_input(value: object) -> bool:
    return isinstance(value, tuple) and all(
        isinstance(item, ToolCallEventInput) and isinstance(item.tool_call_id, str) and isinstance(item.tool_name, str)
        for item in value
    )


def _is_valid_output(value: object) -> bool:
    return isinstance(value, tuple) and all(
        isinstance(item, ToolCallEventOutput)
        and isinstance(item.tool_call_id, str)
        and (item.text is None or isinstance(item.text, str))
        for item in value
    )


def _message_text(message: ToolMessage | None) -> str | None:
    """Model-facing text of a ToolMessage; non-text (multimodal) content is not exposed."""
    if message is None or not isinstance(message.content, str):
        return None
    return message.content


def _apply_text(original: ToolMessage | None, text: str | None, tool_call_id: str) -> ToolMessage | None:
    if text == _message_text(original):
        return original
    if text is None:
        return None
    if original is None:
        return ToolMessage(content=text, tool_call_id=tool_call_id)
    return original.model_copy(update={"content": text}, deep=True)


def check_output_identity(outputs: object, tool_calls: tuple[ToolCall, ...]) -> None:
    """Reject final outputs that do not answer the triggered batch one-to-one and in order."""
    if len(outputs) != len(tool_calls) or any(
        output.tool_call_id != call.id for output, call in zip(outputs, tool_calls)
    ):
        raise_error(StatusCode.EVENTS_PAYLOAD_INVALID, reason="tool.call output does not match the tool batch")


def apply_tool_call_outputs(
    context: ToolCallTriggerContext, outputs: tuple[ToolCallEventOutput, ...]
) -> list[tuple[object, ToolMessage | None]]:
    """Apply the final outputs onto CORE's raw results; build results from outputs when CORE was short-circuited."""
    check_output_identity(outputs, context.tool_calls)
    if context.raw_results is None:
        return [
            (
                output.result,
                ToolMessage(content=output.text, tool_call_id=output.tool_call_id) if output.text is not None else None,
            )
            for output in outputs
        ]
    for index, output in enumerate(outputs):
        _, original = context.raw_results[index]
        context.raw_results[index] = (output.result, _apply_text(original, output.text, output.tool_call_id))
    return context.raw_results


def serialize_arguments(arguments: Any) -> str:
    """Encode tool arguments as the string stored on ``ToolCall.arguments``."""
    if isinstance(arguments, str):
        return arguments
    try:
        return json.dumps(arguments, ensure_ascii=False)
    except (TypeError, ValueError) as error:
        raise build_error(
            StatusCode.EVENTS_PAYLOAD_INVALID, reason="tool.call arguments must be JSON serializable", cause=error
        ) from error


def _to_tool_calls(items: tuple[ToolCallEventInput, ...], originals: tuple[ToolCall, ...]) -> list[ToolCall]:
    """Map the final inputs back onto the original ToolCalls, copying only rewritten ones."""
    if len(items) != len(originals) or any(
        (item.tool_call_id, item.tool_name) != (original.id, original.name) for item, original in zip(items, originals)
    ):
        raise_error(StatusCode.EVENTS_PAYLOAD_INVALID, reason="tool.call batch identity changed")
    return [
        original
        if item.arguments is original.arguments
        else original.model_copy(update={"arguments": serialize_arguments(item.arguments)})
        for item, original in zip(items, originals)
    ]


def register(bus: ExtensionEventBus) -> None:
    """Register ``tool.call`` and its CORE handler on ``bus``."""

    async def core_handler(
        event: Event,
        ops: EventHandlerOps | None,
        event_next: EventNext,
    ) -> tuple[ToolCallEventOutput, ...]:
        """Execute the batch once through the agent's existing AbilityManager."""
        del event_next
        context = getattr(ops, "trigger_context", None)
        if not isinstance(context, ToolCallTriggerContext):
            raise_error(StatusCode.EVENTS_STATE_INVALID, reason="tool.call requires ToolCallTriggerContext")
        tool_calls = _to_tool_calls(event.event_input, context.tool_calls)
        results = await context.invoke_original(tool_calls)
        context.raw_results = results
        return tuple(
            ToolCallEventOutput(tool_call_id=tool_call.id, result=result, text=_message_text(tool_message))
            for tool_call, (result, tool_message) in zip(tool_calls, results)
        )

    bus.register_event_type(
        TOOL_CALL,
        match_pattern_func,
        core_handler,
        input_validator=_is_valid_input,
        output_validator=_is_valid_output,
    )
