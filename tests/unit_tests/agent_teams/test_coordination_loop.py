# coding: utf-8
"""Tests for the coordination loop wake-up pattern."""
from __future__ import annotations

import asyncio

import pytest

from openjiuwen.agent_teams.agent.coordinator import (
    CoordinationEvent,
    CoordinatorLoop,
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

    loop = CoordinatorLoop(
        role=TeamRole.LEADER,
        wake_callback=on_wake,
    )
    await loop.start()

    event = EventMessage(
        event_type=TeamEvent.MESSAGE,
        payload={"content": "hello"},
    )
    await loop.enqueue(event)
    await asyncio.sleep(0.05)
    await loop.stop()

    assert len(woke) == 1
    assert woke[0].event_type == TeamEvent.MESSAGE


@pytest.mark.asyncio
async def test_task_event_wakes_loop():
    """TASK_COMPLETED event triggers wake_callback."""
    woke: list[CoordinationEvent] = []

    async def on_wake(event: CoordinationEvent) -> None:
        woke.append(event)

    loop = CoordinatorLoop(
        role=TeamRole.TEAMMATE,
        wake_callback=on_wake,
    )
    await loop.start()

    event = EventMessage(
        event_type=TeamEvent.TASK_COMPLETED,
        payload={"task_id": "t1"},
    )
    await loop.enqueue(event)
    await asyncio.sleep(0.05)
    await loop.stop()

    assert len(woke) == 1
    assert woke[0].event_type == TeamEvent.TASK_COMPLETED


@pytest.mark.asyncio
async def test_multiple_events_wake_in_order():
    """Events are processed FIFO."""
    woke: list[CoordinationEvent] = []

    async def on_wake(event: CoordinationEvent) -> None:
        woke.append(event)

    loop = CoordinatorLoop(
        role=TeamRole.LEADER,
        wake_callback=on_wake,
    )
    await loop.start()

    for et in [
        TeamEvent.MESSAGE,
        TeamEvent.TASK_COMPLETED,
        TeamEvent.BROADCAST,
    ]:
        await loop.enqueue(
            EventMessage(event_type=et, payload={}),
        )

    await asyncio.sleep(0.1)
    await loop.stop()

    assert [e.event_type for e in woke] == [
        TeamEvent.MESSAGE,
        TeamEvent.TASK_COMPLETED,
        TeamEvent.BROADCAST,
    ]


@pytest.mark.asyncio
async def test_no_callback_does_not_crash():
    """Loop without callback still processes events."""
    loop = CoordinatorLoop(role=TeamRole.LEADER)
    await loop.start()

    await loop.enqueue(
        EventMessage(event_type=TeamEvent.MESSAGE, payload={}),
    )
    await asyncio.sleep(0.05)
    await loop.stop()
