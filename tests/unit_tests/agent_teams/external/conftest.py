# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Shared fixtures for external-access tests.

Builds a process-global sqlite ``:memory:`` team (leader + one teammate)
that an :class:`ExternalTeamClient` can attach to via the shared database
and the in-process messager — no sqlite file or zmq socket required.
"""

from typing import Callable

import pytest
import pytest_asyncio

from openjiuwen.agent_teams.context import reset_session_id, set_session_id
from openjiuwen.agent_teams.external import TeamJoinDescriptor
from openjiuwen.agent_teams.messager.base import MessagerTransportConfig
from openjiuwen.agent_teams.schema.status import MemberStatus
from openjiuwen.agent_teams.spawn.shared_resources import (
    cleanup_shared_resources,
    get_shared_db,
)
from openjiuwen.agent_teams.team_workspace.models import TeamWorkspaceConfig
from openjiuwen.agent_teams.tools.database import DatabaseConfig, DatabaseType

TEAM = "ext_team"
SESSION = "ext_session"


def _memory_db_config() -> DatabaseConfig:
    """Return a sqlite ``:memory:`` config shared across the test session."""
    return DatabaseConfig(db_type=DatabaseType.SQLITE, connection_string=":memory:")


@pytest_asyncio.fixture
async def team_db():
    """Provide a process-global sqlite team with a leader and a teammate.

    Binds ``SESSION`` before ``initialize()`` so the per-session dynamic
    tables (tasks / messages) are created up front — the ExternalTeamClient
    re-uses this same shared instance and its ``initialize()`` is a no-op.
    """
    cleanup_shared_resources()
    token = set_session_id(SESSION)
    db = get_shared_db(_memory_db_config())
    await db.initialize()
    await db.team.create_team(team_name=TEAM, display_name="Ext Team", leader_member_name="leader")
    await db.member.create_member(
        member_name="leader",
        team_name=TEAM,
        display_name="Leader",
        agent_card="{}",
        status=MemberStatus.READY.value,
        role="leader",
    )
    await db.member.create_member(
        member_name="dev-1",
        team_name=TEAM,
        display_name="Dev One",
        agent_card="{}",
        status=MemberStatus.READY.value,
        role="teammate",
    )
    yield db
    reset_session_id(token)
    cleanup_shared_resources()


@pytest.fixture
def make_descriptor() -> Callable[..., TeamJoinDescriptor]:
    """Return a factory for in-memory / in-process join descriptors."""

    def _factory(
        member: str = "dev-1",
        role: str = "teammate",
        scope: str = "operator",
        teammate_mode: str = "build_mode",
        workspace_config: TeamWorkspaceConfig | None = None,
        workspace_path: str | None = None,
    ) -> TeamJoinDescriptor:
        return TeamJoinDescriptor(
            session_id=SESSION,
            team_name=TEAM,
            member_name=member,
            role=role,
            scope=scope,
            teammate_mode=teammate_mode,
            db_config=_memory_db_config(),
            transport_config=MessagerTransportConfig(backend="inprocess", team_name=TEAM),
            workspace_config=workspace_config,
            workspace_path=workspace_path,
        )

    return _factory
