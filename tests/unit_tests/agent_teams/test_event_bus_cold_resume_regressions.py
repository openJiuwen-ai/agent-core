# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Coordination wake delivery regressions across cold pause and restart."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openjiuwen.agent_teams.agent.coordination.dispatcher import EventDispatcher
from openjiuwen.agent_teams.agent.coordination.event_bus import (
    EventBus,
    InnerEventMessage,
    InnerEventType,
)
from openjiuwen.agent_teams.schema.events import EventMessage, TeamEvent
from openjiuwen.agent_teams.schema.team import TeamRole


async def _wait_until(predicate, *, timeout: float = 1.0) -> None:
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_restart_after_stuck_stop_does_not_consume_stale_shutdown(monkeypatch) -> None:
    bus = EventBus(role=TeamRole.LEADER)
    entered = asyncio.Event()

    async def stuck_wake(_event: object) -> None:
        entered.set()
        await asyncio.Event().wait()

    real_wait_for = asyncio.wait_for

    async def short_wait_for(awaitable, timeout):
        return await real_wait_for(awaitable, timeout=0.01 if timeout == 5.0 else timeout)

    monkeypatch.setattr(asyncio, "wait_for", short_wait_for)
    await bus.start(wake_callback=stuck_wake)
    await bus.enqueue(InnerEventMessage(event_type=InnerEventType.USER_INPUT))
    await entered.wait()
    await bus.stop()

    received: list[object] = []

    async def record_wake(event: object) -> None:
        received.append(event)

    await bus.start(wake_callback=record_wake)
    try:
        await bus.enqueue(InnerEventMessage(event_type=InnerEventType.USER_INPUT))
        await _wait_until(lambda: bool(received))
    finally:
        await bus.stop()


@pytest.mark.asyncio
async def test_callback_cancelled_error_does_not_kill_event_loop() -> None:
    bus = EventBus(role=TeamRole.LEADER)
    first_wake = asyncio.Event()
    received: list[object] = []

    async def flaky_wake(event: object) -> None:
        if not first_wake.is_set():
            first_wake.set()
            raise asyncio.CancelledError
        received.append(event)

    await bus.start(wake_callback=flaky_wake)
    try:
        await bus.enqueue(InnerEventMessage(event_type=InnerEventType.REFRESH_TEAM_CONTEXT))
        await first_wake.wait()
        await bus.enqueue(InnerEventMessage(event_type=InnerEventType.USER_INPUT))
        await _wait_until(lambda: bool(received))
    finally:
        await bus.stop()


@pytest.mark.asyncio
async def test_actual_event_loop_task_cancellation_propagates() -> None:
    bus = EventBus(role=TeamRole.LEADER)
    entered = asyncio.Event()

    async def hanging_wake(_event: object) -> None:
        entered.set()
        await asyncio.Event().wait()

    await bus.start(wake_callback=hanging_wake)
    await bus.enqueue(InnerEventMessage(event_type=InnerEventType.USER_INPUT))
    await entered.wait()
    assert bus._loop_task is not None
    bus._loop_task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await bus._loop_task


def _dispatcher(*, ready: bool) -> tuple[EventDispatcher, SimpleNamespace]:
    round_controller = SimpleNamespace(is_agent_ready=lambda: ready)
    dispatcher = EventDispatcher.__new__(EventDispatcher)
    dispatcher._round = round_controller
    dispatcher._deferred_wakes = []
    dispatcher._startup_ready = False
    dispatcher._startup_lock = asyncio.Lock()
    dispatcher._dispatch_ready = AsyncMock()
    return dispatcher, round_controller


@pytest.mark.asyncio
async def test_activation_serializes_deferred_and_concurrent_wakes_fifo() -> None:
    dispatcher, _ = _dispatcher(ready=True)
    old = InnerEventMessage(event_type=InnerEventType.USER_INPUT, payload={"content": "old"})
    new = InnerEventMessage(event_type=InnerEventType.USER_INPUT, payload={"content": "new"})
    flushing = asyncio.Event()
    release = asyncio.Event()

    async def record(event: object) -> None:
        if event is old:
            flushing.set()
            await release.wait()

    dispatcher._dispatch_ready.side_effect = record
    dispatcher._deferred_wakes.append(old)
    activation = asyncio.create_task(dispatcher.activate_and_flush())
    await flushing.wait()
    concurrent = asyncio.create_task(dispatcher.dispatch(new))
    await asyncio.sleep(0)
    assert not concurrent.done()

    release.set()
    await activation
    await concurrent

    replayed = [call.args[0] for call in dispatcher._dispatch_ready.await_args_list]
    assert replayed == [old, new]


@pytest.mark.asyncio
async def test_post_activation_dispatches_and_deactivation_recloses_gate() -> None:
    dispatcher, _ = _dispatcher(ready=True)
    live = InnerEventMessage(event_type=InnerEventType.USER_INPUT, payload={"content": "live"})
    restart = InnerEventMessage(event_type=InnerEventType.USER_INPUT, payload={"content": "restart"})

    await dispatcher.activate_and_flush()
    await dispatcher.dispatch(live)
    dispatcher._dispatch_ready.assert_awaited_once_with(live)

    await dispatcher.deactivate()
    await dispatcher.dispatch(restart)

    dispatcher._dispatch_ready.assert_awaited_once_with(live)
    assert dispatcher._deferred_wakes == [restart]


@pytest.mark.asyncio
async def test_deferred_wakes_are_bounded_and_drop_repeatable_events() -> None:
    dispatcher, _ = _dispatcher(ready=True)

    for event_type in (
        InnerEventType.POLL_TASK,
        InnerEventType.POLL_MAILBOX,
        InnerEventType.INITIAL_POLL_TASK,
        InnerEventType.SCHEDULER_SCAN,
        InnerEventType.SHUTDOWN,
    ):
        await dispatcher.dispatch(InnerEventMessage(event_type=event_type))

    events = [
        EventMessage(event_type=TeamEvent.MESSAGE, payload={"index": index})
        for index in range(dispatcher._MAX_DEFERRED_WAKES + 2)
    ]

    for event in events:
        await dispatcher.dispatch(event)

    await dispatcher.activate_and_flush()

    replayed = [call.args[0] for call in dispatcher._dispatch_ready.await_args_list]
    assert replayed == events[-dispatcher._MAX_DEFERRED_WAKES :]
