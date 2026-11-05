# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Typed state events, field matching, validation and durable default handlers."""

from dataclasses import dataclass
from typing import Any, cast

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import BaseError, build_error
from openjiuwen.core.events import Event, EventHandlerOps, EventNext, ExtensionEventBusProtocol, MatchPattern
from openjiuwen.core.events.ops_impl.state import state_for
from openjiuwen.core.extension.data import StateAddress, StateSetResult, StateSnapshot
from openjiuwen.core.extension.data.json_values import copy_json, encode_json, valid_package_id
from openjiuwen.core.extension.data.state_models import require_address, require_version

STATE_GET_EVENT = "state.get"
STATE_SET_EVENT = "state.set"


@dataclass(frozen=True, slots=True)
class StateGetInput:
    """Read public state on behalf of the original calling package."""

    package_id: str
    address: StateAddress


@dataclass(frozen=True, slots=True)
class StateSetInput:
    """Write only the original package's state, optionally comparing a version."""

    package_id: str
    address: StateAddress
    value: Any
    expected_version: int | None = None


def match_pattern_func(event_input: object, match_pattern: MatchPattern) -> bool:
    """Match documented state fields exactly without interpreting a target."""
    fields = {"package_id", "ref.package", "ref.key", "scope", "session_id"}
    if any(key not in fields or not isinstance(value, str) for key, value in match_pattern.items()):
        raise build_error(StatusCode.EVENTS_MATCH_INVALID, reason="state patterns require supported string fields")
    if not isinstance(event_input, (StateGetInput, StateSetInput)):
        return False
    address = event_input.address
    values = {"package_id": event_input.package_id, "ref.package": address.package, "ref.key": address.key,
              "scope": address.scope, "session_id": address.session_id}
    return all(values.get(key) == value for key, value in match_pattern.items())


def _valid_input(request: object) -> bool:
    if not isinstance(request, (StateGetInput, StateSetInput)) or not valid_package_id(request.package_id):
        return False
    try:
        require_address(request.address)
        if isinstance(request, StateSetInput):
            require_version(request.expected_version)
            encode_json(copy_json(request.value))
            return request.package_id == request.address.package
        return True
    except BaseError:
        return False


def _valid_snapshot(output: object) -> bool:
    if not isinstance(output, StateSnapshot) or not isinstance(output.exists, bool):
        return False
    try:
        require_version(output.version)
        encode_json(copy_json(output.value))
        return output.version is not None and (output.exists or output.value is None)
    except BaseError:
        return False


def _valid_result(output: object) -> bool:
    if not isinstance(output, StateSetResult):
        return False
    if not isinstance(output.is_set, bool) or not isinstance(output.changed, bool):
        return False
    try:
        require_version(output.version)
    except BaseError:
        return False
    if output.is_set:
        return output.reason is None and output.version is not None
    return not output.changed and output.reason in ("version_conflict", "storage_error", "rejected")


def register(bus: ExtensionEventBusProtocol) -> None:
    """Register state events through the existing builtin discovery entry."""

    async def get_core(event: Event, ops: EventHandlerOps | None, event_next: EventNext) -> StateSnapshot:
        del ops, event_next
        request = cast(StateGetInput, event.event_input)
        binding = state_for(bus)
        render_id = binding.dependencies.current_build
        if render_id is not None:
            return await binding.dependencies.read(
                render_id, request.address, lambda: binding.states.get(request.address),
            )
        return await binding.states.get(request.address)

    async def set_core(event: Event, ops: EventHandlerOps | None, event_next: EventNext) -> StateSetResult:
        del ops, event_next
        binding = state_for(bus)
        if binding.dependencies.current_build is not None:
            raise build_error(StatusCode.EVENTS_OPS_INVALID, reason="state.set is forbidden during a render build")
        request = cast(StateSetInput, event.event_input)
        return await binding.states.set(
            request.address, request.value, expected_version=request.expected_version,
        )

    bus.register_event_type(
        STATE_GET_EVENT, match_pattern_func, get_core,
        input_validator=lambda value: isinstance(value, StateGetInput) and _valid_input(value),
        output_validator=_valid_snapshot,
    )
    bus.register_event_type(
        STATE_SET_EVENT, match_pattern_func, set_core,
        input_validator=lambda value: isinstance(value, StateSetInput) and _valid_input(value),
        output_validator=_valid_result,
    )
