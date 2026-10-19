# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Boundary validation of ``display_name`` across the five spawn tools.

Schema ``required`` only binds the hosting LLM; MCP clients call ``invoke``
directly without validating against ``input_params``. A missing or blank
``display_name`` used to escape the boundary as ``None`` and resurface as a
deep ``AgentCard`` pydantic error (or, for the HITT pair, a silent fallback
to an i18n default roster name).
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
import pytest_asyncio

from openjiuwen.agent_teams.context import reset_session_id, set_session_id
from openjiuwen.agent_teams.messager import Messager
from openjiuwen.agent_teams.tools.database import DatabaseConfig, DatabaseType, TeamDatabase
from openjiuwen.agent_teams.tools.locales import make_translator
from openjiuwen.agent_teams.tools.team import TeamBackend
from openjiuwen.agent_teams.tools.tool_member import (
    SpawnBridgeAgentTool,
    SpawnExternalCliTool,
    SpawnHumanAgentTool,
    SpawnPassiveHumanTool,
    SpawnTeammateTool,
)

SPAWN_TOOL_NAMES = (
    "spawn_teammate",
    "spawn_human_agent",
    "spawn_passive_human",
    "spawn_bridge_agent",
    "spawn_external_cli",
)

_INVALID_FORMS = (
    pytest.param({}, id="omitted"),
    pytest.param({"display_name": None}, id="null"),
    pytest.param({"display_name": ""}, id="empty"),
    pytest.param({"display_name": "   "}, id="blank"),
)


@pytest_asyncio.fixture
async def backend():
    token = set_session_id("spawn-display-name-session")
    database = TeamDatabase(DatabaseConfig(db_type=DatabaseType.SQLITE, connection_string=":memory:"))
    try:
        await database.initialize()
        team = TeamBackend(
            team_name="spteam",
            member_name="leader1",
            is_leader=True,
            db=database,
            messager=AsyncMock(spec=Messager),
        )
        await team.build_team(
            display_name="spteam",
            desc="validation fixtures",
            leader_display_name="L",
            leader_desc="leader persona",
        )
        # Unlock spawn_passive_human's runtime gate so its cases exercise the
        # display_name check, not the group_chat rejection.
        team.conversation_mode = "group_chat"
        yield team
    finally:
        await database.close()
        reset_session_id(token)


def _make_tool(team: TeamBackend, tool_name: str):
    """Construct the named spawn tool. HITT / Bridge / external-CLI stay
    disabled on the fixture backend, so a display_name failure below also
    proves the check fires before the capability gates."""
    tools = {
        "spawn_teammate": SpawnTeammateTool,
        "spawn_human_agent": SpawnHumanAgentTool,
        "spawn_passive_human": SpawnPassiveHumanTool,
        "spawn_bridge_agent": SpawnBridgeAgentTool,
        "spawn_external_cli": SpawnExternalCliTool,
    }
    return tools[tool_name](team, make_translator("en"))


@pytest.mark.asyncio
@pytest.mark.level0
@pytest.mark.parametrize("display_name_form", _INVALID_FORMS)
@pytest.mark.parametrize("tool_name", SPAWN_TOOL_NAMES)
async def test_spawn_tools_reject_missing_display_name(backend, tool_name, display_name_form):
    """Every spawn tool fails fast at the boundary, naming the parameter.

    Before the fix the first three tools let a pydantic ``AgentCard`` error
    escape ``invoke`` (the model only saw ``AgentCard.name``, never
    ``display_name``), and the HITT pair silently registered the member
    under an i18n default name.
    """
    tool = _make_tool(backend, tool_name)

    result = await tool.invoke({"member_name": "dev-1", **display_name_form})

    assert result.success is False
    assert "'display_name' is required" in (result.error or "")


@pytest.mark.asyncio
@pytest.mark.level0
async def test_spawn_teammate_with_valid_display_name_still_succeeds(backend):
    """A well-formed display_name passes the boundary and spawns as before."""
    tool = _make_tool(backend, "spawn_teammate")

    result = await tool.invoke({"member_name": "dev-1", "display_name": "Dev", "desc": "helper"})

    assert result.success is True, result.error
    assert result.data["display_name"] == "Dev"
