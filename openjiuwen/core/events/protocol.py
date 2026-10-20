# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Protocols a consumer can implement before an EventBus exists."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Protocol, TypeAlias

from openjiuwen.core.events.types import (
    Event,
    EventOutput,
    EventType,
    HandlerId,
    HandlerLevel,
    MatchPattern,
    MatchPatternFunc,
    OpsMethod,
    PackageMetadata,
    SessionContext,
)

if TYPE_CHECKING:
    from openjiuwen.core.single_agent import Session


class EventNext(Protocol):
    """Advances the current handler chain once."""

    async def __call__(self, event: Event) -> EventOutput | None: ...

    async def to(
        self,
        event: Event,
        handler_level: HandlerLevel,
    ) -> EventOutput | None: ...


class EventHandlerOps(Protocol):
    """Read-only package and session facts for one handler invocation."""

    @property
    def package_metadata(self) -> PackageMetadata | None: ...

    @property
    def session_context(self) -> SessionContext | None: ...


EventHandler: TypeAlias = Callable[
    [Event, EventHandlerOps | None, EventNext],
    Awaitable[EventOutput | None],
]


class EventHandlerOpsBuilder(Protocol):
    """Declares namespaces and methods, then builds one ops view."""

    def add_namespace(self, namespace: str) -> None: ...

    def add_method(
        self,
        namespace: str,
        method_name: str,
        method: OpsMethod,
    ) -> None: ...

    def build(
        self,
        package_metadata: PackageMetadata | None = None,
        session_context: SessionContext | None = None,
    ) -> EventHandlerOps: ...


class ExtensionEventBusProtocol(Protocol):
    """Registration and trigger surface for an extension event bus."""

    def register_event_type(
        self,
        event_type: EventType,
        match_pattern_func: MatchPatternFunc,
        core_handler: EventHandler,
    ) -> None: ...

    def unregister_event_type(self, event_type: EventType) -> None: ...

    def register_event_handler(
        self,
        event_type: EventType,
        handler: EventHandler,
        match_pattern: MatchPattern,
        handler_level: HandlerLevel = HandlerLevel.USER,
    ) -> HandlerId: ...

    def unregister_event_handler(self, handler_id: HandlerId) -> None: ...

    async def trigger(
        self,
        event: Event,
        session: Session | SessionContext | None = None,
    ) -> EventOutput | None: ...
