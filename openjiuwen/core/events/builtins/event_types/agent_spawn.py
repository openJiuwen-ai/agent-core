# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Public ``agent.spawn`` contract and its single CORE side-effect path."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import ValidationError, raise_error
from openjiuwen.core.events.event_next import EventNext
from openjiuwen.core.events.ops import EventHandlerOps
from openjiuwen.core.events.types import Event, MatchPattern

if TYPE_CHECKING:
    from openjiuwen.core.events.bus import ExtensionEventBus
    from openjiuwen.harness.subagent_runtime.control import SubagentControl
    from openjiuwen.harness.subagent_runtime.models import SpawnResult

AGENT_SPAWN = "agent.spawn"


@dataclass(frozen=True, slots=True)
class AgentSpawnInput:
    """Public request for creating one persistent subagent."""

    subagent_type: str
    task_description: str
    display_name: str
    role: str


@dataclass(frozen=True, slots=True)
class AgentSpawnOutput:
    """Public identity and initial status of a created subagent."""

    subagent_id: str
    status: str


@dataclass(slots=True)
class AgentSpawnTriggerContext:
    """Private runtime dependencies and raw result for one spawn dispatch."""

    control: SubagentControl
    allowed_subagent_types: frozenset[str] | None = None
    browser_capabilities: list[str] | None = None
    raw_result: SpawnResult | None = None


def prepare_agent_spawn_input(value: object) -> AgentSpawnInput:
    """Normalize and validate the four public spawn request fields."""
    if not isinstance(value, AgentSpawnInput):
        raise_error(
            StatusCode.EVENTS_PAYLOAD_INVALID,
            reason="agent.spawn requires AgentSpawnInput",
        )

    normalized: dict[str, str] = {}
    for field_name in ("subagent_type", "task_description", "display_name", "role"):
        field_value = getattr(value, field_name)
        if not isinstance(field_value, str) or not field_value.strip():
            raise_error(
                StatusCode.EVENTS_PAYLOAD_INVALID,
                reason=f"agent.spawn {field_name} must be a non-empty string",
            )
        normalized[field_name] = field_value.strip()
    return AgentSpawnInput(**normalized)


def match_pattern_func(event_input: object, match_pattern: MatchPattern) -> bool:
    """Match every valid spawn request with an empty pattern."""
    del event_input
    return not match_pattern


def _is_valid_input(value: object) -> bool:
    try:
        prepare_agent_spawn_input(value)
    except ValidationError:
        return False
    return True


def _is_valid_output(value: object) -> bool:
    return (
        isinstance(value, AgentSpawnOutput)
        and isinstance(value.subagent_id, str)
        and bool(value.subagent_id)
        and isinstance(value.status, str)
        and bool(value.status)
    )


async def agent_spawn_core_handler(
    event: Event,
    ops: EventHandlerOps | None,
    event_next: EventNext,
) -> AgentSpawnOutput:
    """Validate the final request and create the subagent exactly once."""
    del event_next
    context = getattr(ops, "trigger_context", None)
    if not isinstance(context, AgentSpawnTriggerContext):
        raise_error(
            StatusCode.EVENTS_STATE_INVALID,
            reason="agent.spawn requires AgentSpawnTriggerContext",
        )
    request = prepare_agent_spawn_input(event.event_input)
    if (
        context.allowed_subagent_types is not None
        and request.subagent_type not in context.allowed_subagent_types
    ):
        raise_error(
            StatusCode.EVENTS_PAYLOAD_INVALID,
            reason=f"subagent type is not available: {request.subagent_type}",
        )

    result = await context.control.spawn(
        request.subagent_type,
        request.task_description,
        display_name=request.display_name,
        role=request.role,
        browser_capabilities=context.browser_capabilities,
    )
    await context.control.emit_status_update(result.subagent_id)
    context.raw_result = result
    return AgentSpawnOutput(
        subagent_id=result.subagent_id,
        status=result.status.kind.value,
    )


def register(bus: ExtensionEventBus) -> None:
    """Register ``agent.spawn`` and its CORE handler on ``bus``."""
    bus.register_event_type(
        AGENT_SPAWN,
        match_pattern_func,
        agent_spawn_core_handler,
        input_validator=_is_valid_input,
        output_validator=_is_valid_output,
    )
