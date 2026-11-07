# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""The ``prompt.section`` event for one rendered system-prompt section."""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.core.events.event_next import EventNext
from openjiuwen.core.events.ops import EventHandlerOps
from openjiuwen.core.events.types import Event, MatchPattern

if TYPE_CHECKING:
    from openjiuwen.core.events.bus import ExtensionEventBus

PROMPT_SECTION = "prompt.section"


@dataclass(frozen=True, slots=True)
class PromptSectionInput:
    """Rendered text and value-only metadata for one prompt section."""

    name: str
    text: str | None
    detail: Mapping[str, object]


@dataclass(frozen=True, slots=True)
class PromptSectionOutput:
    """Final text for one prompt section; ``None`` omits the section."""

    text: str | None


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


def match_pattern_func(event_input: object, match_pattern: MatchPattern) -> bool:
    """Match every section or only the requested stable section name."""
    if any(key != "name" or not isinstance(value, str) for key, value in match_pattern.items()):
        raise build_error(StatusCode.EVENTS_MATCH_INVALID, reason="prompt.section patterns only support name")
    if not isinstance(event_input, PromptSectionInput):
        return False
    name = match_pattern.get("name")
    return name is None or event_input.name == name


def _is_valid_input(value: object) -> bool:
    return (
        isinstance(value, PromptSectionInput)
        and isinstance(value.name, str)
        and bool(value.name)
        and (value.text is None or isinstance(value.text, str))
        and _is_detail(value.detail)
    )


def _is_valid_output(value: object) -> bool:
    return isinstance(value, PromptSectionOutput) and (value.text is None or isinstance(value.text, str))


async def core_handler(
    event: Event,
    ops: EventHandlerOps | None,
    event_next: EventNext,
) -> PromptSectionOutput:
    """Pass through the rendered section text without touching the builder."""
    del ops, event_next
    event_input = cast(PromptSectionInput, event.event_input)
    return PromptSectionOutput(text=event_input.text)


def register(bus: ExtensionEventBus) -> None:
    """Register ``prompt.section`` and its pass-through CORE handler."""
    bus.register_event_type(
        PROMPT_SECTION,
        match_pattern_func,
        core_handler,
        input_validator=_is_valid_input,
        output_validator=_is_valid_output,
    )
