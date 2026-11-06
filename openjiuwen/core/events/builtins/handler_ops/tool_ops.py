# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Build ``ops.tool.call`` and ``ops.tool.check`` for the session's active DeepAgent."""

from __future__ import annotations

import uuid
from contextvars import ContextVar
from typing import TYPE_CHECKING

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import raise_error
from openjiuwen.core.events.builtins.event_types.tool_call import (
    TOOL_CALL,
    ToolCallEventInput,
    ToolCallEventOutput,
    ToolCallTriggerContext,
    check_output_identity,
    serialize_arguments,
)
from openjiuwen.core.events.builtins.event_types.tool_check import (
    TOOL_CHECK,
    ToolCheckInput,
    ToolCheckOutput,
    ToolCheckTriggerContext,
)
from openjiuwen.core.events.event_next import EventNext
from openjiuwen.core.events.ops import EventHandlerOps, EventHandlerOpsBuilder
from openjiuwen.core.events.types import OPS_BUILD_EVENT_TYPE, Event, HandlerLevel
from openjiuwen.core.foundation.llm import ToolCall, ToolMessage
from openjiuwen.core.session import get_current_session
from openjiuwen.core.session.agent import Session
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext, ToolCallInputs

if TYPE_CHECKING:
    from openjiuwen.core.events.bus import ExtensionEventBus
    from openjiuwen.harness.deep_agent import DeepAgent

_TOOL_CHECK_ACTIVE: ContextVar[bool] = ContextVar("openjiuwen_ops_tool_check_active", default=False)


class _ToolOps:
    """Own the Bus reference needed to trigger ``tool.call`` and ``tool.check``."""

    def __init__(self, bus: ExtensionEventBus) -> None:
        self._bus = bus

    async def call(self, ops: EventHandlerOps, tool_name: str, arguments: object) -> ToolCallEventOutput:
        """Run one tool through a single-item ``tool.call`` batch and return its public output."""
        _, ctx, tool_call = await self._root_call(ops, tool_name, arguments)
        session_context = ops.session_context

        async def invoke_original(tool_calls: list[ToolCall]) -> list[tuple[object, ToolMessage | None]]:
            session = ctx.session or get_current_session()
            if not isinstance(session, Session):
                raise_error(
                    StatusCode.EVENTS_STATE_INVALID,
                    reason="ops.tool.call requires a live session",
                )
            if session_context is None or session.get_session_id() != session_context.session_id:
                raise_error(
                    StatusCode.EVENTS_STATE_INVALID,
                    reason="ops.tool.call session does not match the event context",
                )
            return await ctx.agent.ability_manager.execute(
                ctx=ctx,
                tool_call=tool_calls,
                session=session,
                parallel_tool_calls=ctx.agent.config.parallel_tool_calls,
            )

        context = ToolCallTriggerContext(
            tool_calls=(tool_call,),
            invoke_original=invoke_original,
        )
        event_input = (
            ToolCallEventInput(tool_call_id=tool_call.id, tool_name=tool_call.name, arguments=tool_call.arguments),
        )
        outputs = await self._bus.trigger(
            Event(TOOL_CALL, event_input),
            ops.session_context,
            trigger_context=context,
        )
        check_output_identity(outputs, context.tool_calls)
        return outputs[0]

    async def check(self, ops: EventHandlerOps, tool_name: str, arguments: object) -> ToolCheckOutput:
        """Run one side-effect-free permission query through ``tool.check``."""
        from openjiuwen.harness.rails.security.tool_security_rail import PermissionInterruptRail

        if _TOOL_CHECK_ACTIVE.get():
            raise_error(StatusCode.EVENTS_STATE_INVALID, reason="ops.tool.check cannot be called inside ops.tool.check")
        token = _TOOL_CHECK_ACTIVE.set(True)
        try:
            agent, ctx, _ = await self._root_call(ops, tool_name, arguments)
            rails = agent.find_rails_by_type((PermissionInterruptRail,))
            if rails:
                invoke_original = rails[0].execute_tool_check
            else:
                async def invoke_original(
                    _ctx: AgentCallbackContext,
                ) -> tuple[None, ToolCheckOutput]:
                    return None, ToolCheckOutput(decision="allow")

            event_input = ToolCheckInput(tool_call_id=None, tool_name=tool_name)
            trigger_context = ToolCheckTriggerContext(
                callback_ctx=ctx,
                invoke_original=invoke_original,
            )
            return await self._bus.trigger(
                Event(TOOL_CHECK, event_input),
                ops.session_context,
                trigger_context=trigger_context,
            )
        finally:
            _TOOL_CHECK_ACTIVE.reset(token)

    @staticmethod
    async def _root_call(
        ops: EventHandlerOps, tool_name: str, arguments: object
    ) -> tuple[DeepAgent, AgentCallbackContext, ToolCall]:
        from openjiuwen.core.single_agent.agent_runtime import get_agent_runtime
        from openjiuwen.harness.deep_agent import DeepAgent

        session_context = ops.session_context
        if session_context is None:
            raise_error(StatusCode.EVENTS_OPS_INVALID, reason="ops.tool requires a session context")
        agent = get_agent_runtime().get(session_context.session_id)
        if not isinstance(agent, DeepAgent):
            raise_error(StatusCode.EVENTS_STATE_INVALID, reason="ops.tool requires a DeepAgent bound to the session")
        react_agent = agent.react_agent
        if react_agent is None:
            raise_error(StatusCode.EVENTS_STATE_INVALID, reason="ops.tool requires an initialized ReActAgent")
        tool_call = ToolCall(
            id=f"ops_tool_{uuid.uuid4().hex}",
            type="function",
            name=tool_name,
            arguments=serialize_arguments(arguments),
        )
        callback_ctx = AgentCallbackContext(
            agent=react_agent,
            inputs=ToolCallInputs(
                tool_call=tool_call,
                tool_name=tool_name,
                tool_args=arguments,
            ),
            config=react_agent.config,
            session=None,
            context=None,
            extra={"session_id": session_context.session_id},
        )
        return agent, callback_ctx, tool_call

    async def build(
        self,
        event: Event,
        ops: EventHandlerOps | None,
        event_next: EventNext,
    ) -> EventHandlerOpsBuilder:
        """Add ``ops.tool.call`` and ``ops.tool.check`` to the shared Ops builder."""
        del ops
        builder = await event_next(event)
        if not isinstance(builder, EventHandlerOpsBuilder):
            raise_error(StatusCode.EVENTS_OPS_INVALID, reason="unexpected ops.build output")
        builder.add_namespace("tool")
        builder.add_method("tool", "call", self.call)
        builder.add_method("tool", "check", self.check)
        return builder


def register(bus: ExtensionEventBus) -> None:
    """Register the Tool call/check Ops builder handler."""
    tool_ops = _ToolOps(bus)
    bus.register_event_handler(
        OPS_BUILD_EVENT_TYPE,
        tool_ops.build,
        {},
        HandlerLevel.BUILTIN,
    )
