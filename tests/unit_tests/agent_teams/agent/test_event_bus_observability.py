# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Event-bus / dispatcher silent-loss observability and robustness.

A user input that enters the coordination layer but never reaches a
handler presents as "the agent never replied". These tests pin the
logging / hardening that makes each silent drop point visible:

- enqueue into a stopped bus warns (the event is stranded forever);
- a loop cancelled mid-wake warns and re-raises (CancelledError is a
  BaseException and used to kill the loop task silently);
- deferring a USER_INPUT wake warns (deferral after the single
  ``flush_deferred`` in ``kernel.start`` used to be invisible);
- ``kernel.enqueue_user_input`` without a bus warns instead of
  silently returning.
"""

from __future__ import annotations

import asyncio
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from openjiuwen.agent_teams.agent.coordination.dispatcher import EventDispatcher
from openjiuwen.agent_teams.agent.coordination.event_bus import (
    EventBus,
    InnerEventMessage,
    InnerEventType,
)
from openjiuwen.agent_teams.agent.coordination.kernel import CoordinationKernel
from openjiuwen.agent_teams.schema.team import TeamRole


@pytest.mark.asyncio
async def test_enqueue_on_stopped_bus_warns(caplog: pytest.LogCaptureFixture) -> None:
    bus = EventBus(role=TeamRole.LEADER)
    event = InnerEventMessage(event_type=InnerEventType.USER_INPUT, payload={"content": "hi"})

    with caplog.at_level(logging.WARNING):
        await bus.enqueue(event)

    assert any("not running" in record.message for record in caplog.records)
    # The event is still queued (callers may start the loop later) — only
    # the silent-loss part changes.
    assert bus._event_queue.qsize() == 1


@pytest.mark.asyncio
async def test_loop_cancelled_mid_wake_warns_and_dies(caplog: pytest.LogCaptureFixture) -> None:
    bus = EventBus(role=TeamRole.LEADER)
    entered = asyncio.Event()

    async def hanging_wake(_event: object) -> None:
        entered.set()
        await asyncio.Event().wait()  # never completes

    await bus.start(wake_callback=hanging_wake)
    await bus.enqueue(InnerEventMessage(event_type=InnerEventType.USER_INPUT))
    await asyncio.wait_for(entered.wait(), timeout=2.0)

    with caplog.at_level(logging.WARNING):
        bus._loop_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await bus._loop_task

    assert any("cancelled while handling" in record.message for record in caplog.records)


@pytest.mark.asyncio
async def test_defer_user_input_warns(caplog: pytest.LogCaptureFixture) -> None:
    dispatcher = EventDispatcher.__new__(EventDispatcher)
    dispatcher._deferred_wakes = []

    with caplog.at_level(logging.WARNING):
        dispatcher._defer_wake(
            InnerEventMessage(event_type=InnerEventType.USER_INPUT, payload={"content": "hi"})
        )

    assert any("deferring USER_INPUT" in record.message for record in caplog.records)
    assert len(dispatcher._deferred_wakes) == 1


@pytest.mark.asyncio
async def test_defer_non_user_input_stays_debug(caplog: pytest.LogCaptureFixture) -> None:
    dispatcher = EventDispatcher.__new__(EventDispatcher)
    dispatcher._deferred_wakes = []

    with caplog.at_level(logging.WARNING):
        dispatcher._defer_wake(InnerEventMessage(event_type=InnerEventType.REFRESH_TEAM_CONTEXT))

    assert not caplog.records
    assert len(dispatcher._deferred_wakes) == 1


@pytest.mark.asyncio
async def test_enqueue_user_input_without_bus_warns(caplog: pytest.LogCaptureFixture) -> None:
    kernel = CoordinationKernel.__new__(CoordinationKernel)
    kernel._host = SimpleNamespace(member_name="office")
    kernel._event_bus = None

    with caplog.at_level(logging.WARNING):
        await kernel.enqueue_user_input("hi")

    assert any("user input dropped" in record.message for record in caplog.records)


@pytest.mark.asyncio
async def test_restart_after_stuck_stop_is_not_poisoned_by_stale_shutdown() -> None:
    """A stop() with a stuck loop leaves SHUTDOWN queued; the next start's
    loop must not consume it as its first event and die instantly."""
    bus = EventBus(role=TeamRole.LEADER)
    entered = asyncio.Event()

    async def stuck_wake(_event: object) -> None:
        entered.set()
        await asyncio.Event().wait()  # never completes

    await bus.start(wake_callback=stuck_wake)
    await bus.enqueue(InnerEventMessage(event_type=InnerEventType.USER_INPUT))
    await asyncio.wait_for(entered.wait(), timeout=2.0)

    # The loop is stuck mid-wake: stop() times out, cancels the task, and the
    # SHUTDOWN sentinel it enqueued stays in the queue.
    await bus.stop()
    assert not bus.is_running

    received: list[object] = []

    async def recording_wake(event: object) -> None:
        received.append(event)

    await bus.start(wake_callback=recording_wake)
    try:
        await bus.enqueue(InnerEventMessage(event_type=InnerEventType.USER_INPUT))
        for _ in range(30):
            if received:
                break
            await asyncio.sleep(0.1)
        assert received, "event after restart was never dispatched (stale SHUTDOWN poisoned the new loop)"
    finally:
        await bus.stop()


@pytest.mark.asyncio
async def test_spurious_cancelled_error_in_wake_does_not_kill_loop() -> None:
    """A CancelledError leaking out of a wake (not aimed at the loop task)
    must be logged and survived, not silently end the loop."""
    bus = EventBus(role=TeamRole.LEADER)
    received: list[object] = []
    first = asyncio.Event()

    async def flaky_wake(event: object) -> None:
        if not first.is_set():
            first.set()
            raise asyncio.CancelledError()  # spurious: nobody cancelled the loop task
        received.append(event)

    await bus.start(wake_callback=flaky_wake)
    try:
        await bus.enqueue(InnerEventMessage(event_type=InnerEventType.REFRESH_TEAM_CONTEXT))
        await asyncio.wait_for(first.wait(), timeout=2.0)
        await bus.enqueue(InnerEventMessage(event_type=InnerEventType.USER_INPUT))
        for _ in range(30):
            if received:
                break
            await asyncio.sleep(0.1)
        assert received, "loop died on a spurious CancelledError from the wake callback"
    finally:
        await bus.stop()


def _make_start_kernel(dispatcher: object) -> CoordinationKernel:
    """Leader kernel wired for start(): real flush ordering, mocked edges."""
    host = SimpleNamespace(
        member_name="office",
        role=TeamRole.LEADER,
        team_name="team-demo",
        blueprint=None,
        state=SimpleNamespace(pending_user_query=""),
        resources=SimpleNamespace(memory_manager=None, harness=None),
        infra=SimpleNamespace(
            team_backend=None,
            messager=None,
            workspace_manager=None,
        ),
        session_manager=SimpleNamespace(
            bind_session=AsyncMock(),
            release_session=MagicMock(),
        ),
        update_status=AsyncMock(),
        refresh_idle_baseline=MagicMock(),
        recover_team=AsyncMock(),
    )
    event_bus = SimpleNamespace(
        is_running=True,
        start=AsyncMock(),
        enqueue=AsyncMock(),
    )
    kernel = CoordinationKernel.__new__(CoordinationKernel)
    kernel._host = host
    kernel._event_bus = event_bus
    kernel._dispatcher = dispatcher
    kernel._scheduler = None
    kernel._lifecycle_state = "paused"
    kernel._subscribed_topics = []
    kernel.resume_paused_round = AsyncMock()
    return kernel


@pytest.mark.asyncio
async def test_start_flushes_deferred_after_resume_paused_round() -> None:
    """flush_deferred runs at the very end of start(), after the resume replay."""
    dispatcher = SimpleNamespace(
        flush_deferred=AsyncMock(),
        team_completion=SimpleNamespace(rearm=MagicMock()),
    )
    kernel = _make_start_kernel(dispatcher)
    parent = MagicMock()
    parent.attach_mock(kernel.resume_paused_round, "resume_paused_round")
    parent.attach_mock(dispatcher.flush_deferred, "flush_deferred")

    session = SimpleNamespace(get_session_id=lambda: "sess-1")
    await kernel.start(session)

    call_names = [invocation[0] for invocation in parent.mock_calls]
    assert call_names[-2:] == ["resume_paused_round", "flush_deferred"]
