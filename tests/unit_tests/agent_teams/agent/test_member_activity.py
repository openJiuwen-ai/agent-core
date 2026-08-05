# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unit tests for the leader-side member activity registry and team-idle edge.

Covers the registry's edge semantics, the two write paths that feed it (the
leader's own status updates and other members' ``MEMBER_*`` events), and the
marker chunk the leader puts on its stream when the team goes quiet.
"""

import asyncio
from types import SimpleNamespace

import pytest

from openjiuwen.agent_teams.agent import stream_controller
from openjiuwen.agent_teams.agent.coordination.handlers.member import MemberHandler
from openjiuwen.agent_teams.agent.infra import TeamInfra
from openjiuwen.agent_teams.agent.member_activity import (
    IdleSignal,
    MemberActivityRegistry,
    parse_member_status,
)
from openjiuwen.agent_teams.agent.state import TeamAgentState
from openjiuwen.agent_teams.agent.stream_controller import StreamController
from openjiuwen.agent_teams.agent.team_agent import TeamAgent
from openjiuwen.agent_teams.schema.events import (
    EventMessage,
    MemberSpawnedEvent,
    MemberStatusChangedEvent,
    TeamEvent,
)
from openjiuwen.agent_teams.schema.status import MemberStatus
from openjiuwen.agent_teams.schema.stream import TeamOutputSchema, is_team_event_marker
from openjiuwen.agent_teams.schema.team import TeamRole
from openjiuwen.core.single_agent import AgentCard

LEADER = "leader"


# ----------------------------------------------------------------------
# Registry semantics
# ----------------------------------------------------------------------


def test_registry_starts_disarmed():
    """A team that never ran is quiescent but must not announce idleness."""
    registry = MemberActivityRegistry(LEADER)

    assert registry.is_idle() is True
    assert registry.record(LEADER, MemberStatus.READY) is IdleSignal.NONE


def test_registry_fires_once_on_the_idle_edge():
    registry = MemberActivityRegistry(LEADER)
    assert registry.record(LEADER, MemberStatus.BUSY) is IdleSignal.CANCEL

    assert registry.record(LEADER, MemberStatus.READY) is IdleSignal.SCHEDULE
    # Re-asserting the same resting status is not a second edge.
    assert registry.record(LEADER, MemberStatus.READY) is IdleSignal.NONE


def test_registry_rearms_after_activity():
    registry = MemberActivityRegistry(LEADER)
    registry.record(LEADER, MemberStatus.BUSY)
    assert registry.record(LEADER, MemberStatus.READY) is IdleSignal.SCHEDULE

    registry.record(LEADER, MemberStatus.BUSY)
    assert registry.record(LEADER, MemberStatus.READY) is IdleSignal.SCHEDULE


def test_registry_waits_for_every_member():
    registry = MemberActivityRegistry(LEADER)
    registry.record("dev-1", MemberStatus.BUSY)

    # Somebody is still moving, so any observation says "drop the marker".
    assert registry.record(LEADER, MemberStatus.READY) is IdleSignal.CANCEL
    assert registry.record("dev-1", MemberStatus.READY) is IdleSignal.SCHEDULE
    assert registry.snapshot() == {
        LEADER: MemberStatus.READY.value,
        "dev-1": MemberStatus.READY.value,
    }


@pytest.mark.parametrize(
    "status",
    [MemberStatus.UNSTARTED, MemberStatus.ERROR, MemberStatus.SHUTDOWN, MemberStatus.PAUSED],
)
def test_registry_treats_resting_statuses_as_idle(status):
    """UNSTARTED / ERROR count as at-rest even though they are not 'settled'."""
    registry = MemberActivityRegistry(LEADER)
    registry.record(LEADER, MemberStatus.READY)
    registry.record("dev-1", MemberStatus.BUSY)

    assert registry.record("dev-1", status) is IdleSignal.SCHEDULE


def test_registry_treats_shutdown_requested_as_active():
    registry = MemberActivityRegistry(LEADER)
    registry.record("dev-1", MemberStatus.BUSY)
    registry.record("dev-1", MemberStatus.SHUTDOWN_REQUESTED)

    assert registry.record(LEADER, MemberStatus.READY) is IdleSignal.CANCEL
    assert registry.record("dev-1", MemberStatus.SHUTDOWN) is IdleSignal.SCHEDULE


def test_seed_replaces_roster_and_keeps_self():
    registry = MemberActivityRegistry(LEADER)
    registry.record("gone", MemberStatus.BUSY)

    registry.seed({"dev-1": MemberStatus.READY})

    assert registry.snapshot() == {
        LEADER: MemberStatus.UNSTARTED.value,
        "dev-1": MemberStatus.READY.value,
    }


def test_seed_of_empty_team_is_not_an_error():
    registry = MemberActivityRegistry(LEADER)

    registry.seed({})

    assert registry.snapshot() == {LEADER: MemberStatus.UNSTARTED.value}


def test_parse_member_status_rejects_unknown_values():
    assert parse_member_status(MemberStatus.BUSY.value) is MemberStatus.BUSY
    assert parse_member_status("从未见过的状态") is None
    assert parse_member_status(None) is None


# ----------------------------------------------------------------------
# Stream marker
# ----------------------------------------------------------------------


def _stream_controller(
    queue: asyncio.Queue,
    registry: MemberActivityRegistry | None = None,
) -> StreamController:
    blueprint = SimpleNamespace(member_name=LEADER, role=TeamRole.LEADER)
    state = TeamAgentState()
    state.member_registry = registry
    controller = StreamController(
        blueprint_getter=lambda: blueprint,
        state=state,
        resources=SimpleNamespace(harness=None),
        status_updater=lambda status: None,
        execution_updater=lambda status: None,
    )
    controller.stream_queue = queue
    return controller


@pytest.fixture
def fast_debounce(monkeypatch):
    """Shrink the team-idle debounce window so tests need not wait 2s."""
    monkeypatch.setattr(stream_controller, "_TEAM_IDLE_DEBOUNCE_SECONDS", 0.02)
    return 0.02


def _idle_registry() -> MemberActivityRegistry:
    """A registry whose roster is at rest and owes a marker."""
    registry = MemberActivityRegistry(LEADER)
    registry.record(LEADER, MemberStatus.BUSY)
    registry.record(LEADER, MemberStatus.READY)
    return registry


def test_emit_team_idle_enqueues_open_stream_marker():
    queue: asyncio.Queue = asyncio.Queue()
    controller = _stream_controller(queue)

    controller.emit_team_idle({LEADER: MemberStatus.READY.value})

    marker = queue.get_nowait()
    assert isinstance(marker, TeamOutputSchema)
    assert marker.payload["event_type"] == "team.idle"
    assert marker.payload["member_count"] == 1
    assert marker.payload["members"] == {LEADER: MemberStatus.READY.value}
    assert marker.source_member == LEADER
    assert marker.role is TeamRole.LEADER
    # The stream stays open: no None sentinel behind the marker.
    assert queue.empty()


@pytest.mark.asyncio
async def test_close_stream_cancels_a_pending_marker(fast_debounce):
    """No timer may outlive the stream it would have written to."""
    queue: asyncio.Queue = asyncio.Queue()
    controller = _stream_controller(queue, _idle_registry())

    controller.schedule_team_idle()
    pending = controller._idle_marker_task
    controller.close_stream()

    assert controller._idle_marker_task is None
    await asyncio.sleep(fast_debounce * 3)
    assert pending.cancelled()
    # Only the None sentinel from close_stream, no marker behind it.
    assert queue.get_nowait() is None
    assert queue.empty()


@pytest.mark.asyncio
async def test_stop_cancels_a_pending_marker(fast_debounce):
    queue: asyncio.Queue = asyncio.Queue()
    controller = _stream_controller(queue, _idle_registry())

    controller.schedule_team_idle()
    pending = controller._idle_marker_task
    await controller.stop()

    assert controller._idle_marker_task is None
    await asyncio.sleep(fast_debounce * 3)
    assert pending.cancelled()
    assert queue.empty()


@pytest.mark.asyncio
async def test_cancel_team_idle_is_idempotent(fast_debounce):
    queue: asyncio.Queue = asyncio.Queue()
    controller = _stream_controller(queue, _idle_registry())

    controller.cancel_team_idle()
    controller.schedule_team_idle()
    controller.cancel_team_idle()
    controller.cancel_team_idle()

    assert controller._idle_marker_task is None
    await asyncio.sleep(fast_debounce * 3)
    assert queue.empty()


@pytest.mark.asyncio
async def test_late_cancel_callback_does_not_clear_a_newer_timer(fast_debounce):
    """Re-arming immediately after a cancel must survive the old callback."""
    queue: asyncio.Queue = asyncio.Queue()
    controller = _stream_controller(queue, _idle_registry())

    controller.schedule_team_idle()
    stale = controller._idle_marker_task
    controller.schedule_team_idle()
    fresh = controller._idle_marker_task
    # The cancelled task's done callback runs on the loop, after this yield.
    await asyncio.sleep(0)

    assert stale.cancelled()
    assert controller._idle_marker_task is fresh
    marker = await asyncio.wait_for(queue.get(), timeout=1)
    assert marker.payload["event_type"] == "team.idle"


@pytest.mark.asyncio
async def test_marker_is_dropped_when_the_team_moved_again(fast_debounce):
    """Belt-and-braces: the timer re-checks the registry before emitting."""
    queue: asyncio.Queue = asyncio.Queue()
    registry = _idle_registry()
    controller = _stream_controller(queue, registry)

    controller.schedule_team_idle()
    # Mutate the roster without going through the cancel path.
    registry.record("dev-1", MemberStatus.BUSY)

    await asyncio.sleep(fast_debounce * 3)
    assert queue.empty()
    assert controller._idle_marker_task is None


def test_is_team_event_marker_distinguishes_agent_output():
    marker = TeamOutputSchema(type="message", index=0, payload={"event_type": "team.idle"})
    output = TeamOutputSchema(type="llm_output", index=0, payload={"content": "hi"})

    assert is_team_event_marker(marker) is True
    assert is_team_event_marker(output) is False
    assert is_team_event_marker(None) is False


# ----------------------------------------------------------------------
# TeamAgent wiring
# ----------------------------------------------------------------------


def _leader_agent(queue: asyncio.Queue) -> TeamAgent:
    agent = TeamAgent(AgentCard(name=LEADER))
    agent.state.member_registry = MemberActivityRegistry(LEADER)
    agent.stream_controller.stream_queue = queue
    return agent


@pytest.mark.asyncio
async def test_observe_member_status_emits_after_the_debounce(fast_debounce):
    queue: asyncio.Queue = asyncio.Queue()
    agent = _leader_agent(queue)

    await agent.observe_member_status("dev-1", MemberStatus.BUSY)
    await agent.observe_member_status("dev-1", MemberStatus.READY)
    # Armed, not fired: the quiet has to hold first.
    assert queue.empty()

    marker = await asyncio.wait_for(queue.get(), timeout=1)
    assert marker.payload["event_type"] == "team.idle"
    # The timer left no dangling reference behind.
    assert agent.stream_controller._idle_marker_task is None

    await agent.observe_member_status("dev-1", MemberStatus.READY)
    await asyncio.sleep(fast_debounce * 3)
    assert queue.empty()


@pytest.mark.asyncio
async def test_momentary_quiet_never_emits(fast_debounce):
    """A member settling for an instant mid-handoff is not an idle team."""
    queue: asyncio.Queue = asyncio.Queue()
    agent = _leader_agent(queue)

    await agent.observe_member_status("dev-1", MemberStatus.BUSY)
    await agent.observe_member_status("dev-1", MemberStatus.READY)
    pending = agent.stream_controller._idle_marker_task
    assert pending is not None
    # Somebody picks the work back up well inside the window.
    await agent.observe_member_status(LEADER, MemberStatus.BUSY)

    await asyncio.sleep(fast_debounce * 3)
    assert queue.empty()
    assert pending.cancelled()
    assert agent.stream_controller._idle_marker_task is None


@pytest.mark.asyncio
async def test_repeated_arming_keeps_one_timer_and_emits_once(fast_debounce):
    queue: asyncio.Queue = asyncio.Queue()
    agent = _leader_agent(queue)

    await agent.observe_member_status("dev-1", MemberStatus.BUSY)
    await agent.observe_member_status("dev-1", MemberStatus.READY)
    first = agent.stream_controller._idle_marker_task
    await agent.observe_member_status("dev-1", MemberStatus.BUSY)
    await agent.observe_member_status("dev-1", MemberStatus.READY)
    second = agent.stream_controller._idle_marker_task
    # Cancellation only lands once the loop gets a turn.
    await asyncio.sleep(0)

    assert first is not second
    assert first.cancelled()

    marker = await asyncio.wait_for(queue.get(), timeout=1)
    assert marker.payload["event_type"] == "team.idle"
    await asyncio.sleep(fast_debounce * 3)
    assert queue.empty()
    assert second.done()


@pytest.mark.asyncio
async def test_observe_member_status_is_noop_without_registry():
    """Teammates hold no registry — observing must stay silent, not crash."""
    agent = TeamAgent(AgentCard(name="dev-1"))
    queue: asyncio.Queue = asyncio.Queue()
    agent.stream_controller.stream_queue = queue

    await agent.observe_member_status("dev-1", MemberStatus.READY)

    assert queue.empty()


# ----------------------------------------------------------------------
# MemberHandler feed
# ----------------------------------------------------------------------


class _RecordingHost:
    """Duck-typed DispatcherHost capturing observed member statuses."""

    def __init__(self):
        self.observed: list[tuple[str, MemberStatus]] = []

    def is_agent_ready(self) -> bool:
        return True

    def is_agent_running(self) -> bool:
        return False

    def idle_seconds(self) -> float | None:
        return None

    def has_in_flight_round(self) -> bool:
        return False

    def has_pending_interrupt(self) -> bool:
        return False

    async def cancel_agent(self) -> None:
        return None

    async def deliver_input(self, content, *, use_steer: bool = True) -> None:
        return None

    async def resume_interrupt(self, user_input) -> None:
        return None

    async def shutdown_self(self) -> None:
        return None

    async def conclude_completed_round(self, member_count: int, task_count: int) -> None:
        return None

    async def finalize_non_contributing_worktrees(self) -> None:
        return None

    async def observe_member_status(self, member_name: str, status: MemberStatus) -> None:
        self.observed.append((member_name, status))


class _FakePoll:
    async def pause_polls(self) -> None:
        return None

    async def resume_polls(self) -> None:
        return None


def _leader_handler(host: _RecordingHost) -> MemberHandler:
    blueprint = SimpleNamespace(
        member_name=LEADER,
        role=TeamRole.LEADER,
        lifecycle="temporary",
        spec=SimpleNamespace(reliability=None),
    )
    return MemberHandler(host, blueprint, TeamInfra(), _FakePoll())


@pytest.mark.asyncio
async def test_status_changed_event_feeds_the_registry():
    host = _RecordingHost()
    handler = _leader_handler(host)

    await handler.on_member_event(
        EventMessage.from_event(
            MemberStatusChangedEvent(
                team_name="t",
                member_name="dev-1",
                old_status=MemberStatus.BUSY.value,
                new_status=MemberStatus.READY.value,
            )
        )
    )

    assert host.observed == [("dev-1", MemberStatus.READY)]


@pytest.mark.asyncio
async def test_spawned_event_marks_the_member_active():
    host = _RecordingHost()
    handler = _leader_handler(host)

    await handler.on_member_event(
        EventMessage.from_event(MemberSpawnedEvent(team_name="t", member_name="dev-1"))
    )

    assert host.observed == [("dev-1", MemberStatus.STARTING)]


@pytest.mark.asyncio
async def test_unknown_status_value_is_ignored():
    host = _RecordingHost()
    handler = _leader_handler(host)

    await handler.on_member_event(
        EventMessage(
            event_type=TeamEvent.MEMBER_STATUS_CHANGED,
            payload={
                "team_name": "t",
                "member_name": "dev-1",
                "old_status": MemberStatus.BUSY.value,
                "new_status": "not_a_status",
            },
        )
    )

    assert host.observed == []
