# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Tests for steering the leader's in-flight round.

The reason this is separate from ``interact`` is the idle case. Interact starts
a leader round when the team has nothing running; steering must report that
there was nothing to steer instead, so a stale steer never becomes work the
user did not ask for.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from openjiuwen.agent_teams.runtime.manager import TeamRuntimeManager
from openjiuwen.agent_teams.runtime.pool import ActiveTeam, RuntimeState


def _agent(
    *,
    active_round: object | None,
    steered: bool = True,
    unsupported: bool = False,
) -> MagicMock:
    """A TeamAgent stub whose runtime reports whether a round is in flight.

    ``steered`` is what ``TeamAgent.steer`` returns -- False meaning the harness
    found no round when it came to queue the text. That is a different moment
    from the in-flight check beforehand, and the two can disagree.

    ``unsupported`` makes ``steer`` raise ``NotImplementedError``, the way it does
    for a runtime with no round-scoped steering.

    A stub with an `active_round` attribute is how this file missed the bug that
    mattered: the real default runtime is a ``TeamHarness``, which had neither
    ``steer`` nor ``active_round``, so `steer_leader` raised ``AttributeError``
    for every real team while these tests stayed green. The integration test at
    the bottom of the file is the one that would have caught it; these remain for
    the branch logic they can reach cheaply.
    """
    agent = MagicMock()
    agent.has_in_flight_round = MagicMock(return_value=active_round is not None)
    if unsupported:
        agent.steer = AsyncMock(side_effect=NotImplementedError("no steer_round"))
    else:
        agent.steer = AsyncMock(return_value=steered)
    agent.harness = SimpleNamespace(active_round=active_round)
    return agent


async def _manager_with(agent: MagicMock) -> TeamRuntimeManager:
    manager = TeamRuntimeManager()
    await manager.pool.add(
        ActiveTeam(
            team_name="alpha",
            agent=agent,
            current_session_id="session-1",
            state=RuntimeState.RUNNING,
        )
    )
    return manager


@pytest.mark.asyncio
async def test_steering_an_in_flight_round_reaches_the_leader() -> None:
    agent = _agent(active_round=object())
    manager = await _manager_with(agent)

    agent.harness = SimpleNamespace(active_round=SimpleNamespace(round_id=7))
    result = await manager.steer_leader(
        "prefer the async client",
        team_name="alpha",
        session_id="session-1",
        steer_id="req-9",
    )

    assert result.ok is True
    # No message id: steering writes no bus message, like deliver_to_leader.
    assert result.message_id is None
    # The id must reach the runtime -- STEER_APPLIED's `dropped` is built from it,
    # so dropping it here makes a rail-removed steer look applied. And the round
    # is named, so a steer cannot land in a round that replaced the intended one.
    agent.steer.assert_awaited_once_with(
        "prefer the async client", steer_id="req-9", expected_round_id=7
    )


@pytest.mark.asyncio
async def test_an_idle_leader_is_rejected_and_starts_nothing() -> None:
    """The load-bearing case, and the reason this is not a variant of interact.

    Interact would start a leader round here. A steer must not: the user was
    correcting work in flight, and there is no work in flight.
    """
    agent = _agent(active_round=None)
    manager = await _manager_with(agent)

    result = await manager.steer_leader("too late", team_name="alpha", session_id="session-1")

    assert result.ok is False
    assert result.reason == "no_active_round"
    agent.steer.assert_not_awaited()


@pytest.mark.asyncio
async def test_no_runtime_for_the_target_is_not_active() -> None:
    manager = TeamRuntimeManager()

    result = await manager.steer_leader("hello", team_name="ghost", session_id="session-1")

    assert result.ok is False
    assert result.reason == "not_active"


@pytest.mark.asyncio
async def test_a_shutting_down_runtime_reports_gate_closed() -> None:
    agent = _agent(active_round=object())
    manager = await _manager_with(agent)
    entry = await manager.pool.get("alpha")
    assert entry is not None
    await entry.interact_gate.close_and_drain()

    result = await manager.steer_leader("late", team_name="alpha", session_id="session-1")

    assert result.ok is False
    assert result.reason == "gate_closed"
    agent.steer.assert_not_awaited()


@pytest.mark.asyncio
async def test_an_agent_without_a_harness_is_rejected_not_crashed() -> None:
    """A member still starting up has no harness yet."""
    agent = MagicMock()
    agent.steer = AsyncMock()
    agent.has_in_flight_round = MagicMock(return_value=False)
    agent.harness = None
    manager = await _manager_with(agent)

    result = await manager.steer_leader("hi", team_name="alpha", session_id="session-1")

    assert result.ok is False
    assert result.reason == "no_active_round"
    agent.steer.assert_not_awaited()


@pytest.mark.asyncio
async def test_the_idle_check_happens_before_the_gate_ticket() -> None:
    """A guaranteed no-op must not queue behind live traffic.

    If the idle check ran after admission, a steer with nothing to steer would
    wait for the gate before discovering it had nothing to do.
    """
    agent = _agent(active_round=None)
    manager = await _manager_with(agent)
    entry = await manager.pool.get("alpha")
    assert entry is not None
    entry.interact_gate.admit = AsyncMock(side_effect=AssertionError("gate was taken"))

    result = await manager.steer_leader("nothing", team_name="alpha", session_id="session-1")

    assert result.reason == "no_active_round"


@pytest.mark.asyncio
async def test_a_round_that_ends_in_flight_is_reported_as_no_active_round() -> None:
    """The race the pre-check cannot win.

    ``active_round`` says a round is live, then the gate ticket and the harness
    control-queue hop both yield to the event loop, and the round finishes in
    between. The harness refuses, and that refusal has to reach the caller --
    reporting success would claim a finished round took the correction.
    """
    agent = _agent(active_round=object(), steered=False)
    manager = await _manager_with(agent)

    result = await manager.steer_leader("too late", team_name="alpha", session_id="session-1")

    assert result.ok is False
    assert result.reason == "no_active_round"
    # It really was attempted; this is not the pre-check rejecting it early.
    agent.steer.assert_awaited_once()


@pytest.mark.asyncio
async def test_a_runtime_without_round_steering_says_so_instead() -> None:
    """A CLI-backed leader is not a race, and must not be reported as one.

    Its runtime's own ``steer`` buffers rather than injecting, so there is no
    round-scoped delivery to attempt. Calling that "no_active_round" would send
    the user -- and whoever reads the log -- hunting for a timing problem that
    does not exist.
    """
    agent = _agent(active_round=object(), unsupported=True)
    manager = await _manager_with(agent)
    entry = await manager.pool.get("alpha")
    assert entry is not None

    result = await manager.steer_leader("hi", team_name="alpha", session_id="session-1")

    assert result.ok is False
    assert result.reason == "unsupported_runtime"
    # The NotImplementedError must not escape, and the ticket must still drain.
    assert entry.interact_gate._inflight == 0


@pytest.mark.asyncio
async def test_the_gate_ticket_is_released_even_when_the_harness_refuses() -> None:
    """A refusal must not leak the inflight count, or shutdown never drains."""
    agent = _agent(active_round=object(), steered=False)
    manager = await _manager_with(agent)
    entry = await manager.pool.get("alpha")
    assert entry is not None

    result = await manager.steer_leader("too late", team_name="alpha", session_id="session-1")

    assert result.ok is False
    # close_and_drain waits on this reaching zero.
    assert entry.interact_gate._inflight == 0
