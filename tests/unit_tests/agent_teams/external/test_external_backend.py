# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Tests for TeamBackend.spawn_external_cli_agent registration."""

from unittest.mock import AsyncMock

import pytest
import pytest_asyncio

from openjiuwen.agent_teams.context import reset_session_id, set_session_id
from openjiuwen.agent_teams.messager import Messager
from openjiuwen.agent_teams.schema.team import ExternalCliAgentSpec
from openjiuwen.agent_teams.tools.database import DatabaseConfig, DatabaseType, TeamDatabase
from openjiuwen.agent_teams.tools.team import TeamBackend

_TEAM = "ext_cli_team"


@pytest_asyncio.fixture
async def make_backend():
    """Yield a factory building a backend with a chosen external-CLI roster.

    All backends share one in-memory db so a test can build several with
    different declared ``external_cli_agents`` (the capability ceiling).
    """
    token = set_session_id("sess")
    db = TeamDatabase(DatabaseConfig(db_type=DatabaseType.SQLITE, connection_string=":memory:"))
    await db.initialize()
    await db.team.create_team(team_name=_TEAM, display_name="Ext CLI", leader_member_name="leader")
    messager = AsyncMock(spec=Messager)

    def _make(declared: list[str] | None = None) -> TeamBackend:
        configs = [ExternalCliAgentSpec(cli_agent=name) for name in (declared or [])]
        return TeamBackend(
            team_name=_TEAM,
            member_name="leader",
            db=db,
            messager=messager,
            is_leader=True,
            external_cli_agents=configs,
        )

    yield _make
    reset_session_id(token)
    await db.close()


@pytest.mark.asyncio
@pytest.mark.level0
async def test_spawn_external_cli_agent_registers_member(make_backend):
    backend = make_backend(["claude", "codex"])
    result = await backend.spawn_external_cli_agent(
        member_name="cli-1",
        display_name="CLI One",
        cli_agent="claude",
        persona="senior reviewer",
    )
    assert result.ok, result.reason
    assert backend.is_external_cli_agent("cli-1")
    assert backend.get_external_cli_agent("cli-1") == "claude"
    assert "cli-1" in backend.external_cli_agent_names()

    member = await backend.get_member("cli-1")
    assert member is not None
    assert member.role == "teammate"


@pytest.mark.asyncio
@pytest.mark.level1
async def test_spawn_external_cli_agent_undeclared_fails(make_backend):
    # 'claude' is not in the declared external_cli_agents (capability ceiling).
    backend = make_backend(["codex"])
    result = await backend.spawn_external_cli_agent(
        member_name="cli-x",
        display_name="CLI X",
        cli_agent="claude",
        persona="x",
    )
    assert not result.ok
    assert "not declared" in (result.reason or "")
    assert not backend.is_external_cli_agent("cli-x")


@pytest.mark.asyncio
@pytest.mark.level1
async def test_spawn_external_cli_agent_unknown_adapter_fails(make_backend):
    # Declared so it clears the ceiling, but it maps to no built-in adapter.
    backend = make_backend(["not-a-real-cli"])
    result = await backend.spawn_external_cli_agent(
        member_name="cli-2",
        display_name="CLI Two",
        cli_agent="not-a-real-cli",
        persona="x",
    )
    assert not result.ok
    assert not backend.is_external_cli_agent("cli-2")


@pytest.mark.asyncio
@pytest.mark.level1
async def test_spawn_external_cli_agent_requires_persona(make_backend):
    backend = make_backend(["claude"])
    result = await backend.spawn_external_cli_agent(
        member_name="cli-3",
        display_name="CLI Three",
        cli_agent="claude",
        persona="",
    )
    assert not result.ok
    assert not backend.is_external_cli_agent("cli-3")


@pytest.mark.asyncio
@pytest.mark.level0
async def test_non_external_member_returns_none(make_backend):
    backend = make_backend(["claude"])
    assert backend.get_external_cli_agent("nobody") is None
    assert not backend.is_external_cli_agent("nobody")
