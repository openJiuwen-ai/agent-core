# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Restart / recover must not replay the member's first-start instruction.

``SpawnManager.restart_teammate`` drives every fault-tolerance path
(``recover_team`` / ``on_teammate_unhealthy`` / session switch). It must
re-spawn the member with ``initial_message=None`` so no harness.send is
triggered — the member re-subscribes and recovers via its mailbox, and
only real pending messages drive a round. Replaying the persisted
``teammate.prompt`` here would re-trigger the first round on every
recovery.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openjiuwen.agent_teams.agent.spawn_manager import SpawnManager
from openjiuwen.agent_teams.agent.state import TeamAgentState
from openjiuwen.agent_teams.schema.status import ExecutionStatus, MemberStatus


class _ExecutionStatusDao:
    def __init__(self, events: list[str]) -> None:
        self.execution_status = ExecutionStatus.RUNNING.value
        self.events = events

    async def reset_member_execution_status(self, member_name: str, team_name: str, status: str) -> bool:
        self.events.append("reset")
        self.execution_status = status
        return True

    async def update_member_execution_status(self, member_name: str, team_name: str, status: str) -> bool:
        if self.execution_status != ExecutionStatus.IDLE.value or status != ExecutionStatus.STARTING.value:
            return False
        self.execution_status = status
        return True

    async def update_member_status(self, member_name: str, team_name: str, status: str) -> bool:
        self.events.append(f"status:{status}")
        return True

    async def get_member(self, member_name: str, team_name: str):
        self.events.append("get_member")
        return SimpleNamespace(status=MemberStatus.READY.value)


def _make_spawn_manager(member_dao: _ExecutionStatusDao) -> SpawnManager:
    """Build a SpawnManager whose backend would still expose a prompt.

    ``get_member`` is wired to return a row carrying a non-empty ``prompt``
    so the test proves restart does not read it.
    """
    team_backend = SimpleNamespace(
        get_member=AsyncMock(return_value=SimpleNamespace(prompt="original first-start task")),
        db=SimpleNamespace(member=member_dao),
    )
    configurator = SimpleNamespace(
        member_name="leader",
        team_backend=team_backend,
        team_name="t",
    )
    return SpawnManager(
        state=TeamAgentState(),
        configurator=configurator,
        team_agent_getter=lambda: None,
    )


@pytest.mark.asyncio
@pytest.mark.level0
async def test_restart_owns_member_across_cleanup_and_spawn():
    """A direct restart excludes an unhealthy restart through the full flow."""
    events: list[str] = []
    member_dao = _ExecutionStatusDao(events)
    sm = _make_spawn_manager(member_dao)
    cleanup_started = asyncio.Event()
    release_cleanup = asyncio.Event()

    async def cleanup(member_name: str) -> None:
        events.append("cleanup")
        cleanup_started.set()
        await release_cleanup.wait()

    async def spawn(*args, **kwargs):
        events.append("spawn")
        started = await member_dao.update_member_execution_status("dev-1", "t", ExecutionStatus.STARTING.value)
        if not started:
            raise RuntimeError("stale execution status blocked the fresh round")
        return SimpleNamespace()

    sm.cleanup_teammate = AsyncMock(side_effect=cleanup)
    sm.build_context_from_db = AsyncMock(return_value=SimpleNamespace(member_name="dev-1"))
    sm._spawn_teammate_inner = AsyncMock(side_effect=spawn)
    sm.publish_restart_event = AsyncMock()

    winner = asyncio.create_task(sm.restart_teammate("dev-1", max_retries=1))
    await cleanup_started.wait()
    loser = asyncio.create_task(sm.on_teammate_unhealthy("dev-1"))
    await asyncio.sleep(0)
    release_cleanup.set()
    winner_result, loser_result = await asyncio.gather(winner, loser)

    assert winner_result is True
    assert loser_result is None
    assert member_dao.execution_status == ExecutionStatus.STARTING.value
    assert events == ["cleanup", "reset", "spawn"]
    assert "dev-1" not in sm._spawning
    sm.cleanup_teammate.assert_awaited_once_with("dev-1")
    sm._spawn_teammate_inner.assert_awaited_once()
    assert sm._spawn_teammate_inner.await_args.kwargs["initial_message"] is None
    sm.publish_restart_event.assert_awaited_once_with("dev-1", 1)
    sm._configurator.team_backend.get_member.assert_not_awaited()
