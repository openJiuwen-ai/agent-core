# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unit tests for the leader-side member activity registry and team-idle edge.

Covers the registry's edge semantics, the two write paths that feed it (the
leader's own status updates and other members' ``MEMBER_*`` events), and the
marker chunk the leader puts on its stream when the team goes quiet.
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

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
from openjiuwen.agent_teams.agent.spawn_manager import SpawnManager
from openjiuwen.agent_teams.agent.stream_controller import StreamController
from openjiuwen.agent_teams.agent.team_agent import TeamAgent
from openjiuwen.agent_teams.schema.events import (
    EventMessage,
    MemberSpawnedEvent,
    MemberRestartedEvent,
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
    task_board_probe=None,
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
        task_board_probe=task_board_probe,
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


def _probe(settled: bool):
    """A task-board probe answering a fixed verdict."""

    async def probe() -> bool:
        return settled

    return probe


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


# ----------------------------------------------------------------------
# Task-board condition
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_open_tasks_hold_the_marker_back(fast_debounce):
    """Quiet members with work still on the board is not an idle team."""
    queue: asyncio.Queue = asyncio.Queue()
    controller = _stream_controller(queue, _idle_registry(), _probe(False))

    controller.schedule_team_idle()

    await asyncio.sleep(fast_debounce * 3)
    assert queue.empty()
    assert controller._idle_marker_task is None


@pytest.mark.asyncio
async def test_settled_board_lets_the_marker_through(fast_debounce):
    queue: asyncio.Queue = asyncio.Queue()
    controller = _stream_controller(queue, _idle_registry(), _probe(True))

    controller.schedule_team_idle()

    marker = await asyncio.wait_for(queue.get(), timeout=1)
    assert marker.payload["event_type"] == "team.idle"


@pytest.mark.asyncio
async def test_board_is_only_probed_once_the_members_held_still(fast_debounce):
    """The database round-trip must not happen while somebody is moving."""
    queue: asyncio.Queue = asyncio.Queue()
    calls: list[int] = []

    async def probe() -> bool:
        calls.append(1)
        return True

    registry = _idle_registry()
    controller = _stream_controller(queue, registry, probe)

    controller.schedule_team_idle()
    registry.record("dev-1", MemberStatus.BUSY)

    await asyncio.sleep(fast_debounce * 3)
    assert calls == []
    assert queue.empty()


@pytest.mark.asyncio
async def test_missing_probe_falls_back_to_the_member_view(fast_debounce):
    """Non-leaders hold no probe; the member view stays the whole answer."""
    queue: asyncio.Queue = asyncio.Queue()
    controller = _stream_controller(queue, _idle_registry())

    controller.schedule_team_idle()

    marker = await asyncio.wait_for(queue.get(), timeout=1)
    assert marker.payload["event_type"] == "team.idle"


@pytest.mark.asyncio
async def test_task_board_probe_reads_one_aggregate_count():
    agent = TeamAgent(AgentCard(name=LEADER))
    counts = SimpleNamespace(value=(3, 1))

    async def count_tasks_terminality(team_name: str) -> tuple[int, int]:
        assert team_name == "t"
        return counts.value

    backend = SimpleNamespace(
        team_name="t",
        db=SimpleNamespace(task=SimpleNamespace(count_tasks_terminality=count_tasks_terminality)),
    )
    agent._configurator.team_backend = backend

    assert await agent.is_task_board_settled() is False
    # Every task terminal, and an empty board, both count as settled.
    counts.value = (3, 0)
    assert await agent.is_task_board_settled() is True
    counts.value = (0, 0)
    assert await agent.is_task_board_settled() is True


@pytest.mark.asyncio
async def test_task_board_probe_without_a_backend_is_settled():
    agent = TeamAgent(AgentCard(name=LEADER))

    assert await agent.is_task_board_settled() is True


@pytest.mark.asyncio
async def test_task_board_probe_holds_back_on_a_database_error():
    agent = TeamAgent(AgentCard(name=LEADER))

    async def count_tasks_terminality(team_name: str) -> tuple[int, int]:
        raise RuntimeError("database is locked")

    agent._configurator.team_backend = SimpleNamespace(
        team_name="t",
        db=SimpleNamespace(task=SimpleNamespace(count_tasks_terminality=count_tasks_terminality)),
    )

    assert await agent.is_task_board_settled() is False


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

    pending = agent.stream_controller._idle_marker_task
    assert pending is not None
    # Queue delivery can precede the timer's done callback; await its lifecycle.
    await asyncio.wait_for(pending, timeout=1)
    marker = queue.get_nowait()
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


def _leader_handler(host: _RecordingHost, backend=None) -> MemberHandler:
    blueprint = SimpleNamespace(
        member_name=LEADER,
        role=TeamRole.LEADER,
        lifecycle="temporary",
        spec=SimpleNamespace(reliability=None),
    )
    return MemberHandler(host, blueprint, TeamInfra(team_backend=backend), _FakePoll())


@pytest.mark.asyncio
async def test_status_changed_event_feeds_the_registry():
    host = _RecordingHost()
    backend = SimpleNamespace(get_member=AsyncMock())
    handler = _leader_handler(host, backend)

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
    backend.get_member.assert_not_awaited()


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


@pytest.mark.asyncio
async def test_late_restart_notification_preserves_ready_and_emits_idle(fast_debounce):
    queue: asyncio.Queue = asyncio.Queue()
    agent = _leader_agent(queue)
    backend = SimpleNamespace(get_member=AsyncMock(return_value=SimpleNamespace(status=MemberStatus.READY)))
    handler = _leader_handler(agent, backend)
    await agent.observe_member_status(LEADER, MemberStatus.READY)
    await agent.observe_member_status("dev-1", MemberStatus.BUSY)
    await handler.on_member_event(EventMessage.from_event(MemberStatusChangedEvent(
        team_name="t", member_name="dev-1", old_status="busy", new_status="ready",
    )))
    pending = agent.stream_controller._idle_marker_task
    assert pending is not None

    await handler.on_member_event(EventMessage.from_event(MemberRestartedEvent(
        team_name="t", member_name="dev-1", restart_count=1,
    )))

    assert agent.state.member_registry.snapshot()["dev-1"] == "ready"
    assert agent.state.member_registry.is_idle()
    await asyncio.wait_for(pending, timeout=1)
    assert queue.get_nowait().payload["event_type"] == "team.idle"
    assert queue.empty()
    # The same team can start work again; fixing completion must not freeze it.
    await agent.observe_member_status("dev-1", MemberStatus.BUSY)
    assert not agent.state.member_registry.is_idle()
    await agent.observe_member_status("dev-1", MemberStatus.READY)
    await asyncio.wait_for(agent.stream_controller._idle_marker_task, timeout=1)
    assert queue.get_nowait().payload["event_type"] == "team.idle"


@pytest.mark.asyncio
async def test_restart_publishes_notification_after_teammate_has_settled(fast_debounce):
    queue: asyncio.Queue = asyncio.Queue()
    agent = _leader_agent(queue)
    await agent.observe_member_status(LEADER, MemberStatus.READY)
    await agent.observe_member_status("dev-1", MemberStatus.RESTARTING)
    member = SimpleNamespace(status=MemberStatus.RESTARTING.value)
    backend = SimpleNamespace(team_name="t", get_member=AsyncMock(side_effect=lambda _: member))
    handler = _leader_handler(agent, backend)
    ready_seen = asyncio.Event()
    allow_spawn_return = asyncio.Event()
    published = []

    async def publish(*, topic_id, message):
        published.append(message.event_type)
        await handler.on_member_event(message)

    manager = SpawnManager(
        state=TeamAgentState(),
        configurator=SimpleNamespace(member_name=LEADER, team_name="t", team_backend=backend,
                                     messager=SimpleNamespace(publish=publish)),
        team_agent_getter=lambda: agent,
    )
    manager.cleanup_teammate = AsyncMock()
    manager.build_context_from_db = AsyncMock(return_value=SimpleNamespace(member_name="dev-1"))

    async def spawn(*args, **kwargs):
        member.status = MemberStatus.READY.value
        await handler.on_member_event(EventMessage.from_event(MemberStatusChangedEvent(
            team_name="t", member_name="dev-1", old_status="restarting", new_status="ready",
        )))
        ready_seen.set()
        await allow_spawn_return.wait()
        return SimpleNamespace()

    manager.spawn_teammate = spawn
    restart = asyncio.create_task(manager.restart_teammate("dev-1"))
    try:
        await asyncio.wait_for(ready_seen.wait(), timeout=1)
        assert agent.state.member_registry.snapshot()["dev-1"] == "ready"
        allow_spawn_return.set()
        assert await asyncio.wait_for(restart, timeout=1)
        assert published == [TeamEvent.MEMBER_RESTARTED]
        assert agent.state.member_registry.snapshot()["dev-1"] == "ready"
        await asyncio.wait_for(agent.stream_controller._idle_marker_task, timeout=1)
        assert queue.get_nowait().payload["event_type"] == "team.idle"
    finally:
        restart.cancel()
        await asyncio.gather(restart, return_exceptions=True)
        agent.stream_controller.cancel_team_idle()


@pytest.mark.asyncio
@pytest.mark.parametrize("event_class", [MemberSpawnedEvent, MemberRestartedEvent])
@pytest.mark.parametrize("current", [
    MemberStatus.STARTING, MemberStatus.RESTARTING, MemberStatus.BUSY,
    MemberStatus.READY, MemberStatus.PAUSED, MemberStatus.SHUTDOWN,
])
async def test_lifecycle_notification_uses_current_member_status(event_class, current):
    host = _RecordingHost()
    backend = SimpleNamespace(get_member=AsyncMock(return_value=SimpleNamespace(status=current.value)))
    handler = _leader_handler(host, backend)

    await handler.on_member_event(EventMessage.from_event(event_class(team_name="t", member_name="dev-1")))

    assert host.observed == [("dev-1", current)]
    backend.get_member.assert_awaited_once_with("dev-1")


@pytest.mark.asyncio
@pytest.mark.parametrize("event_class,fallback", [
    (MemberSpawnedEvent, MemberStatus.STARTING),
    (MemberRestartedEvent, MemberStatus.RESTARTING),
])
@pytest.mark.parametrize("row", [None, SimpleNamespace(status="unstarted"), SimpleNamespace(status="invalid")])
async def test_unresolved_lifecycle_notification_stays_active(event_class, fallback, row):
    host = _RecordingHost()
    handler = _leader_handler(host, SimpleNamespace(get_member=AsyncMock(return_value=row)))

    await handler.on_member_event(EventMessage.from_event(event_class(team_name="t", member_name="dev-1")))

    assert host.observed == [("dev-1", fallback)]


@pytest.mark.asyncio
async def test_failed_lifecycle_lookup_does_not_declare_member_idle():
    host = _RecordingHost()
    handler = _leader_handler(host, SimpleNamespace(get_member=AsyncMock(side_effect=RuntimeError("offline"))))

    await handler.on_member_event(EventMessage.from_event(MemberRestartedEvent(team_name="t", member_name="dev-1")))

    assert host.observed == [("dev-1", MemberStatus.RESTARTING)]


@pytest.mark.asyncio
async def test_reconciled_summary_can_finalize_and_repeat_its_idle_cycle(fast_debounce):
    from openjiuwen.agent_teams.organization.runtime import OrganizationRuntimeManager
    from openjiuwen.agent_teams.runtime.manager import TeamRuntimeManager
    from openjiuwen.agent_teams.runtime.pool import ActiveTeam, RuntimeState

    queue: asyncio.Queue = asyncio.Queue()
    leader = _leader_agent(queue)
    await leader.observe_member_status(LEADER, MemberStatus.READY)
    backend = SimpleNamespace(get_member=AsyncMock(return_value=SimpleNamespace(status="ready")))
    handler = _leader_handler(leader, backend)
    pooled_agent = SimpleNamespace(
        _state=leader.state, lifecycle="persistent", has_in_flight_round=lambda: False,
        is_task_board_settled=AsyncMock(return_value=True), is_shutdown_requested=AsyncMock(return_value=False),
        pause_coordination=AsyncMock(), stop_coordination=AsyncMock(), invalidate_workspace_cache=Mock(),
    )
    runtime = TeamRuntimeManager()
    entry = ActiveTeam(team_name="summary", agent=pooled_agent, current_session_id="s")
    await runtime.pool.add(entry)

    for _ in range(2):
        entry.state = RuntimeState.RUNNING
        await leader.observe_member_status("dev-1", MemberStatus.BUSY)
        assert not await OrganizationRuntimeManager._team_is_idle_settled_for_org_wake(entry)
        await handler.on_member_event(EventMessage.from_event(MemberRestartedEvent(
            team_name="summary", member_name="dev-1",
        )))
        await asyncio.wait_for(leader.stream_controller._idle_marker_task, timeout=1)
        assert queue.get_nowait().payload["event_type"] == "team.idle"
        assert await OrganizationRuntimeManager._team_is_idle_settled_for_org_wake(entry)
        await runtime.finalize(team_name="summary", session_id="s")
        assert (await runtime.pool.get("summary")).state is RuntimeState.PAUSED

    assert pooled_agent.pause_coordination.await_count == 2
    pooled_agent.stop_coordination.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("restart_path", ["cold", "wake", "health", "session"])
@pytest.mark.parametrize("ready_before_notice", [False, True])
async def test_restart_claim_replaces_old_quiescent_status_before_notification(
    tmp_path, fast_debounce, restart_path, ready_before_notice,
):
    """A persisted idle row is claimed before spawn; only a new READY settles it."""
    from openjiuwen.agent_teams.agent.recovery_manager import RecoveryManager
    from openjiuwen.agent_teams.context import reset_session_id, set_session_id
    from openjiuwen.agent_teams.tools.database import DatabaseConfig, DatabaseType, TeamDatabase
    from openjiuwen.agent_teams.tools.team import TeamBackend

    token = set_session_id("restart-activity")
    database = TeamDatabase(DatabaseConfig(
        db_type=DatabaseType.SQLITE, connection_string=str(tmp_path / "team.db"),
    ))
    queue: asyncio.Queue = asyncio.Queue()
    leader = _leader_agent(queue)
    await leader.observe_member_status(LEADER, MemberStatus.BUSY)
    old_status = MemberStatus.DORMANT if restart_path == "wake" else MemberStatus.READY
    published = []

    async def publish(*, topic_id, message):
        published.append(message.event_type)
        await handler.on_member_event(message)

    backend = TeamBackend(
        team_name="t", member_name=LEADER, is_leader=True,
        db=database, messager=SimpleNamespace(publish=publish),
    )
    handler = _leader_handler(leader, backend)
    configurator = SimpleNamespace(
        member_name=LEADER, role=TeamRole.LEADER, team_name="t",
        team_backend=backend, messager=backend.messager,
    )
    manager = SpawnManager(
        state=TeamAgentState(), configurator=configurator, team_agent_getter=lambda: leader,
    )
    manager.build_context_from_db = AsyncMock(return_value=SimpleNamespace(member_name="dev-1"))
    backend._on_member_restarted = manager.restart_teammate

    async def mark_ready():
        assert await database.member.update_member_status("dev-1", "t", MemberStatus.READY.value)
        await handler.on_member_event(EventMessage.from_event(MemberStatusChangedEvent(
            team_name="t", member_name="dev-1", old_status="restarting", new_status="ready",
        )))

    async def spawn(*args, **kwargs):
        # Real recovery callers and the DAO must replace the previous run's
        # READY/DORMANT before an asynchronous child can be considered idle.
        row = await backend.get_member("dev-1")
        assert row.status == MemberStatus.RESTARTING.value
        if ready_before_notice:
            await mark_ready()
        return SimpleNamespace()

    manager.spawn_teammate = AsyncMock(side_effect=spawn)
    try:
        await database.initialize()
        assert await database.team.create_team("t", "Team", LEADER)
        assert await database.member.create_member("dev-1", "t", "Dev", "{}", old_status.value)
        await leader.observe_member_status("dev-1", old_status)
        recovery = RecoveryManager(configurator, manager)
        if restart_path == "cold":
            assert await recovery.recover_team() == ["dev-1"]
        elif restart_path == "wake":
            assert await backend.wake_member("dev-1")
        elif restart_path == "health":
            await manager.on_teammate_unhealthy("dev-1")
        else:
            await recovery.restart_for_session_switch([("dev-1", old_status)], cleanup_first=True)

        manager.spawn_teammate.assert_awaited_once()
        assert published == [TeamEvent.MEMBER_RESTARTED]
        if not ready_before_notice:
            assert (await backend.get_member("dev-1")).status == MemberStatus.RESTARTING.value
            assert leader.state.member_registry.snapshot()["dev-1"] == "restarting"
            assert not leader.state.member_registry.is_idle()
            assert leader.stream_controller._idle_marker_task is None
            assert queue.empty()
            await mark_ready()

        assert (await backend.get_member("dev-1")).status == MemberStatus.READY.value
        assert leader.state.member_registry.snapshot()["dev-1"] == "ready"
        await leader.observe_member_status(LEADER, MemberStatus.READY)
        assert leader.state.member_registry.is_idle()
        await asyncio.wait_for(leader.stream_controller._idle_marker_task, timeout=1)
        assert queue.get_nowait().payload["event_type"] == "team.idle"
        assert queue.empty()
    finally:
        leader.stream_controller.cancel_team_idle()
        await database.close()
        reset_session_id(token)
