# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""The ``tool.check`` event: the first permission decision for one tool call."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error, raise_error
from openjiuwen.core.events.event_next import EventNext
from openjiuwen.core.events.ops import EventHandlerOps
from openjiuwen.core.events.types import Event, MatchPattern

if TYPE_CHECKING:
    from openjiuwen.core.events.bus import ExtensionEventBus
    from openjiuwen.core.single_agent.rail.base import AgentCallbackContext

TOOL_CHECK = "tool.check"
_DECISIONS = ("allow", "ask", "deny")


@dataclass(frozen=True, slots=True)
class ToolCheckInput:
    """Public identity of the checked tool call; arguments stay private."""

    tool_call_id: str | None
    tool_name: str
    agent_id: str | None = None


@dataclass(frozen=True, slots=True)
class ToolCheckOutput:
    """Public permission decision returned to event handlers."""

    decision: Literal["allow", "ask", "deny"]
    reason: str | None = None


ToolCheckExecutor = Callable[
    ["AgentCallbackContext"],
    Awaitable[tuple[object | None, ToolCheckOutput]],
]


@dataclass(slots=True)
class ToolCheckTriggerContext:
    """Private invocation data and the raw host result written by CORE."""

    callback_ctx: AgentCallbackContext
    invoke_original: ToolCheckExecutor
    raw_result: object | None = None


def match_pattern_func(event_input: object, match_pattern: MatchPattern) -> bool:
    """Match every check with an empty pattern, or checks for ``tool_name``."""
    if any(key != "tool_name" or not isinstance(value, str) for key, value in match_pattern.items()):
        raise build_error(StatusCode.EVENTS_MATCH_INVALID, reason="tool.check patterns only support tool_name")
    if not _is_valid_input(event_input):
        return False
    tool_name = match_pattern.get("tool_name")
    return tool_name is None or event_input.tool_name == tool_name


def _is_valid_input(value: object) -> bool:
    return (
        isinstance(value, ToolCheckInput)
        and (value.tool_call_id is None or isinstance(value.tool_call_id, str))
        and isinstance(value.tool_name, str)
        and (value.agent_id is None or isinstance(value.agent_id, str))
    )


def _is_valid_output(value: object) -> bool:
    return (
        isinstance(value, ToolCheckOutput)
        and value.decision in _DECISIONS
        and (value.reason is None or isinstance(value.reason, str))
    )


def register(bus: ExtensionEventBus) -> None:
    """Register ``tool.check`` and its CORE handler on ``bus``."""

    async def core_handler(
        event: Event,
        ops: EventHandlerOps | None,
        event_next: EventNext,
    ) -> ToolCheckOutput:
        """Run the host's first permission check once for the identified tool call."""
        del event, event_next
        context = getattr(ops, "trigger_context", None)
        if not isinstance(context, ToolCheckTriggerContext):
            raise_error(StatusCode.EVENTS_STATE_INVALID, reason="tool.check requires ToolCheckTriggerContext")
        raw_result, output = await context.invoke_original(context.callback_ctx)
        context.raw_result = raw_result
        return output

    bus.register_event_type(
        TOOL_CHECK,
        match_pattern_func,
        core_handler,
        input_validator=_is_valid_input,
        output_validator=_is_valid_output,
    )
