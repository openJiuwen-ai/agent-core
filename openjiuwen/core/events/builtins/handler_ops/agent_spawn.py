# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

# pylint: disable=duplicate-code
"""Build ``ops.agent.spawn`` and trigger the matching event."""

from __future__ import annotations

from typing import TYPE_CHECKING

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import raise_error
from openjiuwen.core.events import (
    OPS_BUILD_EVENT_TYPE,
    Event,
    EventHandlerOps,
    EventHandlerOpsBuilder,
    EventNext,
    HandlerLevel,
)
from openjiuwen.core.events.builtins.event_types.agent_spawn import (
    AGENT_SPAWN,
    AgentSpawnInput,
    AgentSpawnOutput,
    AgentSpawnTriggerContext,
    prepare_agent_spawn_input,
)
from openjiuwen.core.session import get_current_session
from openjiuwen.core.session.agent import Session

if TYPE_CHECKING:
    from openjiuwen.core.events.bus import ExtensionEventBus


class _AgentSpawnOps:
    """Own the Bus reference needed to trigger ``agent.spawn``."""

    def __init__(self, bus: ExtensionEventBus) -> None:
        self._bus = bus

    async def spawn(
        self,
        ops: EventHandlerOps,
        request: AgentSpawnInput,
    ) -> AgentSpawnOutput:
        """Create a persistent subagent through the shared ``agent.spawn`` CORE."""
        session_context = ops.session_context
        if session_context is None:
            raise_error(
                StatusCode.EVENTS_STATE_INVALID,
                reason="agent.spawn requires session context",
            )
        session = get_current_session()
        if not isinstance(session, Session):
            raise_error(
                StatusCode.EVENTS_STATE_INVALID,
                reason="ops.agent.spawn requires a live session",
            )
        if session.get_session_id() != session_context.session_id:
            raise_error(
                StatusCode.EVENTS_STATE_INVALID,
                reason="ops.agent.spawn session does not match the event context",
            )

        from openjiuwen.core.single_agent.agent_runtime import get_agent_runtime
        from openjiuwen.harness.deep_agent import DeepAgent
        from openjiuwen.harness.tools.subagent._control_registry import get_subagent_control

        parent_agent = get_agent_runtime().get(session_context.session_id)
        if not isinstance(parent_agent, DeepAgent):
            raise_error(
                StatusCode.EVENTS_STATE_INVALID,
                reason="agent.spawn requires a bound DeepAgent",
            )
        context = AgentSpawnTriggerContext(
            control=get_subagent_control(parent_agent, session),
        )
        output = await self._bus.trigger(
            Event(AGENT_SPAWN, prepare_agent_spawn_input(request)),
            session,
            trigger_context=context,
        )
        if not isinstance(output, AgentSpawnOutput) or context.raw_result is None:
            raise_error(
                StatusCode.EVENTS_STATE_INVALID,
                reason="agent.spawn did not create a subagent",
            )
        return output

    async def build(
        self,
        event: Event,
        ops: EventHandlerOps | None,
        event_next: EventNext,
    ) -> EventHandlerOpsBuilder:
        """Add ``ops.agent.spawn`` to the shared Ops builder."""
        del ops
        builder = await event_next(event)
        if not isinstance(builder, EventHandlerOpsBuilder):
            raise_error(
                StatusCode.EVENTS_OPS_INVALID,
                reason="unexpected ops.build output",
            )
        builder.add_namespace("agent")
        builder.add_method("agent", "spawn", self.spawn)
        return builder


def register(bus: ExtensionEventBus) -> None:
    """Register the Agent spawn Ops builder handler."""
    spawn_ops = _AgentSpawnOps(bus)
    bus.register_event_handler(
        OPS_BUILD_EVENT_TYPE,
        spawn_ops.build,
        {},
        HandlerLevel.BUILTIN,
    )
