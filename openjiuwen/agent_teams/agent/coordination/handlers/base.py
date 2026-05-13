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

from openjiuwen.agent_teams.agent.blueprint import TeamAgentBlueprint
from openjiuwen.agent_teams.agent.coordination.event_bus import CoordinationEvent
from openjiuwen.agent_teams.agent.infra import TeamInfra

if TYPE_CHECKING:
    from openjiuwen.agent_teams.agent.coordination.dispatcher import (
        AgentRoundController,
        DispatcherHost,
        PollController,
        TeamLifecycleController,
    )

EventCallback = Callable[[CoordinationEvent], Awaitable[None]]


class BaseCoordinationHandler:
    """Base class for scenario-scoped coordination event handlers.

    Subclasses:
        - declare ``EVENT_METHOD_MAP`` mapping ``event_key -> method_name``
        - implement the corresponding ``async`` methods
        - read static config / per-process infra directly via
          ``self._blueprint`` / ``self._infra``
        - drive the round through ``self._round``, trigger lifecycle
          effects through ``self._lifecycle``, and toggle the
          coordination poll timers through ``self._poll``

    Multiple handlers may register the same ``event_key`` — the
    framework fans out callbacks in registration order, so handlers
    stay decoupled and never call each other directly.
    """

    EVENT_METHOD_MAP: ClassVar[dict[str, str]] = {}

    def __init__(
        self,
        host: "DispatcherHost",
        blueprint: TeamAgentBlueprint,
        infra: TeamInfra,
        poll_ctrl: "PollController",
    ) -> None:
        # ``host`` satisfies both AgentRoundController and
        # TeamLifecycleController (it is the owning TeamAgent);
        # ``poll_ctrl`` is the coordination event bus. Aliasing under
        # narrower protocol-typed fields documents which surface each
        # call site actually depends on — handlers must not reach for
        # ``host`` directly.
        self._round: "AgentRoundController" = host
        self._lifecycle: "TeamLifecycleController" = host
        self._poll = poll_ctrl
        self._blueprint = blueprint
        self._infra = infra

    def get_callbacks(self) -> dict[str, EventCallback]:
        """Return ``event_key -> bound method`` for framework registration."""
        return {event_key: getattr(self, method_name) for event_key, method_name in self.EVENT_METHOD_MAP.items()}
