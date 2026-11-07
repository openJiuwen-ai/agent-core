# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Build ``ops.prompt.compose`` for explicit prompt composition requests."""

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
from openjiuwen.core.events.builtins.event_types.prompt_compose import (
    PROMPT_COMPOSE,
    PromptComposeInput,
    PromptComposeOutput,
)

if TYPE_CHECKING:
    from openjiuwen.core.events.bus import ExtensionEventBus


class _PromptComposeOps:
    """Own the Bus reference required by the explicit compose method."""

    def __init__(self, bus: ExtensionEventBus) -> None:
        self._bus = bus

    async def compose(
        self,
        ops: EventHandlerOps,
        request: PromptComposeInput,
    ) -> PromptComposeOutput:
        """Trigger one new ``prompt.compose`` chain with explicit input facts."""
        if not isinstance(request, PromptComposeInput):
            raise_error(StatusCode.EVENTS_PAYLOAD_INVALID, reason="ops.prompt.compose requires PromptComposeInput")
        session_context = ops.session_context
        if session_context is None or not session_context.session_id:
            raise_error(StatusCode.EVENTS_STATE_INVALID, reason="ops.prompt.compose requires a session context")

        output = await self._bus.trigger(Event(PROMPT_COMPOSE, request), session_context)
        if not isinstance(output, PromptComposeOutput):
            raise_error(StatusCode.EVENTS_PAYLOAD_INVALID, reason="ops.prompt.compose returned an invalid output")
        return output

    async def build(
        self,
        event: Event,
        ops: EventHandlerOps | None,
        event_next: EventNext,
    ) -> EventHandlerOpsBuilder:
        """Add ``ops.prompt.compose`` to the shared Ops builder."""
        del ops
        builder = await event_next(event)
        if not isinstance(builder, EventHandlerOpsBuilder):
            raise_error(StatusCode.EVENTS_OPS_INVALID, reason="unexpected ops.build output")
        builder.add_namespace("prompt")
        builder.add_method("prompt", "compose", self.compose)
        return builder


def register(bus: ExtensionEventBus) -> None:
    """Register the prompt Ops Build handler."""
    prompt_ops = _PromptComposeOps(bus)
    bus.register_event_handler(
        OPS_BUILD_EVENT_TYPE,
        prompt_ops.build,
        {},
        HandlerLevel.BUILTIN,
    )
