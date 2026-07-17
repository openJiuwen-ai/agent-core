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
from openjiuwen.agent_teams.harness.state import HarnessState
from openjiuwen.agent_teams.runtime.metadata import (
    merge_pending_resume,
    read_pending_resume,
)
from openjiuwen.agent_teams.schema.events import (
    EventMessage,
    TeamEvent,
)
from openjiuwen.agent_teams.schema.team import TeamRole


class _StubSession:
    """Minimal session exposing the state API the kernel's persistence uses."""

    def __init__(self) -> None:
        self.state: dict = {}

    def update_state(self, data: dict) -> None:
        self.state.update(data)

    def get_state(self, key=None):
        if key is None:
            return self.state
        return self.state.get(key)


def _make_kernel_host(
    memory_manager: object | None = None,
    messager: object | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        member_name="leader-1",
        role=TeamRole.LEADER,
        team_name="test-team",
        resources=SimpleNamespace(
            memory_manager=memory_manager,
            harness=SimpleNamespace(
                stop=AsyncMock(),
                dispose=AsyncMock(),
                # Not PAUSED, so kernel.start's warm-resume hook stays inert.
                state=HarnessState.IDLE,
            ),
        ),
        infra=SimpleNamespace(
            messager=messager,
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
            pause_agent=AsyncMock(),
            resume_agent=AsyncMock(),
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
async def test_pause_stops_at_boundary_and_never_hard_cancels():
    """kernel.pause pauses the round; it must never hard-cancel it.

    Regression guard for the root cause: pause used to route through
    ``drain_agent_task`` → ``abort(immediate=True)``, throwing away everything
    the member had done in the round it interrupted mid-way.
    """
    host = _make_kernel_host()
    kernel = CoordinationKernel(host)
    kernel._lifecycle_state = "running"

    await kernel.pause()

    host.stream_controller.pause_agent.assert_awaited_once()
    host.stream_controller.drain_agent_task.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.level0
async def test_stop_hard_cancels_the_round():
    """kernel.stop tears the round down: it is discarded outright."""
    host = _make_kernel_host()
    kernel = CoordinationKernel(host)
    kernel._lifecycle_state = "running"

    await kernel.stop()

    host.stream_controller.drain_agent_task.assert_awaited_once()
    host.stream_controller.pause_agent.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.level0
async def test_pause_persists_pending_resume_for_a_later_cold_start():
    """A paused leader records what a cold start needs to continue its round."""
    session = _StubSession()
    host = _make_kernel_host()
    host.session_manager.team_session = session
    host.resources.harness.state = HarnessState.PAUSED
    host.resources.harness.paused_query = "the original task"
    kernel = CoordinationKernel(host)
    kernel._lifecycle_state = "running"

    await kernel.pause()

    assert read_pending_resume(session, "test-team") == {"query": "the original task"}


@pytest.mark.asyncio
@pytest.mark.level0
async def test_pause_records_nothing_when_no_round_was_suspended():
    """An idle harness had nothing to suspend, so no marker is written."""
    session = _StubSession()
    host = _make_kernel_host()
    host.session_manager.team_session = session
    host.resources.harness.state = HarnessState.IDLE
    kernel = CoordinationKernel(host)
    kernel._lifecycle_state = "running"

    await kernel.pause()

    assert read_pending_resume(session, "test-team") is None


@pytest.mark.asyncio
@pytest.mark.level0
async def test_resume_paused_round_cold_path_consumes_the_marker():
    """A rebuilt (IDLE) harness continues the round the marker recorded.

    This is what makes ``pause -> stop -> start`` behave like ``pause -> resume``.
    """
    session = _StubSession()
    host = _make_kernel_host()
    host.session_manager.team_session = session
    host.resources.harness.state = HarnessState.IDLE
    merge_pending_resume(session, "test-team", {"query": "the original task"})
    kernel = CoordinationKernel(host)

    await kernel.resume_paused_round()

    host.stream_controller.resume_agent.assert_awaited_once_with(query="the original task")
    assert read_pending_resume(session, "test-team") is None


@pytest.mark.asyncio
@pytest.mark.level0
async def test_resume_paused_round_warm_path_ignores_the_marker():
    """A still-PAUSED harness resumes from memory, then drops the marker."""
    session = _StubSession()
    host = _make_kernel_host()
    host.session_manager.team_session = session
    host.resources.harness.state = HarnessState.PAUSED
    merge_pending_resume(session, "test-team", {"query": "unused"})
    kernel = CoordinationKernel(host)

    await kernel.resume_paused_round()

    host.stream_controller.resume_agent.assert_awaited_once_with()
    assert read_pending_resume(session, "test-team") is None


@pytest.mark.asyncio
@pytest.mark.level0
async def test_resume_paused_round_is_noop_without_a_marker():
    """Nothing was paused: the idle harness is left alone."""
    session = _StubSession()
    host = _make_kernel_host()
    host.session_manager.team_session = session
    host.resources.harness.state = HarnessState.IDLE
    kernel = CoordinationKernel(host)

    await kernel.resume_paused_round()

    host.stream_controller.resume_agent.assert_not_awaited()


def _arm_kernel_for_start(host: SimpleNamespace) -> CoordinationKernel:
    """Wire the minimum a ``start`` call needs, short of a real setup().

    ``start`` returns early without an event bus, so the bus and dispatcher
    are stubbed as already-running: this test targets the tail of ``start``,
    not bus construction.
    """
    host.session_manager.bind_session = AsyncMock()
    host.resources.harness.start = AsyncMock()
    host.stream_controller.start = AsyncMock()
    host.update_status = AsyncMock()
    host.refresh_idle_baseline = MagicMock()
    host.infra.workspace_manager = None
    host.infra.workspace_initialized = True
    host.blueprint = SimpleNamespace(spec=SimpleNamespace(workspace=None))

    kernel = CoordinationKernel(host)
    kernel._event_bus = SimpleNamespace(is_running=True, start=AsyncMock(), enqueue=AsyncMock())
    kernel._dispatcher = SimpleNamespace(team_completion=SimpleNamespace(rearm=MagicMock()))
    return kernel


@pytest.mark.asyncio
@pytest.mark.level0
async def test_start_resumes_a_round_left_paused():
    """``start`` must continue the round a lifecycle pause suspended.

    Regression guard. The resume call used to live in ``notify_team_built``,
    which returns early when there is no scheduler — so an **autonomous** team
    (no scheduler, ever) resumed into silence and silently dropped the work it
    was suspended mid-way through, despite four docs and the method's own
    docstring promising ``start`` resumes it. The other ``resume_paused_round``
    tests call the method directly and so could never catch that; this one
    drives ``start`` itself.
    """
    session = _StubSession()
    host = _make_kernel_host()
    host.session_manager.team_session = session
    host.resources.harness.state = HarnessState.PAUSED
    kernel = _arm_kernel_for_start(host)

    await kernel.start(session)

    kernel._event_bus.enqueue.assert_awaited_once()
    event = kernel._event_bus.enqueue.await_args.args[0]
    assert isinstance(event, InnerEventMessage)
    assert event.event_type == InnerEventType.REFRESH_TEAM_CONTEXT
    host.stream_controller.resume_agent.assert_awaited_once_with()


@pytest.mark.asyncio
@pytest.mark.level0
async def test_start_leaves_an_unpaused_round_alone():
    """A member with nothing suspended is not driven by ``start``."""
    session = _StubSession()
    host = _make_kernel_host()
    host.session_manager.team_session = session
    host.resources.harness.state = HarnessState.IDLE
    kernel = _arm_kernel_for_start(host)

    await kernel.start(session)

    host.stream_controller.resume_agent.assert_not_awaited()


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
async def test_stop_closes_messager_transport():
    """Direct stop closes the current host's messager transport."""
    messager = SimpleNamespace(stop=AsyncMock())
    host = _make_kernel_host(messager=messager)
    kernel = CoordinationKernel(host)
    kernel._lifecycle_state = "running"

    await kernel.stop()

    messager.stop.assert_awaited_once()


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
