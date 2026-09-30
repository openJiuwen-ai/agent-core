# coding: utf-8
"""Coverage for the PAUSED lifecycle additions in Step 4.

The Pause path needs ``MemberStatus.PAUSED`` to participate in the
member transition table (so recovery can flip PAUSED back to RESTARTING)
and the ``lifecycle`` hint to round-trip through the team namespace.
The deeper integration with ``CoordinationKernel.pause`` is exercised
in the persistent-team integration tests; here we only assert the
contracts that pause relies on.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openjiuwen.agent_teams.runtime.manager import TeamRuntimeManager
from openjiuwen.agent_teams.runtime.metadata import (
    merge_team_namespace,
    read_team_namespace,
    write_team_namespace,
)
from openjiuwen.agent_teams.runtime.pool import RuntimeState
from openjiuwen.agent_teams.schema.status import (
    MEMBER_TRANSITIONS,
    MemberStatus,
    is_valid_transition,
)


class _StubSession:
    def __init__(self) -> None:
        self.state: dict = {}

    def update_state(self, data: dict) -> None:
        self.state.update(data)

    def get_state(self, key=None):
        if key is None:
            return self.state
        return self.state.get(key)


def test_paused_to_restarting_transition_is_valid():
    assert is_valid_transition(
        MemberStatus.PAUSED,
        MemberStatus.RESTARTING,
        MEMBER_TRANSITIONS,
    )


def test_paused_to_ready_transition_is_valid():
    assert is_valid_transition(
        MemberStatus.PAUSED,
        MemberStatus.READY,
        MEMBER_TRANSITIONS,
    )


def test_ready_and_busy_can_enter_paused():
    assert is_valid_transition(MemberStatus.READY, MemberStatus.PAUSED, MEMBER_TRANSITIONS)
    assert is_valid_transition(MemberStatus.BUSY, MemberStatus.PAUSED, MEMBER_TRANSITIONS)


def test_paused_cannot_jump_back_to_busy_directly():
    assert not is_valid_transition(
        MemberStatus.PAUSED,
        MemberStatus.BUSY,
        MEMBER_TRANSITIONS,
    )


def test_lifecycle_hint_round_trips_through_team_namespace():
    session = _StubSession()
    write_team_namespace(session, "t1", {"spec": {"team_name": "t1"}})
    merge_team_namespace(session, "t1", {"lifecycle": "paused"})
    assert read_team_namespace(session, "t1")["lifecycle"] == "paused"


def test_lifecycle_hint_overrides_previous_value():
    session = _StubSession()
    write_team_namespace(session, "t1", {"lifecycle": "running"})
    merge_team_namespace(session, "t1", {"lifecycle": "paused"})
    assert read_team_namespace(session, "t1")["lifecycle"] == "paused"


# ---------------------------------------------------------------------------
# Pause / interact while the run cycle is still starting
# ---------------------------------------------------------------------------


def _manager_with_entry(lifecycle_state: str):
    agent = SimpleNamespace(
        coordination=SimpleNamespace(lifecycle_state=lifecycle_state),
        pause_coordination=AsyncMock(),
    )
    gate = SimpleNamespace(admit=AsyncMock(return_value=None))
    entry = SimpleNamespace(agent=agent, state=RuntimeState.RUNNING, interact_gate=gate)
    manager = TeamRuntimeManager()
    manager._resolve_entry = AsyncMock(return_value=entry)
    return manager, entry


@pytest.mark.asyncio
@pytest.mark.parametrize("lifecycle_state", ["idle", "paused"])
async def test_voice_pause_rejected_while_run_cycle_is_starting(lifecycle_state):
    """A voice barge-in between activate and the end of kernel.start must not
    park the cycle: its input has not reached the harness yet and would be
    dropped with the stream. ``idle`` = CREATE before start, ``paused`` =
    RESUME_FROM_PAUSE reusing the previous cycle's kernel."""
    manager, entry = _manager_with_entry(lifecycle_state)

    assert await manager.pause(team_name="t1", session_id="s1", voice=True) is False
    entry.agent.pause_coordination.assert_not_awaited()
    assert entry.state is RuntimeState.RUNNING


