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

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openjiuwen.agent_teams.agent.spawn_manager import SpawnManager
from openjiuwen.agent_teams.agent.state import TeamAgentState


def _make_spawn_manager() -> SpawnManager:
    """Build a SpawnManager whose backend would still expose a prompt.

    ``get_member`` is wired to return a row carrying a non-empty ``prompt``
    so the test proves restart does not read it.
    """
    team_backend = SimpleNamespace(
        get_member=AsyncMock(return_value=SimpleNamespace(prompt="original first-start task")),
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
async def test_restart_teammate_passes_no_initial_message():
    """restart re-spawns with initial_message=None and never reads the prompt."""
    sm = _make_spawn_manager()
    sm.cleanup_teammate = AsyncMock()
    sm.build_context_from_db = AsyncMock(return_value=SimpleNamespace(member_name="dev-1"))
    sm.spawn_teammate = AsyncMock(return_value=SimpleNamespace())
    sm.publish_restart_event = AsyncMock()

    ok = await sm.restart_teammate("dev-1")

    assert ok is True
    sm.spawn_teammate.assert_awaited_once()
    assert sm.spawn_teammate.await_args.kwargs["initial_message"] is None
    sm._configurator.team_backend.get_member.assert_not_awaited()
