# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""TeamMonitorEventBridge: queueing, consumption, dropping and lifecycle."""
from __future__ import annotations

import asyncio
from typing import Any

import pytest

from openjiuwen.agent_teams.extensions.monitor_event_bridge import TeamMonitorEventBridge
from openjiuwen.agent_teams.monitor.models import MonitorEventType
from openjiuwen.agent_teams.schema.events import EventMessage


def _task_event(task_id: str) -> EventMessage:
    """Build a minimal public task event accepted by MonitorEvent."""
    return EventMessage(
        event_type=MonitorEventType.TASK_CREATED.value,
        payload={"team_name": "research-team", "task_id": task_id},
        sender_id="leader",
    )


def _internal_event() -> EventMessage:
    """Build an event whose type is not part of the public monitor surface."""
    return EventMessage(
        event_type="plan_approval",
        payload={"team_name": "research-team"},
        sender_id="leader",
    )


class _Collector:
    """Dispatcher double that records monitor events and optional failures."""

    def __init__(self, *, fail_on: str | None = None, delay: float = 0.0) -> None:
        self.received: list[tuple[MonitorEventType, str, str]] = []
        self._fail_on = fail_on
        self._delay = delay

    async def __call__(self, event: Any, session_id: str) -> None:
        if self._delay:
            await asyncio.sleep(self._delay)
        if self._fail_on is not None and event.task_id == self._fail_on:
            raise RuntimeError("dispatcher boom")
        self.received.append((event.event_type, event.task_id, session_id))


@pytest.mark.asyncio
async def test_bridge_dispatches_public_events_in_order() -> None:
    """Public events reach the dispatcher in arrival order with the session id."""
    collector = _Collector()
    bridge = TeamMonitorEventBridge(team_name="research-team", session_id="session-1", dispatcher=collector)
    await bridge.start()
    for task_id in ("t1", "t2", "t3"):
        await bridge.on_internal_event(_task_event(task_id))
    await asyncio.sleep(0.05)
    await bridge.stop()

    assert collector.received == [
        (MonitorEventType.TASK_CREATED, "t1", "session-1"),
        (MonitorEventType.TASK_CREATED, "t2", "session-1"),
        (MonitorEventType.TASK_CREATED, "t3", "session-1"),
    ]
    assert bridge.dropped_count == 0


@pytest.mark.asyncio
async def test_bridge_drops_internal_event_types() -> None:
    """Event types outside the public monitor surface never reach the queue."""
    collector = _Collector()
    bridge = TeamMonitorEventBridge(team_name="research-team", session_id="session-1", dispatcher=collector)
    await bridge.start()
    await bridge.on_internal_event(_internal_event())
    await asyncio.sleep(0.05)
    await bridge.stop()

    assert collector.received == []
    assert bridge.dropped_count == 0


@pytest.mark.asyncio
async def test_dispatcher_failure_continues_next_monitor_event() -> None:
    """One failing dispatcher call is logged over, not a queue stall."""
    collector = _Collector(fail_on="t1")
    bridge = TeamMonitorEventBridge(team_name="research-team", session_id="session-1", dispatcher=collector)
    await bridge.start()
    await bridge.on_internal_event(_task_event("t1"))
    await bridge.on_internal_event(_task_event("t2"))
    await asyncio.sleep(0.05)
    await bridge.stop()

    assert [task_id for _, task_id, _ in collector.received] == ["t2"]


@pytest.mark.asyncio
async def test_full_queue_drops_new_events_and_counts() -> None:
    """Enqueueing never blocks; overflow drops with a counter (no consumer)."""
    bridge = TeamMonitorEventBridge(team_name="research-team", session_id="session-1", capacity=2)
    await bridge.on_internal_event(_task_event("t1"))
    await bridge.on_internal_event(_task_event("t2"))
    await bridge.on_internal_event(_task_event("t3"))

    assert bridge.dropped_count == 1


@pytest.mark.asyncio
async def test_stop_counts_backlog_and_cancels_consumer() -> None:
    """Stop cancels a slow consumer and tallies unprocessed monitor events."""
    collector = _Collector(delay=0.2)
    bridge = TeamMonitorEventBridge(team_name="research-team", session_id="session-1", dispatcher=collector)
    await bridge.start()
    await bridge.on_internal_event(_task_event("t1"))
    await bridge.on_internal_event(_task_event("t2"))
    await bridge.stop()

    assert not bridge.is_running
    assert bridge.dropped_count == 2
    assert collector.received == []


@pytest.mark.level1
@pytest.mark.asyncio
async def test_stop_propagates_cancellation_of_stop_itself() -> None:
    """A cancel injected into stop() propagates instead of being swallowed.

    Only the consumer's own cancellation (requested inside stop) may be
    absorbed; a cancel aimed at stop() itself belongs to its caller.
    """
    collector = _Collector(delay=0.05)
    bridge = TeamMonitorEventBridge(team_name="research-team", session_id="session-1", dispatcher=collector)
    await bridge.start()
    await bridge.on_internal_event(_task_event("t1"))

    stop_task = asyncio.create_task(bridge.stop())
    await asyncio.sleep(0)  # let stop() reach its await on the consumer
    stop_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await stop_task
    # Let the already-cancelled consumer settle so the loop closes clean.
    await asyncio.sleep(0.05)


@pytest.mark.asyncio
async def test_stop_rejects_new_events() -> None:
    """After stop the bridge no longer accepts monitor events."""
    collector = _Collector()
    bridge = TeamMonitorEventBridge(team_name="research-team", session_id="session-1", dispatcher=collector)
    await bridge.start()
    await bridge.stop()

    await bridge.on_internal_event(_task_event("t1"))
    await asyncio.sleep(0.02)

    assert collector.received == []
    assert bridge.dropped_count == 0


@pytest.mark.asyncio
async def test_start_is_idempotent() -> None:
    """Repeated start calls neither duplicate the consumer nor break stop."""
    collector = _Collector()
    bridge = TeamMonitorEventBridge(team_name="research-team", session_id="session-1", dispatcher=collector)
    await bridge.start()
    await bridge.start()
    assert bridge.is_running

    await bridge.stop()
    assert not bridge.is_running
    # A start after stop stays a no-op (the bridge never replays sessions).
    await bridge.start()
    assert not bridge.is_running


@pytest.mark.asyncio
async def test_no_dispatcher_discards_without_dropping() -> None:
    """Without a dispatcher the bridge discards quietly; drops stay at zero."""
    bridge = TeamMonitorEventBridge(team_name="research-team", session_id="session-1")
    await bridge.start()
    await bridge.on_internal_event(_task_event("t1"))
    await asyncio.sleep(0.05)
    await bridge.stop()

    assert bridge.dropped_count == 0
