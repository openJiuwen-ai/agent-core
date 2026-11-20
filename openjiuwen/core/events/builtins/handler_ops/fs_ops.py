# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Inject the ``ops.fs`` namespace during ``ops.build``."""

from __future__ import annotations

from typing import TYPE_CHECKING

from openjiuwen.core.events.event_next import EventNext
from openjiuwen.core.events.ops import EventHandlerOps, EventHandlerOpsBuilder
from openjiuwen.core.events.ops_impl.fs.facade import FsOpsFacade
from openjiuwen.core.events.types import OPS_BUILD_EVENT_TYPE, Event, HandlerLevel

if TYPE_CHECKING:
    from openjiuwen.core.events.bus import ExtensionEventBus

FS_NAMESPACE = "fs"
FS_METHODS = ("read", "write", "list", "stat", "exists", "ancestors")


def _make_method(bus: ExtensionEventBus, name: str) -> object:
    """Build one ops method that triggers the matching ``fs.*`` event."""

    async def method(ops: EventHandlerOps, request: object) -> object:
        return await getattr(FsOpsFacade.from_ops(bus, ops), name)(request)

    return method


def register(bus: ExtensionEventBus) -> None:
    """Register the BUILTIN handler that adds the ``fs`` namespace."""

    async def fs_ops_build(
        event: Event,
        ops: EventHandlerOps | None,
        event_next: EventNext,
    ) -> EventHandlerOpsBuilder:
        builder = await event_next(event)
        if not isinstance(builder, EventHandlerOpsBuilder):
            raise TypeError("ops.build must return EventHandlerOpsBuilder")
        builder.add_namespace(FS_NAMESPACE)
        for name in FS_METHODS:
            builder.add_method(FS_NAMESPACE, name, _make_method(bus, name))
        return builder

    bus.register_event_handler(OPS_BUILD_EVENT_TYPE, fs_ops_build, {}, HandlerLevel.BUILTIN)