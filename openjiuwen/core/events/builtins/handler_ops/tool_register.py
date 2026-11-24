# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

# pylint: disable=duplicate-code
"""Build ``ops.tool.register`` and trigger the executable-tool event."""

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
from openjiuwen.core.events.builtins.event_types.tool_register import (
    TOOL_REGISTER,
    ToolRegisterInput,
    ToolRegisterOutput,
    ToolRegisterSpec,
    ToolRegisterValue,
    build_tool_register_input,
)

if TYPE_CHECKING:
    from openjiuwen.core.events.bus import ExtensionEventBus


class _ToolRegistrationOps:
    """Own the Bus reference needed to trigger ``tool.register``."""

    def __init__(self, bus: ExtensionEventBus) -> None:
        self._bus = bus

    async def register(
        self,
        ops: EventHandlerOps,
        spec: ToolRegisterSpec,
    ) -> ToolRegisterValue:
        """Trigger ``tool.register`` for one executable tool declaration."""
        if ops.session_context is None or not ops.session_context.session_id:
            raise RuntimeError("tool.register requires a session")
        if not isinstance(spec, ToolRegisterSpec):
            raise TypeError("tool.register requires ToolRegisterSpec")
        prepared = build_tool_register_input(spec)
        output = await self._bus.trigger(
            Event(TOOL_REGISTER, prepared),
            ops.session_context,
        )
        if not isinstance(output, ToolRegisterOutput):
            raise TypeError("tool.register requires ToolRegisterOutput")
        output.validate()
        if output.deny is not None:
            raise RuntimeError(output.deny)
        if output.value is None:
            raise RuntimeError("tool.register returned no value")
        return output.value

    async def build(
        self,
        event: Event,
        ops: EventHandlerOps | None,
        event_next: EventNext,
    ) -> EventHandlerOpsBuilder:
        """Add ``ops.tool.register`` to the shared Ops builder."""
        del ops
        builder = await event_next(event)
        if not isinstance(builder, EventHandlerOpsBuilder):
            raise TypeError("unexpected ops.build output")
        builder.add_namespace("tool")
        builder.add_method("tool", "register", self.register)
        return builder


def register(bus: ExtensionEventBus) -> None:
    """Register the Tool Ops builder handler."""
    registration_ops = _ToolRegistrationOps(bus)
    bus.register_event_handler(
        OPS_BUILD_EVENT_TYPE,
        registration_ops.build,
        {},
        HandlerLevel.BUILTIN,
    )
