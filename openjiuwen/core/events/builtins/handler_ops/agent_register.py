# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

# pylint: disable=duplicate-code
"""Build ``ops.agent.register`` and trigger the matching event."""

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
from openjiuwen.core.events.builtins.event_types.agent_register import (
    AGENT_REGISTER,
    AgentRegisterInput,
    AgentRegisterOutput,
    AgentRegisterValue,
    prepare_agent_register_input,
)

if TYPE_CHECKING:
    from openjiuwen.core.events.bus import ExtensionEventBus


class _AgentRegistrationOps:
    """Own the Bus reference needed to trigger ``agent.register``."""

    def __init__(self, bus: ExtensionEventBus) -> None:
        self._bus = bus

    async def register(
        self,
        ops: EventHandlerOps,
        data: AgentRegisterInput,
    ) -> AgentRegisterValue:
        """Trigger ``agent.register`` for one agent declaration."""
        if ops.session_context is None or not ops.session_context.session_id:
            raise RuntimeError("agent.register requires a session")
        prepared = prepare_agent_register_input(data)
        output = await self._bus.trigger(
            Event(AGENT_REGISTER, prepared),
            ops.session_context,
        )
        if not isinstance(output, AgentRegisterOutput):
            raise TypeError("agent.register requires AgentRegisterOutput")
        output.validate()
        if output.deny is not None:
            raise RuntimeError(output.deny)
        if output.value is None:
            raise RuntimeError("agent.register returned no value")
        return output.value

    async def build(
        self,
        event: Event,
        ops: EventHandlerOps | None,
        event_next: EventNext,
    ) -> EventHandlerOpsBuilder:
        """Add ``ops.agent.register`` to the shared Ops builder."""
        del ops
        builder = await event_next(event)
        if not isinstance(builder, EventHandlerOpsBuilder):
            raise TypeError("unexpected ops.build output")
        builder.add_namespace("agent")
        builder.add_method("agent", "register", self.register)
        return builder


def register(bus: ExtensionEventBus) -> None:
    """Register the Agent Ops builder handler."""
    registration_ops = _AgentRegistrationOps(bus)
    bus.register_event_handler(
        OPS_BUILD_EVENT_TYPE,
        registration_ops.build,
        {},
        HandlerLevel.BUILTIN,
    )
