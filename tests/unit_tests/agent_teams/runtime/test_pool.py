# coding: utf-8
"""TeamRuntimePool behaviour tests including multi-team-per-session."""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest

from openjiuwen.agent_teams.runtime.pool import (
    ActiveTeam,
    RuntimeState,
    TeamRuntimePool,
)


def _make_team(team_name: str, session_id: str = "s1") -> ActiveTeam:
    return ActiveTeam(
        team_name=team_name,
        agent=MagicMock(name=f"TeamAgent[{team_name}]"),
        current_session_id=session_id,
    )


@pytest.mark.asyncio
async def test_empty_pool_has_no_teams():
    pool = TeamRuntimePool()
    assert await pool.list_team_names() == []
    assert await pool.has_active("alpha") is False
    assert await pool.get("alpha") is None


@pytest.mark.asyncio
async def test_add_get_and_remove_round_trip():
    pool = TeamRuntimePool()
    team = _make_team("alpha")
    await pool.add(team)

    assert await pool.has_active("alpha") is True
    assert (await pool.get("alpha")) is team

    removed = await pool.remove("alpha")
    assert removed is team
    assert await pool.has_active("alpha") is False
    assert await pool.remove("alpha") is None


@pytest.mark.asyncio
async def test_add_replaces_existing_entry():
    pool = TeamRuntimePool()
    first = _make_team("alpha", session_id="s-old")
    second = _make_team("alpha", session_id="s-new")
    await pool.add(first)
    await pool.add(second)
    assert (await pool.get("alpha")) is second
    assert (await pool.list_team_names()) == ["alpha"]


@pytest.mark.asyncio
async def test_multi_team_in_same_session_listed_together():
    pool = TeamRuntimePool()
    await pool.add(_make_team("alpha", session_id="shared"))
    await pool.add(_make_team("beta", session_id="shared"))
    await pool.add(_make_team("gamma", session_id="other"))

    teams = await pool.teams_for_session("shared")
    names = sorted(t.team_name for t in teams)
    assert names == ["alpha", "beta"]

    assert sorted(await pool.list_team_names()) == ["alpha", "beta", "gamma"]


@pytest.mark.asyncio
async def test_lifecycle_state_persists_across_get():
    pool = TeamRuntimePool()
    team = _make_team("alpha")
    team.state = RuntimeState.PAUSED
    await pool.add(team)
    fetched = await pool.get("alpha")
    assert fetched is not None
    assert fetched.state is RuntimeState.PAUSED


@pytest.mark.asyncio
async def test_list_all_info_returns_readonly_snapshots():
    pool = TeamRuntimePool()
    running = _make_team("alpha", session_id="s-running")
    paused = _make_team("beta", session_id="s-paused")
    paused.state = RuntimeState.PAUSED
    await pool.add(running)
    await pool.add(paused)

    snapshots = await pool.list_all_info()

    by_name = {info.team_name: info for info in snapshots}
    assert by_name["alpha"].state is RuntimeState.RUNNING
    assert by_name["alpha"].current_session_id == "s-running"
    assert by_name["alpha"].gate_closed is False
    assert by_name["beta"].state is RuntimeState.PAUSED
    # ActiveTeamInfo is frozen — mutation must fail.
    with pytest.raises(Exception):
        by_name["alpha"].state = RuntimeState.PAUSED  # type: ignore[misc]


@pytest.mark.asyncio
async def test_concurrent_add_remove_keeps_pool_consistent():
    pool = TeamRuntimePool()
    names = [f"team-{i}" for i in range(20)]
    teams = [_make_team(name) for name in names]

    await asyncio.gather(*(pool.add(t) for t in teams))
    snapshot = sorted(await pool.list_team_names())
    assert snapshot == sorted(names)

    await asyncio.gather(*(pool.remove(name) for name in names))
    assert await pool.list_team_names() == []
