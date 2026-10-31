# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""M1-C1: lock team_mode spawn gate + temporary clean_team wiring.

Locks the jiuwen Swarm Skill M1 contract for agent-core:
- ``default`` / ``hybrid`` keep spawn_* tools (exclude empty)
- ``predefined`` strips every spawn_* tool
- ``temporary`` lifecycle exposes ``clean_team``; ``persistent`` does not
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from openjiuwen.agent_teams.agent.agent_configurator import (
    _resolve_team_mode,
    _spawn_tools_excluded_for_mode,
)
from openjiuwen.agent_teams.schema.blueprint import TeamAgentSpec
from openjiuwen.agent_teams.schema.deep_agent_spec import DeepAgentSpec
from openjiuwen.agent_teams.schema.team import TeamMemberSpec, TeamRole
from openjiuwen.agent_teams.tools.team_tools import create_team_tools


def _minimal_spec(**overrides) -> TeamAgentSpec:
    agents = {"leader": DeepAgentSpec()}
    base: dict = {"agents": agents, "team_name": "m1_mode_team"}
    base.update(overrides)
    return TeamAgentSpec(**base)


@pytest.mark.level0
@pytest.mark.parametrize(
    ("team_mode", "expect_exclude"),
    [
        ("default", False),
        ("hybrid", False),
        ("predefined", True),
    ],
)
def test_spawn_tools_excluded_for_mode(team_mode: str, expect_exclude: bool) -> None:
    excluded = _spawn_tools_excluded_for_mode(team_mode)
    if expect_exclude:
        assert "spawn_teammate" in excluded
        assert "spawn_human_agent" in excluded
        assert "spawn_bridge_agent" in excluded
        assert "spawn_external_cli" in excluded
    else:
        assert excluded == []


@pytest.mark.level0
def test_explicit_default_mode_keeps_spawn_even_with_seed_roster() -> None:
    """Flexible专班 may still declare seeds; explicit default must keep spawn."""
    pre = TeamMemberSpec(
        member_name="seed_dev",
        display_name="Seed Dev",
        role_type=TeamRole.TEAMMATE,
        desc="seed",
    )
    spec = _minimal_spec(predefined_members=[pre], team_mode="default")
    assert _resolve_team_mode(spec) == "default"
    assert _spawn_tools_excluded_for_mode(_resolve_team_mode(spec)) == []


@pytest.mark.level0
def test_explicit_hybrid_mode_keeps_spawn() -> None:
    pre = TeamMemberSpec(
        member_name="seed_dev",
        display_name="Seed Dev",
        role_type=TeamRole.TEAMMATE,
        desc="seed",
    )
    spec = _minimal_spec(predefined_members=[pre], team_mode="hybrid")
    assert _resolve_team_mode(spec) == "hybrid"
    assert _spawn_tools_excluded_for_mode(_resolve_team_mode(spec)) == []


@pytest.mark.level0
def test_explicit_predefined_mode_strips_spawn() -> None:
    pre = TeamMemberSpec(
        member_name="seed_dev",
        display_name="Seed Dev",
        role_type=TeamRole.TEAMMATE,
        desc="seed",
    )
    spec = _minimal_spec(predefined_members=[pre], team_mode="predefined")
    assert _resolve_team_mode(spec) == "predefined"
    excluded = _spawn_tools_excluded_for_mode(_resolve_team_mode(spec))
    assert "spawn_teammate" in excluded


@pytest.mark.level0
def test_leader_tools_respect_mode_exclude_gate() -> None:
    """End-to-end: exclude list from mode gate reaches create_team_tools."""
    agent_team = AsyncMock()
    agent_team.hitt_enabled = MagicMock(return_value=False)
    agent_team.bridge_enabled = MagicMock(return_value=False)
    agent_team.external_cli_kinds = MagicMock(return_value=frozenset())
    agent_team.is_leader = True

    for mode in ("default", "hybrid"):
        tools = create_team_tools(
            role="leader",
            agent_team=agent_team,
            exclude_tools=set(_spawn_tools_excluded_for_mode(mode)),
            lifecycle="temporary",
        )
        names = {tool.card.name for tool in tools}
        assert "spawn_teammate" in names
        assert "clean_team" in names

    tools_pre = create_team_tools(
        role="leader",
        agent_team=agent_team,
        exclude_tools=set(_spawn_tools_excluded_for_mode("predefined")),
        lifecycle="temporary",
    )
    names_pre = {tool.card.name for tool in tools_pre}
    assert "spawn_teammate" not in names_pre
    assert "clean_team" in names_pre


@pytest.mark.level0
def test_persistent_lifecycle_drops_clean_team() -> None:
    agent_team = AsyncMock()
    agent_team.hitt_enabled = MagicMock(return_value=False)
    agent_team.bridge_enabled = MagicMock(return_value=False)
    agent_team.external_cli_kinds = MagicMock(return_value=frozenset())
    agent_team.is_leader = True

    tools = create_team_tools(
        role="leader",
        agent_team=agent_team,
        lifecycle="persistent",
    )
    names = {tool.card.name for tool in tools}
    assert "clean_team" not in names
