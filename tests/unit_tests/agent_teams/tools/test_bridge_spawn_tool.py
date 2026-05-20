# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unit tests for ``SpawnMemberTool`` bridge_agent branch."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
import pytest_asyncio

from openjiuwen.agent_teams.context import (
    reset_session_id,
    set_session_id,
)
from openjiuwen.agent_teams.messager import Messager
from openjiuwen.agent_teams.schema.team import BridgeMailboxInjectMode
from openjiuwen.agent_teams.tools.database import (
    DatabaseConfig,
    DatabaseType,
    TeamDatabase,
)
from openjiuwen.agent_teams.tools.locales import make_translator
from openjiuwen.agent_teams.tools.team import TeamBackend
from openjiuwen.agent_teams.tools.team_tools import SpawnMemberTool


@pytest.fixture
def db_config() -> DatabaseConfig:
    return DatabaseConfig(db_type=DatabaseType.SQLITE, connection_string=":memory:")


@pytest_asyncio.fixture
async def db(db_config):
    token = set_session_id("bridge_spawn_session")
    database = TeamDatabase(db_config)
    try:
        await database.initialize()
        yield database
    finally:
        await database.close()
        reset_session_id(token)


@pytest_asyncio.fixture
async def messager():
    yield AsyncMock(spec=Messager)


async def _build_backend(db, messager, *, enable_bridge: bool) -> TeamBackend:
    backend = TeamBackend(
        team_name="bt",
        member_name="team_leader",
        is_leader=True,
        db=db,
        messager=messager,
        enable_bridge=enable_bridge,
    )
    await backend.build_team(
        display_name="bt",
        desc="goal",
        leader_display_name="L",
        leader_desc="leader persona",
    )
    return backend


def _make_tool(backend) -> SpawnMemberTool:
    return SpawnMemberTool(team=backend, t=make_translator("cn"))


@pytest.mark.asyncio
@pytest.mark.level0
async def test_bridge_spawn_rejected_when_disabled(db, messager):
    backend = await _build_backend(db, messager, enable_bridge=False)
    tool = _make_tool(backend)
    out = await tool.invoke(
        {
            "member_name": "codex",
            "display_name": "Codex",
            "desc": "reviewer",
            "role_type": "bridge_agent",
        },
    )
    assert out.success is False
    assert "Bridge capability is disabled" in (out.error or "")


@pytest.mark.asyncio
@pytest.mark.level0
async def test_bridge_spawn_requires_desc(db, messager):
    backend = await _build_backend(db, messager, enable_bridge=True)
    tool = _make_tool(backend)
    out = await tool.invoke(
        {
            "member_name": "codex",
            "display_name": "Codex",
            "desc": "",
            "role_type": "bridge_agent",
        },
    )
    assert out.success is False
    assert "persona" in (out.error or "").lower() or "desc" in (out.error or "").lower()


@pytest.mark.asyncio
@pytest.mark.level0
async def test_bridge_spawn_happy_path(db, messager):
    backend = await _build_backend(db, messager, enable_bridge=True)
    tool = _make_tool(backend)
    out = await tool.invoke(
        {
            "member_name": "codex",
            "display_name": "Codex",
            "desc": "senior python reviewer",
            "role_type": "bridge_agent",
            "mailbox_inject_mode": "rephrase",
            "protocol": "codex",
            "adapter_config": {"endpoint": "stdio://codex"},
            "model_name": "gpt-4",
        },
    )
    assert out.success, out.error
    assert out.data["role_type"] == "bridge_agent"
    assert out.data["mailbox_inject_mode"] == "rephrase"
    assert out.data["protocol"] == "codex"
    # Backend state must agree with the tool's view.
    assert backend.is_bridge_agent("codex") is True
    spec = backend.get_bridge_member_spec("codex")
    assert spec is not None
    assert spec.mailbox_inject_mode == BridgeMailboxInjectMode.REPHRASE
    assert spec.protocol == "codex"
    assert spec.adapter_config == {"endpoint": "stdio://codex"}


@pytest.mark.asyncio
@pytest.mark.level0
async def test_bridge_spawn_rejects_bad_inject_mode(db, messager):
    backend = await _build_backend(db, messager, enable_bridge=True)
    tool = _make_tool(backend)
    out = await tool.invoke(
        {
            "member_name": "codex",
            "display_name": "Codex",
            "desc": "x",
            "role_type": "bridge_agent",
            "mailbox_inject_mode": "summarize",  # not a valid enum
        },
    )
    assert out.success is False
    assert "mailbox_inject_mode" in (out.error or "")


@pytest.mark.asyncio
@pytest.mark.level0
async def test_bridge_spawn_rejects_non_dict_adapter_config(db, messager):
    backend = await _build_backend(db, messager, enable_bridge=True)
    tool = _make_tool(backend)
    out = await tool.invoke(
        {
            "member_name": "codex",
            "display_name": "Codex",
            "desc": "x",
            "role_type": "bridge_agent",
            "adapter_config": "not-a-dict",
        },
    )
    assert out.success is False
    assert "adapter_config" in (out.error or "")


@pytest.mark.asyncio
@pytest.mark.level0
async def test_invalid_role_type_lists_three_choices(db, messager):
    backend = await _build_backend(db, messager, enable_bridge=True)
    tool = _make_tool(backend)
    out = await tool.invoke(
        {
            "member_name": "x",
            "display_name": "X",
            "desc": "y",
            "role_type": "alien",
        },
    )
    assert out.success is False
    err = out.error or ""
    assert "teammate" in err
    assert "human_agent" in err
    assert "bridge_agent" in err
