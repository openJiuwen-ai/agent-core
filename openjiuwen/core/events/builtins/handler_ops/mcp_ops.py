# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Inject the ``ops.mcp`` namespace during ``ops.build``."""

from __future__ import annotations

from typing import TYPE_CHECKING

from openjiuwen.core.events.event_next import EventNext
from openjiuwen.core.events.ops import EventHandlerOps, EventHandlerOpsBuilder
from openjiuwen.core.events.ops_impl.mcp.facade import McpOpsFacade
from openjiuwen.core.events.ops_impl.mcp.models import McpCallInput, McpConnectInput, McpRegisterSpec
from openjiuwen.core.events.types import OPS_BUILD_EVENT_TYPE, Event, HandlerLevel

if TYPE_CHECKING:
    from openjiuwen.core.events.bus import ExtensionEventBus

MCP_NAMESPACE = "mcp"


def register(bus: ExtensionEventBus) -> None:
    """Register the BUILTIN handler that adds the ``mcp`` namespace."""

    async def mcp_call(ops: EventHandlerOps, request: McpCallInput) -> object:
        return await McpOpsFacade.from_ops(bus, ops).call(request)

    async def mcp_connect(ops: EventHandlerOps, request: McpConnectInput) -> object:
        return await McpOpsFacade.from_ops(bus, ops).connect(request)

    async def mcp_register(ops: EventHandlerOps, spec: McpRegisterSpec) -> object:
        return await McpOpsFacade.from_ops(bus, ops).register(spec)

    async def mcp_ops_build(
        event: Event,
        ops: EventHandlerOps | None,
        event_next: EventNext,
    ) -> EventHandlerOpsBuilder:
        builder = await event_next(event)
        if not isinstance(builder, EventHandlerOpsBuilder):
            raise TypeError("ops.build must return EventHandlerOpsBuilder")
        builder.add_namespace(MCP_NAMESPACE)
        builder.add_method(MCP_NAMESPACE, "call", mcp_call)
        builder.add_method(MCP_NAMESPACE, "connect", mcp_connect)
        builder.add_method(MCP_NAMESPACE, "register", mcp_register)
        return builder

    bus.register_event_handler(OPS_BUILD_EVENT_TYPE, mcp_ops_build, {}, HandlerLevel.BUILTIN)
