# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

# pylint: disable=duplicate-code
"""Build ``ops.mcp.register`` and trigger the MCP-server event."""

from __future__ import annotations

from typing import TYPE_CHECKING

from openjiuwen.core.events import (
    OPS_BUILD_EVENT_TYPE,
    Event,
    EventHandlerOps,
    EventHandlerOpsBuilder,
    EventNext,
    HandlerLevel,
)
from openjiuwen.core.events.builtins.event_types.mcp_register import (
    MCP_REGISTER,
    McpRegisterOutput,
    McpRegisterSpec,
    McpRegisterValue,
    build_mcp_register_input,
)

if TYPE_CHECKING:
    from openjiuwen.core.events.bus import ExtensionEventBus


class _McpRegistrationOps:
    """Own the Bus reference needed to trigger ``mcp.register``."""

    def __init__(self, bus: ExtensionEventBus) -> None:
        self._bus = bus

    async def register(
        self,
        ops: EventHandlerOps,
        spec: McpRegisterSpec,
    ) -> McpRegisterValue:
        """Trigger ``mcp.register`` for one pure-data server declaration."""
        if ops.session_context is None or not ops.session_context.session_id:
            raise RuntimeError("mcp.register requires a session")
        prepared = build_mcp_register_input(spec)
        output = await self._bus.trigger(
            Event(MCP_REGISTER, prepared),
            ops.session_context,
        )
        if not isinstance(output, McpRegisterOutput):
            raise TypeError("mcp.register requires McpRegisterOutput")
        output.validate()
        if output.deny is not None:
            raise RuntimeError(output.deny)
        assert output.value is not None
        return output.value

    async def build(
        self,
        event: Event,
        ops: EventHandlerOps | None,
        event_next: EventNext,
    ) -> EventHandlerOpsBuilder:
        """Add ``ops.mcp.register`` to the shared Ops builder."""
        del ops
        builder = await event_next(event)
        if not isinstance(builder, EventHandlerOpsBuilder):
            raise TypeError("unexpected ops.build output")
        builder.add_namespace("mcp")
        builder.add_method("mcp", "register", self.register)
        return builder


def register(bus: ExtensionEventBus) -> None:
    """Register the MCP Ops builder handler."""
    registration_ops = _McpRegistrationOps(bus)
    bus.register_event_handler(
        OPS_BUILD_EVENT_TYPE,
        registration_ops.build,
        {},
        HandlerLevel.BUILTIN,
    )
