# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Shared engine event contracts for RSI scenarios.

Harness and artifact engines use the same callback boundary. Snapshot events
contain complete persisted data, while ``NodeStageEvent`` is a pass-through
notification for a subprocess transition inside an existing node.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Literal, TypeAlias

from openjiuwen.rsi.schema import RsiModelCall, RsiStatus, RsiTreeNode, RsiUsage


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
    """A persisted complete tree-node snapshot."""

    node: RsiTreeNode
    artifacts: list[dict[str, str]] = field(default_factory=list)
    event_type: Literal["node"] = field(default="node", init=False)


@dataclass(frozen=True, slots=True)
class EventUsage:
    """Per-call raw usage delta (not a cumulative progress snapshot).

    ``call_id`` remains stable in the local ledger for service-side deduplication.
    Existing event variants retain their wire shape.
    """

    event_id: int
    task_id: str
    ts: str
    call_id: str
    model_call: RsiModelCall
    node_ref: str | None = None
    stage_ref: str | None = None
    family: Literal["progress"] = field(default="progress", init=False)
    kind: Literal["usage"] = field(default="usage", init=False)
    event_type: Literal["progress.usage"] = field(default="progress.usage", init=False)


@dataclass(frozen=True, slots=True)
class NodeStageEvent:
    """A subprocess-stage transition for an existing tree node.

    ``stage`` is intentionally a JSON-like object. The engine owns its stage
    taxonomy and the AgentServer forwards the values without interpreting
    them.
    """

    node_ref: str
    stage: dict[str, Any]
    note: str | None = None
    event_type: Literal["node.stage"] = field(default="node.stage", init=False)


# Keep the Event* naming convention available to callers that use the current
# artifact event names; the canonical name follows the end-to-end event
# contract.
EventNodeStage: TypeAlias = NodeStageEvent


EngineEvent: TypeAlias = EventStatus | EventProgress | EventNode | NodeStageEvent | EventUsage
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
    "EventNodeStage",
    "EventProgress",
    "EventStatus",
    "EventUsage",
    "NodeStageEvent",
    "OnEvent",
    "emit",
]
