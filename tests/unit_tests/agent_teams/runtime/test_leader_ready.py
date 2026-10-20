# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unit tests for the leader-ready waiter and its runtime manager wiring."""

import asyncio
from typing import Any

import pytest

from openjiuwen.agent_teams.runtime.manager import TeamRuntimeManager
from openjiuwen.agent_teams.runtime.pool import ActiveTeam, RuntimeState


class _CallbackAgent:
    """Agent double exposing only the leader-started callback face."""

    def __init__(self) -> None:
        self.leader_started_callback: Any = None

    def set_leader_started_callback(self, callback: Any) -> None:
        self.leader_started_callback = callback


async def _seed_pool_entry(
    manager: TeamRuntimeManager,
    team_name: str,
    session_id: str,
    agent: _CallbackAgent,
) -> None:
    await manager.pool.add(
        ActiveTeam(
            team_name=team_name,
            agent=agent,
            current_session_id=session_id,
            state=RuntimeState.RUNNING,
        )
    )


class TestLeaderReadyWaiter:
    @pytest.mark.asyncio
    @pytest.mark.level0
    async def test_set_ready_resolves_wait(self) -> None:
        manager = TeamRuntimeManager()
        waiter = manager.watch_next_leader_ready(team_name="t1", session_id="s1")
        waiter.set_ready()
        await asyncio.wait_for(waiter.wait(), timeout=1.0)

    @pytest.mark.asyncio
    @pytest.mark.level1
    async def test_cancel_discards_waiter_and_blocks_wait(self) -> None:
        manager = TeamRuntimeManager()
        waiter = manager.watch_next_leader_ready(team_name="t1", session_id="s1")
        waiter.cancel()
        waiter.cancel()
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(waiter.wait(), timeout=0.05)
        waiter.set_ready()
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(waiter.wait(), timeout=0.05)

    @pytest.mark.asyncio
    @pytest.mark.level1
    async def test_watch_replaces_previous_waiter(self) -> None:
        manager = TeamRuntimeManager()
        agent = _CallbackAgent()
        await _seed_pool_entry(manager, "t1", "s1", agent)
        first = manager.watch_next_leader_ready(team_name="t1", session_id="s1")
        second = manager.watch_next_leader_ready(team_name="t1", session_id="s1")

        manager._bind_leader_ready_callback(team_name="t1", session_id="s1", agent=agent)
        await agent.leader_started_callback()

        await asyncio.wait_for(second.wait(), timeout=1.0)
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(first.wait(), timeout=0.05)


class TestBindLeaderReadyCallback:
    @pytest.mark.asyncio
    @pytest.mark.level0
    async def test_matching_activation_publishes_ready_and_clears_callback(self) -> None:
        manager = TeamRuntimeManager()
        agent = _CallbackAgent()
        await _seed_pool_entry(manager, "t1", "s1", agent)
        waiter = manager.watch_next_leader_ready(team_name="t1", session_id="s1")

        manager._bind_leader_ready_callback(team_name="t1", session_id="s1", agent=agent)
        await agent.leader_started_callback()

        await asyncio.wait_for(waiter.wait(), timeout=1.0)
        assert agent.leader_started_callback is None

    @pytest.mark.asyncio
    @pytest.mark.level1
    async def test_identity_mismatch_does_not_publish_ready(self) -> None:
        manager = TeamRuntimeManager()
        pool_agent = _CallbackAgent()
        bound_agent = _CallbackAgent()
        await _seed_pool_entry(manager, "t1", "s1", pool_agent)
        waiter = manager.watch_next_leader_ready(team_name="t1", session_id="s1")

        manager._bind_leader_ready_callback(team_name="t1", session_id="s1", agent=bound_agent)
        await bound_agent.leader_started_callback()

        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(waiter.wait(), timeout=0.05)
