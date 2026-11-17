# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""The ``prompt.compose`` event for final system-prompt section assembly."""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error, raise_error
from openjiuwen.core.events.event_next import EventNext
from openjiuwen.core.events.ops import EventHandlerOps
from openjiuwen.core.events.types import Event, MatchPattern

if TYPE_CHECKING:
    from openjiuwen.core.events.bus import ExtensionEventBus

PROMPT_COMPOSE = "prompt.compose"


@dataclass(frozen=True, slots=True)
class PromptOutputStyle:
    """Value-only output style facts used by prompt composition handlers."""

    name: str
    is_keeping_coding_instructions: bool


@dataclass(frozen=True, slots=True)
class PromptComposeInput:
    """Composition facts supplied before the native section snapshot is built."""

    model: str
    prompt_model: str | None
    surfaces: tuple[str, ...]
    tools: tuple[str, ...]
    output_style: PromptOutputStyle | None
    traits: tuple[str, ...]
    detail: Mapping[str, object]


@dataclass(frozen=True, slots=True)
class PromptSectionSpec:
    """One ordered section in the final composed system prompt."""

    id: str
    text: str | None
    priority: int
    category: str | None
    carrier: str
    detail: Mapping[str, object]


@dataclass(frozen=True, slots=True)
class PromptComposeOutput:
    """Final ordered section view returned by ``prompt.compose``."""

    sections: tuple[PromptSectionSpec, ...]


def _is_value(value: object) -> bool:
    """Return whether a detail value is safe to carry as a snapshot."""
    if value is None or isinstance(value, (str, bool, int)):
        return True
    if isinstance(value, float):
        return math.isfinite(value)
    if isinstance(value, Mapping):
        return all(isinstance(key, str) and _is_value(item) for key, item in value.items())
    if isinstance(value, (list, tuple)):
        return all(_is_value(item) for item in value)
    return False


def _is_detail(value: object) -> bool:
    return isinstance(value, Mapping) and _is_value(value)


def _is_string_tuple(value: object) -> bool:
    return isinstance(value, tuple) and all(isinstance(item, str) for item in value)


def _is_valid_output_style(value: object) -> bool:
    return value is None or (
        isinstance(value, PromptOutputStyle)
        and isinstance(value.name, str)
        and bool(value.name)
        and isinstance(value.is_keeping_coding_instructions, bool)
    )


def _is_valid_input(value: object) -> bool:
    return (
        isinstance(value, PromptComposeInput)
        and isinstance(value.model, str)
        and (value.prompt_model is None or isinstance(value.prompt_model, str))
        and _is_string_tuple(value.surfaces)
        and _is_string_tuple(value.tools)
        and _is_valid_output_style(value.output_style)
        and _is_string_tuple(value.traits)
        and _is_detail(value.detail)
    )


def _is_valid_section(value: object) -> bool:
    return (
        isinstance(value, PromptSectionSpec)
        and isinstance(value.id, str)
        and bool(value.id)
        and (value.text is None or isinstance(value.text, str))
        and isinstance(value.priority, int)
        and not isinstance(value.priority, bool)
        and (value.category is None or isinstance(value.category, str))
        and isinstance(value.carrier, str)
        and bool(value.carrier)
        and _is_detail(value.detail)
    )


def _is_valid_output(value: object) -> bool:
    if not isinstance(value, PromptComposeOutput) or not isinstance(value.sections, tuple):
        return False
    if not all(_is_valid_section(section) for section in value.sections):
        return False
    ids = [section.id for section in value.sections]
    return len(ids) == len(set(ids))


def match_pattern_func(event_input: object, match_pattern: MatchPattern) -> bool:
    """Match all compositions, or filter by model and one declared trait."""
    if any(key not in {"model", "trait"} or not isinstance(value, str) for key, value in match_pattern.items()):
        raise build_error(StatusCode.EVENTS_MATCH_INVALID, reason="prompt.compose patterns support model and trait")
    if not isinstance(event_input, PromptComposeInput):
        return False
    model = match_pattern.get("model")
    trait = match_pattern.get("trait")
    return (model is None or event_input.model == model) and (trait is None or trait in event_input.traits)


async def _core_handler(
    bus: ExtensionEventBus,
    event: Event,
    ops: EventHandlerOps | None,
    event_next: EventNext,
) -> PromptComposeOutput:
    """Resolve the session-bound DeepAgent and build the section snapshot."""
    del event_next
    if ops is None or ops.session_context is None or not ops.session_context.session_id:
        raise_error(StatusCode.EVENTS_STATE_INVALID, reason="prompt.compose requires a session context")

    from openjiuwen.core.single_agent.agent_runtime import get_agent_runtime
    from openjiuwen.harness.deep_agent import DeepAgent

    session_context = ops.session_context
    agent = get_agent_runtime().get(session_context.session_id)
    if not isinstance(agent, DeepAgent):
        raise_error(StatusCode.EVENTS_STATE_INVALID, reason="prompt.compose requires a bound DeepAgent")

    result = await agent.compose_prompt_sections(
        cast(PromptComposeInput, event.event_input),
        bus,
        session_context,
    )
    if not isinstance(result, PromptComposeOutput):
        raise_error(StatusCode.EVENTS_PAYLOAD_INVALID, reason="prompt.compose CORE returned an invalid output")
    return result


def register(bus: ExtensionEventBus) -> None:
    """Register ``prompt.compose`` and its Agent-backed CORE handler."""

    async def core_handler(
        event: Event,
        ops: EventHandlerOps | None,
        event_next: EventNext,
    ) -> PromptComposeOutput:
        return await _core_handler(bus, event, ops, event_next)

    bus.register_event_type(
        PROMPT_COMPOSE,
        match_pattern_func,
        core_handler,
        input_validator=_is_valid_input,
        output_validator=_is_valid_output,
    )
