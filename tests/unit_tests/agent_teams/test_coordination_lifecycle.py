# coding: utf-8
"""Tests for EventBus lifecycle."""
from __future__ import annotations

import asyncio

import pytest

from openjiuwen.agent_teams.agent.coordination import (
    CoordinationEvent,
    EventBus,
    InnerEventMessage,
    InnerEventType,
)
from openjiuwen.agent_teams.schema.team import TeamRole
from openjiuwen.agent_teams.schema.events import (
    EventMessage,
    TeamEvent,
)


@pytest.mark.asyncio
async def test_start_stop_sets_running_flag():
    """start() sets is_running, stop() clears it."""
    bus = EventBus(role=TeamRole.LEADER)
    assert bus.is_running is False

    await bus.start()
    assert bus.is_running is True

    await bus.stop()
    assert bus.is_running is False


@pytest.mark.asyncio
async def test_stop_is_idempotent():
    """Calling stop() twice does not raise."""
    bus = EventBus(role=TeamRole.LEADER)
    await bus.start()
    await bus.stop()
    await bus.stop()
    assert bus.is_running is False


@pytest.mark.asyncio
async def test_wake_callback_invoked_on_event():
    """When an event is enqueued, the wake callback fires."""
    woke: list[CoordinationEvent] = []

    async def on_wake(event: CoordinationEvent) -> None:
        woke.append(event)

    bus = EventBus(role=TeamRole.LEADER)
    await bus.start(wake_callback=on_wake)

    event = EventMessage(
        event_type=TeamEvent.MESSAGE,
        payload={"msg": "hello"},
    )
    await bus.enqueue(event)
    await asyncio.sleep(0.05)
    await bus.stop()

    assert len(woke) == 1
    assert woke[0].event_type == TeamEvent.MESSAGE


@pytest.mark.asyncio
async def test_poll_timer_fires_periodically():
    """Poll timer enqueues synthetic poll events."""
    woke: list[CoordinationEvent] = []

    async def on_wake(event: CoordinationEvent) -> None:
        woke.append(event)

    bus = EventBus(
        role=TeamRole.LEADER,
        mailbox_poll_interval=0.05,
        task_poll_interval=0.05,
    )
    await bus.start(wake_callback=on_wake)

    await asyncio.sleep(0.15)
    await bus.stop()

    mailbox_polls = [
        e for e in woke
        if isinstance(e, InnerEventMessage) and e.event_type == InnerEventType.POLL_MAILBOX
    ]
    task_polls = [
        e for e in woke
        if isinstance(e, InnerEventMessage) and e.event_type == InnerEventType.POLL_TASK
    ]
    assert len(mailbox_polls) >= 2
    assert len(task_polls) >= 2
