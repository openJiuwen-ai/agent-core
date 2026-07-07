# coding: utf-8
"""Tests for EventBus lifecycle."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from openjiuwen.agent_teams.agent.coordination.event_bus import (
    CoordinationEvent,
    EventBus,
    InnerEventMessage,
    InnerEventType,
)
from openjiuwen.agent_teams.agent.coordination.kernel import CoordinationKernel
from openjiuwen.agent_teams.schema.events import (
    EventMessage,
    TeamEvent,
)
from openjiuwen.agent_teams.schema.team import TeamRole


def _make_kernel_host(memory_manager: object | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        member_name="leader-1",
        role=TeamRole.LEADER,
        team_name="test-team",
        resources=SimpleNamespace(
            memory_manager=memory_manager,
            harness=SimpleNamespace(
                stop=AsyncMock(),
                dispose=AsyncMock(),
            ),
        ),
        infra=SimpleNamespace(
            messager=None,
            team_backend=None,
        ),
        spawn_manager=SimpleNamespace(
            spawned_handles={},
            cancel_recovery_tasks=AsyncMock(),
            shutdown_all_handles=AsyncMock(),
        ),
        session_manager=SimpleNamespace(
            team_session=None,
            release_session=MagicMock(),
        ),
        stream_controller=SimpleNamespace(
            stream_queue=object(),
            drain_agent_task=AsyncMock(),
            close_stream=MagicMock(),
            stop=AsyncMock(),
        ),
        persist_allocator_state=MagicMock(),
    )


@pytest.mark.asyncio
@pytest.mark.level0
async def test_start_stop_sets_running_flag():
    """start() sets is_running, stop() clears it."""
    loop = EventBus(role=TeamRole.LEADER)
    assert loop.is_running is False

    await loop.start()
    assert loop.is_running is True

    await loop.stop()
    assert loop.is_running is False


@pytest.mark.asyncio
@pytest.mark.level0
async def test_stop_is_idempotent():
    """Calling stop() twice does not raise."""
    loop = EventBus(role=TeamRole.LEADER)
    await loop.start()
    await loop.stop()
    await loop.stop()
    assert loop.is_running is False


@pytest.mark.asyncio
@pytest.mark.level1
async def test_wake_callback_invoked_on_event():
    """When an event is enqueued, the wake callback fires."""
    woke: list[CoordinationEvent] = []

    async def on_wake(event: CoordinationEvent) -> None:
        woke.append(event)

    loop = EventBus(role=TeamRole.LEADER)
    await loop.start(wake_callback=on_wake)

    event = EventMessage(
        event_type=TeamEvent.MESSAGE,
        payload={"msg": "hello"},
    )
    await loop.enqueue(event)
    await asyncio.sleep(0.05)
    await loop.stop()

    assert len(woke) == 1
    assert woke[0].event_type == TeamEvent.MESSAGE


@pytest.mark.asyncio
@pytest.mark.level1
async def test_poll_timer_fires_periodically():
    """Poll timer enqueues synthetic poll events."""
    woke: list[CoordinationEvent] = []

    async def on_wake(event: CoordinationEvent) -> None:
        woke.append(event)

    loop = EventBus(
        role=TeamRole.LEADER,
        mailbox_poll_interval=0.05,
        task_poll_interval=0.05,
    )
    await loop.start(wake_callback=on_wake)

    await asyncio.sleep(0.15)
    await loop.stop()

    mailbox_polls = [
        e for e in woke if isinstance(e, InnerEventMessage) and e.event_type == InnerEventType.POLL_MAILBOX
    ]
    task_polls = [e for e in woke if isinstance(e, InnerEventMessage) and e.event_type == InnerEventType.POLL_TASK]
    assert len(mailbox_polls) >= 2
    assert len(task_polls) >= 2


@pytest.mark.asyncio
@pytest.mark.level0
async def test_pause_extracts_memory_once_and_stop_after_pause_does_not_repeat():
    """Pause owns persistent-team extraction; a later stop does not repeat it."""
    memory_manager = SimpleNamespace(
        extract_after_round=AsyncMock(),
        close=AsyncMock(),
    )
    host = _make_kernel_host(memory_manager)
    kernel = CoordinationKernel(host)
    kernel._lifecycle_state = "running"

    await kernel.pause()
    memory_manager.extract_after_round.assert_awaited_once()

    await kernel.stop()
    memory_manager.extract_after_round.assert_awaited_once()
    memory_manager.close.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.level0
async def test_stop_extracts_memory_when_stopping_from_running():
    """Direct stop from running performs final memory extraction before teardown."""
    memory_manager = SimpleNamespace(
        extract_after_round=AsyncMock(),
        close=AsyncMock(),
    )
    host = _make_kernel_host(memory_manager)
    kernel = CoordinationKernel(host)
    kernel._lifecycle_state = "running"

    await kernel.stop()

    memory_manager.extract_after_round.assert_awaited_once()
    memory_manager.close.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.level0
async def test_finalize_round_does_not_extract_memory():
    """Round cleanup no longer performs team memory extraction."""
    memory_manager = SimpleNamespace(extract_after_round=AsyncMock())
    host = _make_kernel_host(memory_manager)
    kernel = CoordinationKernel(host)

    await kernel.finalize_round()

    memory_manager.extract_after_round.assert_not_awaited()
    host.stream_controller.stop.assert_awaited_once()
    host.resources.harness.stop.assert_awaited_once()
    assert host.stream_controller.stream_queue is None
