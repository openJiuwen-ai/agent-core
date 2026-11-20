# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Typed store events, exact field matchers, validators, and default CORE."""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import BaseError, build_error
from openjiuwen.core.events.event_next import EventNext
from openjiuwen.core.events.ops import EventHandlerOps
from openjiuwen.core.events.protocol import EventHandler, ExtensionEventBusProtocol
from openjiuwen.core.events.ops_impl.store import store_for
from openjiuwen.core.events.types import Event, MatchPattern
from openjiuwen.core.extension.data.json_values import copy_json, encode_json, valid_package_id

STORE_GET_EVENT = "store.get"
STORE_SET_EVENT = "store.set"
STORE_DELETE_EVENT = "store.delete"
STORE_KEYS_EVENT = "store.keys"


@dataclass(frozen=True, slots=True)
class StoreGetInput:
    """Read a package key; default is returned only for a missing key."""

    package_id: str
    key: str
    default: Any = None


@dataclass(frozen=True, slots=True)
class StoreSetInput:
    """Persist a JSON value in the original caller's package."""

    package_id: str
    key: str
    value: Any


@dataclass(frozen=True, slots=True)
class StoreDeleteInput:
    """Remove a key from the original caller's package."""

    package_id: str
    key: str


@dataclass(frozen=True, slots=True)
class StoreKeysInput:
    """List the original caller's package keys in insertion order."""

    package_id: str


_INPUT_TYPES = {
    STORE_GET_EVENT: StoreGetInput,
    STORE_SET_EVENT: StoreSetInput,
    STORE_DELETE_EVENT: StoreDeleteInput,
    STORE_KEYS_EVENT: StoreKeysInput,
}


def match_pattern_func(event_input: object, match_pattern: MatchPattern) -> bool:
    """Match package_id and key exactly; reject unsupported pattern fields."""
    fields = {"package_id"} if isinstance(event_input, StoreKeysInput) else {"package_id", "key"}
    if any(key not in fields or not isinstance(value, str) for key, value in match_pattern.items()):
        raise build_error(StatusCode.EVENTS_MATCH_INVALID, reason="store patterns require supported string fields")
    if not isinstance(event_input, (StoreGetInput, StoreSetInput, StoreDeleteInput, StoreKeysInput)):
        return False
    return all(getattr(event_input, key) == value for key, value in match_pattern.items())


def _input_validator(event_type: str) -> Callable[[object], bool]:
    def validate(value: object) -> bool:
        matches_type = isinstance(value, _INPUT_TYPES[event_type])
        if not isinstance(value, (StoreGetInput, StoreSetInput, StoreDeleteInput, StoreKeysInput)):
            return False
        if not matches_type or not valid_package_id(value.package_id):
            return False
        if not isinstance(value, StoreKeysInput) and not isinstance(value.key, str):
            return False
        if isinstance(value, StoreSetInput):
            try:
                encode_json(copy_json(value.value))
            except BaseError:
                return False
        return True

    return validate


def _output_validator(event_type: str) -> Callable[[object], bool]:
    def validate(value: object) -> bool:
        if event_type in (STORE_SET_EVENT, STORE_DELETE_EVENT):
            return isinstance(value, bool)
        if event_type == STORE_KEYS_EVENT:
            return isinstance(value, list) and all(isinstance(key, str) for key in value)
        return True

    return validate


def _core_handler(bus: ExtensionEventBusProtocol, event_type: str) -> EventHandler:
    async def core(event: Event, ops: EventHandlerOps | None, event_next: EventNext) -> object:
        del ops, event_next
        request = event.event_input
        service = store_for(bus)
        if event_type == STORE_GET_EVENT and isinstance(request, StoreGetInput):
            return await service.get(request.package_id, request.key, request.default)
        if event_type == STORE_SET_EVENT and isinstance(request, StoreSetInput):
            return await service.set(request.package_id, request.key, request.value)
        if event_type == STORE_DELETE_EVENT and isinstance(request, StoreDeleteInput):
            return await service.delete(request.package_id, request.key)
        if isinstance(request, StoreKeysInput):
            return await service.keys(request.package_id)
        raise build_error(StatusCode.EVENTS_PAYLOAD_INVALID, reason="unexpected store request type")

    return core


def register(bus: ExtensionEventBusProtocol) -> None:
    """Register the four store events without creating or accessing storage."""
    for event_type in _INPUT_TYPES:
        bus.register_event_type(
            event_type,
            match_pattern_func,
            _core_handler(bus, event_type),
            input_validator=_input_validator(event_type),
            output_validator=_output_validator(event_type),
        )
