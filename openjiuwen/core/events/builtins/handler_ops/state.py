# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Build state methods that resolve scope and enter their matching event chains."""

from typing import Any

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.core.events.builtins.event_types.state import (
    STATE_GET_EVENT, STATE_SET_EVENT, StateGetInput, StateSetInput,
)
from openjiuwen.core.events.event_next import EventNext
from openjiuwen.core.events.ops import EventHandlerOps, EventHandlerOpsBuilder
from openjiuwen.core.events.ops_impl.state import state_call
from openjiuwen.core.events.protocol import ExtensionEventBusProtocol
from openjiuwen.core.events.types import OPS_BUILD_EVENT_TYPE, Event, HandlerLevel
from openjiuwen.core.extension.data import StateAddress, StateRef, StateSetResult, StateSnapshot
from openjiuwen.core.extension.data.json_values import copy_json, require_package_id
from openjiuwen.core.extension.data.state_models import require_address, require_version


async def _resolve(ops: EventHandlerOps, ref: StateRef) -> tuple[str, StateAddress]:
    package_id = await ops.package.name()
    if ops.package_metadata is None or package_id != ops.package_metadata.package_id:
        raise build_error(StatusCode.EVENTS_OPS_INVALID, reason="state requires a handler with package metadata")
    require_package_id(package_id)
    if not isinstance(ref, StateRef) or ref.scope not in ("auto", "session", "application"):
        raise build_error(StatusCode.EXTENSION_DATA_INVALID, reason="state ref must specify a supported scope")
    scope = ref.scope
    if scope == "auto":
        scope = "session" if ops.session_context is not None else "application"
    session_id = ops.session_context.session_id if scope == "session" and ops.session_context is not None else None
    address = StateAddress(ref.package, ref.key, scope, session_id)
    require_address(address)
    return package_id, address


def register(bus: ExtensionEventBusProtocol) -> None:
    """Register a Build handler without accessing state files."""

    async def state_get(ops: EventHandlerOps, ref: StateRef) -> StateSnapshot:
        package_id, address = await _resolve(ops, ref)
        with state_call(bus, package_id, ops):
            output = await bus.trigger(
                Event(STATE_GET_EVENT, StateGetInput(package_id, address)), ops.session_context,
            )
        if not isinstance(output, StateSnapshot):
            raise build_error(StatusCode.EVENTS_PAYLOAD_INVALID, reason="state.get returned an invalid snapshot")
        return StateSnapshot(output.exists, copy_json(output.value), output.version)

    async def state_set(
        ops: EventHandlerOps, ref: StateRef, value: Any, expected_version: int | None = None,
    ) -> StateSetResult:
        package_id, address = await _resolve(ops, ref)
        if address.package != package_id:
            raise build_error(StatusCode.EVENTS_OPS_INVALID, reason="state.set cannot write another package's state")
        require_version(expected_version)
        request = StateSetInput(package_id, address, copy_json(value), expected_version)
        with state_call(bus, package_id, ops):
            output = await bus.trigger(
                Event(STATE_SET_EVENT, request), ops.session_context,
            )
        if not isinstance(output, StateSetResult):
            raise build_error(StatusCode.EVENTS_PAYLOAD_INVALID, reason="state.set returned an invalid result")
        return output

    async def build_state_ops(
        event: Event, ops: EventHandlerOps | None, event_next: EventNext,
    ) -> EventHandlerOpsBuilder:
        del ops
        builder = await event_next(event)
        if not isinstance(builder, EventHandlerOpsBuilder):
            raise build_error(StatusCode.EVENTS_OPS_INVALID, reason="ops.build did not return a builder")
        builder.add_namespace("state")
        builder.add_method("state", "get", state_get)
        builder.add_method("state", "set", state_set)
        return builder

    bus.register_event_handler(OPS_BUILD_EVENT_TYPE, build_state_ops, {}, HandlerLevel.BUILTIN)
