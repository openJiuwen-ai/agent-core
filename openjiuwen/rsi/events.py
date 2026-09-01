# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Shared engine event contracts for RSI scenarios.

Harness and artifact engines use the same callback boundary.  The event
payloads intentionally contain complete snapshots so AgentServer can project
them to the common RSI transport without rebuilding scenario-specific data.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Literal, TypeAlias

from openjiuwen.rsi.schema import RsiStatus, RsiTreeNode, RsiUsage


@dataclass(frozen=True, slots=True)
class EventStatus:
    """A persisted task status transition."""

    status: RsiStatus
    event_type: Literal["status"] = field(default="status", init=False)


@dataclass(frozen=True, slots=True)
class EventProgress:
    """A persisted cumulative progress snapshot."""

    iteration: int
    total_iterations: int
    score: float | None
    baseline: float | None
    usage: RsiUsage | None
    event_type: Literal["progress"] = field(default="progress", init=False)


@dataclass(frozen=True, slots=True)
class EventNode:
    """A newly persisted complete tree node."""

    node: RsiTreeNode
    event_type: Literal["node"] = field(default="node", init=False)


EngineEvent: TypeAlias = EventStatus | EventProgress | EventNode
OnEvent: TypeAlias = Callable[[EngineEvent], Awaitable[None]]

# Name used by the cross-scenario engine adapter design.  Keep ``OnEvent`` as
# the concise provider-facing alias used by the original artifact contract.
EngineEventSink: TypeAlias = OnEvent


async def emit(on_event: OnEvent | None, event: EngineEvent) -> None:
    """Await an injected event callback when one is configured.

    Engines should call this only after the state, node, report, or artifact
    referenced by ``event`` has been durably persisted.  Callback exceptions
    are intentionally not swallowed: the AgentServer owns the observation
    channel and decides how to record or compensate delivery failures.
    """

    if on_event is not None:
        await on_event(event)


__all__ = [
    "EngineEvent",
    "EngineEventSink",
    "EventNode",
    "EventProgress",
    "EventStatus",
    "OnEvent",
    "emit",
]
