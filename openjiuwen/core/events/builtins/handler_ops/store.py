# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Build package-bound store methods that trigger their matching events."""

from typing import Any, cast

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.core.events.builtins.event_types.store import (
    STORE_DELETE_EVENT,
    STORE_GET_EVENT,
    STORE_KEYS_EVENT,
    STORE_SET_EVENT,
    StoreDeleteInput,
    StoreGetInput,
    StoreKeysInput,
    StoreSetInput,
)
from openjiuwen.core.events.event_next import EventNext
from openjiuwen.core.events.ops import EventHandlerOps, EventHandlerOpsBuilder
from openjiuwen.core.events.protocol import ExtensionEventBusProtocol
from openjiuwen.core.events.ops_impl.store import store_call
from openjiuwen.core.events.types import OPS_BUILD_EVENT_TYPE, Event, HandlerLevel
from openjiuwen.core.extension.data.json_values import copy_json, require_package_id


async def _package_id(ops: EventHandlerOps) -> str:
    package_id = await ops.package.name()
    if ops.package_metadata is None or package_id != ops.package_metadata.package_id:
        raise build_error(
            StatusCode.EVENTS_OPS_INVALID, reason="store requires an active handler with package metadata"
        )
    require_package_id(package_id)
    return package_id


def register(bus: ExtensionEventBusProtocol) -> None:
    """Register store's ops.build BUILTIN, binding each method to this bus."""

    async def store_get(ops: EventHandlerOps, key: str, default: Any = None) -> Any:
        """Trigger store.get for the calling package."""
        package_id = await _package_id(ops)
        with store_call(bus, package_id, ops):
            return await bus.trigger(
                Event(STORE_GET_EVENT, StoreGetInput(package_id, key, default)), ops.session_context
            )

    async def store_set(ops: EventHandlerOps, key: str, value: Any) -> bool:
        """Copy the JSON input and trigger store.set for the calling package."""
        package_id = await _package_id(ops)
        request = StoreSetInput(package_id, key, copy_json(value))
        with store_call(bus, package_id, ops):
            return cast(bool, await bus.trigger(Event(STORE_SET_EVENT, request), ops.session_context))

    async def store_delete(ops: EventHandlerOps, key: str) -> bool:
        """Trigger store.delete for the calling package."""
        package_id = await _package_id(ops)
        with store_call(bus, package_id, ops):
            return cast(
                bool,
                await bus.trigger(Event(STORE_DELETE_EVENT, StoreDeleteInput(package_id, key)), ops.session_context),
            )

    async def store_keys(ops: EventHandlerOps) -> list[str]:
        """Trigger store.keys for the calling package."""
        package_id = await _package_id(ops)
        with store_call(bus, package_id, ops):
            return cast(
                list[str], await bus.trigger(Event(STORE_KEYS_EVENT, StoreKeysInput(package_id)), ops.session_context)
            )

    async def build_store_ops(
        event: Event,
        ops: EventHandlerOps | None,
        event_next: EventNext,
    ) -> EventHandlerOpsBuilder:
        del ops
        builder = await event_next(event)
        if not isinstance(builder, EventHandlerOpsBuilder):
            raise build_error(StatusCode.EVENTS_OPS_INVALID, reason="ops.build did not return EventHandlerOpsBuilder")
        builder.add_namespace("store")
        for name, method in (("get", store_get), ("set", store_set), ("delete", store_delete), ("keys", store_keys)):
            builder.add_method("store", name, method)
        return builder

    bus.register_event_handler(OPS_BUILD_EVENT_TYPE, build_store_ops, {}, HandlerLevel.BUILTIN)
