# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""The team tool factory wrapper logs only; rendering stays on the tool."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio

from openjiuwen.agent_teams.context import reset_session_id, set_session_id
from openjiuwen.agent_teams.paths import reset_task_openjiuwen_home, set_task_openjiuwen_home
from openjiuwen.agent_teams.tools.database import DatabaseConfig, TeamDatabase
from openjiuwen.agent_teams.tools.team import TeamBackend
from openjiuwen.agent_teams.tools.team_tools import create_team_tools
from openjiuwen.agent_teams.tools.tool_base import TeamTool
from openjiuwen.agent_teams.tools.tool_factory import _wrap_invoke_with_logging
from openjiuwen.agent_teams.tools.tool_permissions import (
    LEADER_ONLY_TOOLS,
    LEADER_TOOLS,
    MEMBER_TOOLS,
    MEMBER_TOOLS_BY_DISPATCH,
    SHARED_TOOLS,
)
from openjiuwen.core.foundation.tool import ToolCard
from openjiuwen.harness.tools.base_tool import ToolOutput


class _CountTool(TeamTool):
    def __init__(self) -> None:
        super().__init__(ToolCard(id="count_tool", name="count_tool", description="count"))

    async def invoke(self, inputs: dict[str, Any], **kwargs: Any) -> ToolOutput:
        return ToolOutput(success=True, data={"count": inputs["count"]})

    def render_for_llm(self, output: ToolOutput) -> str:
        return f"{output.data['count']} item(s)"


@pytest.mark.asyncio
@pytest.mark.level0
async def test_wrapped_invoke_returns_the_structured_result_unchanged() -> None:
    tool = _CountTool()
    _wrap_invoke_with_logging(tool)

    result = await tool.invoke({"count": 3})

    assert type(result) is ToolOutput
    assert result.data == {"count": 3}
    assert tool.render_for_llm(result) == "3 item(s)"


# ---------------------------------------------------------------------------
# swarmflow tool-face opening: the NAME moved to SHARED_TOOLS, the TOOL stays
# behind the factory's resolver gate (T3).
# ---------------------------------------------------------------------------


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


@pytest.mark.level0
def test_swarmflow_name_moved_to_shared_tools_keeps_the_leader_face():
    """A union move: the name joins SHARED_TOOLS, so the leader's union tool
    name set is unchanged and both dispatch flavours of the member face gain
    the name."""
    assert "swarmflow" in SHARED_TOOLS
    assert "swarmflow" not in LEADER_ONLY_TOOLS
    assert "swarmflow" in LEADER_TOOLS
    assert "swarmflow" in MEMBER_TOOLS
    assert "swarmflow" in MEMBER_TOOLS_BY_DISPATCH["scheduled"]


@pytest.mark.level0
def test_teammate_without_a_resolver_keeps_no_swarmflow_tool(backend):
    """The wiring gate: a member whose handles were never injected (subprocess
    spawn, external CLI) has the name in its allowed set but the factory drops
    the tool — the name alone grants nothing."""
    tools = create_team_tools(role="teammate", agent_team=backend)

    assert "swarmflow" in MEMBER_TOOLS  # the name IS in the member face...
    assert all(tool.card.name != "swarmflow" for tool in tools)  # ...but no tool


@pytest.mark.level0
def test_teammate_with_a_resolver_gets_the_swarmflow_tool(backend):
    """An in-process teammate with the shared handles (resolver non-None)
    gets the real tool — the opening this card delivers."""
    tools = create_team_tools(
        role="teammate",
        agent_team=backend,
        swarmflow_model_resolver=lambda name: None,
    )

    assert any(tool.card.name == "swarmflow" for tool in tools)


@pytest.mark.level0
def test_leader_swarmflow_face_is_unchanged_by_the_move(backend):
    """Leader regression: with and without the resolver the leader sees
    exactly what it saw when the name sat in LEADER_ONLY_TOOLS."""
    wired = create_team_tools(
        role="leader",
        agent_team=backend,
        swarmflow_model_resolver=lambda name: None,
    )
    assert any(tool.card.name == "swarmflow" for tool in wired)

    unwired = create_team_tools(role="leader", agent_team=backend)
    assert all(tool.card.name != "swarmflow" for tool in unwired)


@pytest.mark.level0
def test_async_task_control_tools_stay_leader_only():
    """Teammates control their runs through the swarmflow tool's own
    resume_id + action surface; the raw async_task_* inspectors stay
    leader-only (Part B open-decision 5: do not open)."""
    for name in ("async_tasks_list", "async_task_output", "async_task_cancel"):
        assert name in LEADER_ONLY_TOOLS
        assert name not in MEMBER_TOOLS


@pytest.mark.level0
def test_swarmflow_description_is_shared_across_roles(backend):
    """The factory no longer picks a variant by role: leader and teammate
    both render the single shared description."""
    teammate_tools = create_team_tools(
        role="teammate",
        agent_team=backend,
        swarmflow_model_resolver=lambda name: None,
    )
    teammate_tool = next(t for t in teammate_tools if t.card.name == "swarmflow")

    leader_tools = create_team_tools(
        role="leader",
        agent_team=backend,
        swarmflow_model_resolver=lambda name: None,
    )
    leader_tool = next(t for t in leader_tools if t.card.name == "swarmflow")

    assert teammate_tool.card.description == leader_tool.card.description
