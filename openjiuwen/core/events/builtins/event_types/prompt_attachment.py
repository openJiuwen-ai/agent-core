# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""The ``prompt.attachment`` event for prompt-history attachment extensions."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import TYPE_CHECKING, TypeAlias, cast

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.core.events.event_next import EventNext
from openjiuwen.core.events.ops import EventHandlerOps
from openjiuwen.core.events.types import Event, MatchPattern

if TYPE_CHECKING:
    from openjiuwen.core.events.bus import ExtensionEventBus


PROMPT_ATTACHMENT = "prompt.attachment"
PromptAttachmentOrigin: TypeAlias = Mapping[str, str]


def _freeze_value(value: object) -> object:
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _freeze_value(nested) for key, nested in value.items()})
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return tuple(_freeze_value(item) for item in value)
    return value


@dataclass(frozen=True, slots=True)
class PromptAttachmentInput:
    """Immutable snapshot of one attachment before it is rendered."""

    type: str
    text: str
    origin: PromptAttachmentOrigin = field(
        default_factory=lambda: cast(PromptAttachmentOrigin, MappingProxyType({"kind": "engine"}))
    )
    agent_id: str | None = None
    detail: Mapping[str, object] = field(default_factory=lambda: MappingProxyType({}))

    def __post_init__(self) -> None:
        object.__setattr__(self, "origin", _freeze_value(self.origin))
        object.__setattr__(self, "detail", _freeze_value(self.detail))


@dataclass(frozen=True, slots=True)
class PromptAttachmentOutput:
    """Text consumed by the attachment renderer; ``None`` omits this fragment."""

    text: str | None


def _is_valid_origin(value: object) -> bool:
    if not isinstance(value, Mapping) or not isinstance(value.get("kind"), str):
        return False
    kind = value["kind"]
    if kind == "engine":
        return set(value) == {"kind"}
    return (
        kind in {"hook", "plugin"}
        and set(value) == {"kind", "event"}
        and isinstance(value.get("event"), str)
        and bool(value["event"])
    )


def _is_valid_input(value: object) -> bool:
    return (
        isinstance(value, PromptAttachmentInput)
        and isinstance(value.type, str)
        and isinstance(value.text, str)
        and _is_valid_origin(value.origin)
        and (value.agent_id is None or isinstance(value.agent_id, str))
        and isinstance(value.detail, Mapping)
        and all(isinstance(key, str) for key in value.detail)
    )


def _is_valid_output(value: object) -> bool:
    return isinstance(value, PromptAttachmentOutput) and (
        value.text is None or isinstance(value.text, str)
    )


def _validate_pattern_mapping(value: object, name: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or not all(isinstance(key, str) for key in value):
        raise build_error(StatusCode.EVENTS_MATCH_INVALID, reason=f"prompt.attachment {name} must be a mapping")
    return value


def match_pattern_func(event_input: object, match_pattern: MatchPattern) -> bool:
    """Match by attachment type, origin, agent, or detail fields."""
    if not _is_valid_input(event_input):
        return False

    allowed_fields = {
        "type",
        "agent_id",
        "origin_kind",
        "origin_event",
        "section",
        "source",
        "content_kind",
        "origin",
        "detail",
    }
    unknown = set(match_pattern) - allowed_fields
    if unknown:
        raise build_error(
            StatusCode.EVENTS_MATCH_INVALID,
            reason=f"prompt.attachment patterns do not support: {sorted(unknown)}",
        )

    current = cast(PromptAttachmentInput, event_input)
    actual = {
        "type": current.type,
        "agent_id": current.agent_id,
        "origin_kind": current.origin.get("kind"),
        "origin_event": current.origin.get("event"),
        "section": current.detail.get("section"),
        "source": current.detail.get("source"),
        "content_kind": current.detail.get("content_kind"),
    }
    for key in allowed_fields - {"origin", "detail"}:
        if key in match_pattern and match_pattern[key] != actual[key]:
            return False

    if "origin" in match_pattern:
        expected_origin = _validate_pattern_mapping(match_pattern["origin"], "origin pattern")
        if any(current.origin.get(key) != value for key, value in expected_origin.items()):
            return False
    if "detail" in match_pattern:
        expected_detail = _validate_pattern_mapping(match_pattern["detail"], "detail pattern")
        if any(current.detail.get(key) != value for key, value in expected_detail.items()):
            return False
    return True


def register(bus: ExtensionEventBus) -> None:
    """Register ``prompt.attachment`` and its pass-through CORE handler."""

    async def core_handler(
        event: Event,
        ops: EventHandlerOps | None,
        event_next: EventNext,
    ) -> PromptAttachmentOutput:
        del ops, event_next
        event_input = event.event_input
        if not isinstance(event_input, PromptAttachmentInput):
            raise build_error(StatusCode.EVENTS_PAYLOAD_INVALID, reason="invalid prompt.attachment input")
        return PromptAttachmentOutput(text=event_input.text)

    bus.register_event_type(
        PROMPT_ATTACHMENT,
        match_pattern_func,
        core_handler,
        input_validator=_is_valid_input,
        output_validator=_is_valid_output,
    )


# Event-suffixed aliases follow the naming used by other builtin event modules.
PromptAttachmentEventInput = PromptAttachmentInput
PromptAttachmentEventOutput = PromptAttachmentOutput


__all__ = [
    "PROMPT_ATTACHMENT",
    "PromptAttachmentInput",
    "PromptAttachmentOutput",
    "PromptAttachmentEventInput",
    "PromptAttachmentEventOutput",
    "PromptAttachmentOrigin",
    "match_pattern_func",
    "register",
]
