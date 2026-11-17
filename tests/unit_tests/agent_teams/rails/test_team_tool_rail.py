# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""TeamToolRail keeps the coordination surface direct under progressive exposure."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio

from openjiuwen.agent_teams.context import reset_session_id, set_session_id
from openjiuwen.agent_teams.paths import reset_task_openjiuwen_home, set_task_openjiuwen_home
from openjiuwen.agent_teams.rails.team_tool_rail import TeamToolRail, declare_team_tool_direct
from openjiuwen.agent_teams.tools.database import DatabaseConfig, TeamDatabase
from openjiuwen.agent_teams.tools.team import TeamBackend
from openjiuwen.core.foundation.tool import ToolCard, ToolExposure
from openjiuwen.core.single_agent.ability_manager import AbilityManager


def test_declare_team_tool_direct_marks_undeclared_card():
    card = ToolCard(id="team.send_message", name="send_message")

    declare_team_tool_direct(card)

    assert card.exposure is ToolExposure.DIRECT
    assert card.get_exposure_declared() is True


def test_declare_team_tool_direct_keeps_explicit_exposure():
    card = ToolCard(id="team.rare", name="rare", exposure=ToolExposure.DEFERRED)

    declare_team_tool_direct(card)

    assert card.exposure is ToolExposure.DEFERRED
    assert card.get_exposure_declared() is None


@pytest_asyncio.fixture
async def backend(tmp_path):
    home = set_task_openjiuwen_home(tmp_path / "home")
    db = TeamDatabase(DatabaseConfig(connection_string=":memory:"))
    await db.initialize()
    await db.team.create_team("team", "Team", "leader")
    await db.member.create_member("leader", "team", "Leader", "{}", "ready", role="leader")
    token = set_session_id("session")
    try:
        yield TeamBackend("team", "leader", True, db, AsyncMock())
    finally:
        reset_session_id(token)
        await db.close()
        reset_task_openjiuwen_home(home)


@pytest.mark.parametrize("role", ["leader", "teammate"])
@pytest.mark.asyncio
async def test_team_tools_stay_direct_under_progressive_policy(backend, role):
    """A member built with progressive_tool enabled still advertises every team tool."""
    manager = AbilityManager(owner_id=f"{role}-agent")
    manager.set_tool_exposure_policy(True, direct_tool_names={"tool_search", "tool_call"})
    agent = SimpleNamespace(ability_manager=manager)
    rail = TeamToolRail(team_backend=backend, role=role)

    rail.init(agent)

    team_tool_names = {tool.card.name for tool in rail._tools}
    assert team_tool_names
    for name in team_tool_names:
        card = manager.get(name)
        assert card is not None, name
        assert card.exposure is ToolExposure.DIRECT, name

    rail.uninit(agent)
    assert all(manager.get(name) is None for name in team_tool_names)