@pytest.mark.asyncio
@pytest.mark.parametrize("lifecycle_state", ["idle", "paused"])
async def test_default_pause_keeps_parking_starting_cycle(lifecycle_state):
    """Non-voice callers keep the original pause semantics."""
    manager, entry = _manager_with_entry(lifecycle_state)

    assert await manager.pause(team_name="t1", session_id="s1") is True
    entry.agent.pause_coordination.assert_awaited_once()
    assert entry.state is RuntimeState.PAUSED
    assert manager.consume_voice_pause("t1") is False


@pytest.mark.asyncio
@pytest.mark.parametrize("voice", [False, True])
async def test_pause_of_parked_entry_stays_idempotent(voice):
    manager, entry = _manager_with_entry("paused")
    entry.state = RuntimeState.PAUSED

    assert await manager.pause(team_name="t1", session_id="s1", voice=voice) is True


@pytest.mark.asyncio
async def test_default_pause_parks_running_cycle():
    manager, entry = _manager_with_entry("running")

    assert await manager.pause(team_name="t1", session_id="s1") is True
    entry.agent.pause_coordination.assert_awaited_once()
    assert entry.state is RuntimeState.PAUSED
    assert manager.consume_voice_pause("t1") is False


@pytest.mark.asyncio
async def test_voice_pause_parks_running_cycle_in_voice_scope():
    from openjiuwen.agent_teams.runtime.voice import is_voice_pause

    manager, entry = _manager_with_entry("running")
    seen: list[bool] = []
    entry.agent.pause_coordination = AsyncMock(side_effect=lambda: seen.append(is_voice_pause()))

    assert await manager.pause(team_name="t1", session_id="s1", voice=True) is True
    assert seen == [True]
    assert is_voice_pause() is False
    assert entry.state is RuntimeState.PAUSED
    # The ending run cycle flushes its trace off the event loop, once.
    assert manager.consume_voice_pause("t1") is True
    assert manager.consume_voice_pause("t1") is False


@pytest.mark.asyncio
async def test_voice_interact_waits_for_starting_cycle_to_preserve_turn_order():
    manager, entry = _manager_with_entry("idle")

    result = await manager.interact("second turn", team_name="t1", session_id="s1", voice=True)

    assert not result
    assert result.reason == "runtime_starting"
    entry.interact_gate.admit.assert_not_awaited()


@pytest.mark.asyncio
async def test_default_interact_goes_to_gate_while_cycle_is_starting():
    manager, entry = _manager_with_entry("idle")

    result = await manager.interact("second turn", team_name="t1", session_id="s1")

    assert result.reason == "gate_closed"
    entry.interact_gate.admit.assert_awaited_once()


def _spawn_manager_tracking_overlap():
    import asyncio

    from openjiuwen.agent_teams.agent.spawn_manager import SpawnManager

    spawn_manager = object.__new__(SpawnManager)
    spawn_manager.spawned_handles = {"m1": object(), "m2": object()}
    state = {"live": 0, "max_live": 0}

    async def _cleanup(member_name):
        state["live"] += 1
        state["max_live"] = max(state["max_live"], state["live"])
        await asyncio.sleep(0.01)
        state["live"] -= 1

    spawn_manager.cleanup_teammate = _cleanup
    return spawn_manager, state


@pytest.mark.asyncio
async def test_shutdown_all_handles_stays_serial_by_default():
    spawn_manager, state = _spawn_manager_tracking_overlap()

    await spawn_manager.shutdown_all_handles()

    assert state["max_live"] == 1
    assert spawn_manager.spawned_handles == {}


@pytest.mark.asyncio
async def test_shutdown_all_handles_concurrent_under_voice_pause():
    from openjiuwen.agent_teams.runtime.voice import voice_pause_scope

    spawn_manager, state = _spawn_manager_tracking_overlap()

    with voice_pause_scope():
        await spawn_manager.shutdown_all_handles()

    assert state["max_live"] == 2
    assert spawn_manager.spawned_handles == {}
