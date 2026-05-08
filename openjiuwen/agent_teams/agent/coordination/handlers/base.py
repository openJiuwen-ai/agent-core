# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Base class for scenario-scoped coordination handlers.

Each subclass declares its own ``EVENT_METHOD_MAP`` and exposes bound
callbacks via ``get_callbacks()``. Mirrors the rails convention from
``core/single_agent/rail/base.py:AgentRail``: a declarative
``event_key -> method_name`` table plus a ``get_callbacks()`` helper
that returns the bound-method dict for framework registration.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Awaitable, Callable, ClassVar

from openjiuwen.agent_teams.agent.coordination.event_bus import CoordinationEvent

if TYPE_CHECKING:
    from openjiuwen.agent_teams.agent.coordination.dispatcher import DispatcherHost

EventCallback = Callable[[CoordinationEvent], Awaitable[None]]


class BaseCoordinationHandler:
    """Base class for scenario-scoped coordination event handlers.

    Subclasses:
        - declare ``EVENT_METHOD_MAP`` mapping ``event_key -> method_name``
        - implement the corresponding ``async`` methods
        - share read-only access to the ``DispatcherHost`` via ``self._host``

    Multiple handlers may register the same ``event_key`` — the
    framework fans out callbacks in registration order, so handlers
    stay decoupled and never call each other directly.
    """

    EVENT_METHOD_MAP: ClassVar[dict[str, str]] = {}

    def __init__(self, host: "DispatcherHost") -> None:
        self._host = host

    def get_callbacks(self) -> dict[str, EventCallback]:
        """Return ``event_key -> bound method`` for framework registration."""
        return {event_key: getattr(self, method_name) for event_key, method_name in self.EVENT_METHOD_MAP.items()}
