# coding: utf-8
"""Tests for the coordination event-bus wake-up pattern."""
from __future__ import annotations

import asyncio

import pytest

from openjiuwen.agent_teams.agent.coordination import (
    CoordinationEvent,
    EventBus,
)
from openjiuwen.agent_teams.schema.team import TeamRole
from openjiuwen.agent_teams.schema.events import (
    EventMessage,
    TeamEvent,
)


@pytest.mark.asyncio
async def test_message_event_wakes_loop():
    """MESSAGE event triggers wake_callback."""
    woke: list[CoordinationEvent] = []

    async def on_wake(event: CoordinationEvent) -> None:
        woke.append(event)

    bus = EventBus(role=TeamRole.LEADER)
    await bus.start(wake_callback=on_wake)

    event = EventMessage(
        event_type=TeamEvent.MESSAGE,
        payload={"content": "hello"},
    )
    await bus.enqueue(event)
    await asyncio.sleep(0.05)
    await bus.stop()

    assert len(woke) == 1
    assert woke[0].event_type == TeamEvent.MESSAGE


@pytest.mark.asyncio
async def test_task_event_wakes_loop():
    """TASK_COMPLETED event triggers wake_callback."""
    woke: list[CoordinationEvent] = []

    async def on_wake(event: CoordinationEvent) -> None:
        woke.append(event)

    bus = EventBus(role=TeamRole.TEAMMATE)
    await bus.start(wake_callback=on_wake)

    event = EventMessage(
        event_type=TeamEvent.TASK_COMPLETED,
        payload={"task_id": "t1"},
    )
    await bus.enqueue(event)
    await asyncio.sleep(0.05)
    await bus.stop()

    assert len(woke) == 1
    assert woke[0].event_type == TeamEvent.TASK_COMPLETED


@pytest.mark.asyncio
async def test_multiple_events_wake_in_order():
    """Events are processed FIFO."""
    woke: list[CoordinationEvent] = []

    async def on_wake(event: CoordinationEvent) -> None:
        woke.append(event)

    bus = EventBus(role=TeamRole.LEADER)
    await bus.start(wake_callback=on_wake)

    for et in [
        TeamEvent.MESSAGE,
        TeamEvent.TASK_COMPLETED,
        TeamEvent.BROADCAST,
    ]:
        await bus.enqueue(
            EventMessage(event_type=et, payload={}),
        )

    await asyncio.sleep(0.1)
    await bus.stop()

    assert [e.event_type for e in woke] == [
        TeamEvent.MESSAGE,
        TeamEvent.TASK_COMPLETED,
        TeamEvent.BROADCAST,
    ]


@pytest.mark.asyncio
async def test_no_callback_does_not_crash():
    """Bus without callback still processes events."""
    bus = EventBus(role=TeamRole.LEADER)
    await bus.start()

    await bus.enqueue(
        EventMessage(event_type=TeamEvent.MESSAGE, payload={}),
    )
    await asyncio.sleep(0.05)
    await bus.stop()
