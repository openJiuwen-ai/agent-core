# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Minimal consumer stub for the public event bus protocol."""

from __future__ import annotations

from typing import TYPE_CHECKING

from openjiuwen.core.events import (
    Event,
    EventHandler,
    EventOutput,
    EventType,
    ExtensionEventBusProtocol,
    HandlerId,
    HandlerLevel,
    MatchPattern,
    MatchPatternFunc,
    SessionContext,
)

if TYPE_CHECKING:
    from openjiuwen.core.single_agent import Session


class ConsumerEventBus:
    """Implements only the five public bus methods."""

    def register_event_type(
        self,
        event_type: EventType,
        match_pattern_func: MatchPatternFunc,
        core_handler: EventHandler,
    ) -> None:
        return None

    def unregister_event_type(self, event_type: EventType) -> None:
        return None

    def register_event_handler(
        self,
        event_type: EventType,
        handler: EventHandler,
        match_pattern: MatchPattern,
        handler_level: HandlerLevel = HandlerLevel.USER,
    ) -> HandlerId:
        return ""

    def unregister_event_handler(self, handler_id: HandlerId) -> None:
        return None

    async def trigger(
        self,
        event: Event,
        session: Session | SessionContext | None = None,
    ) -> EventOutput | None:
        return None


def consumer_bus() -> ExtensionEventBusProtocol:
    return ConsumerEventBus()
