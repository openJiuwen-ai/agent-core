# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Shared fixtures for external-access tests.

Builds a process-global in-memory team (leader + one teammate) that an
:class:`ExternalTeamClient` can attach to via the in-memory database and the
in-process messager — no sqlite file or zmq socket required.
"""

from typing import Callable

import pytest
import pytest_asyncio

from openjiuwen.agent_teams.external import TeamJoinDescriptor
from openjiuwen.agent_teams.messager.base import MessagerTransportConfig
from openjiuwen.agent_teams.schema.status import MemberStatus
from openjiuwen.agent_teams.spawn.shared_resources import (
    cleanup_shared_resources,
    get_shared_db,
)
from openjiuwen.agent_teams.tools.memory_database import MemoryDatabaseConfig

TEAM = "ext_team"
SESSION = "ext_session"


@pytest_asyncio.fixture
async def team_db():
    """Provide a process-global in-memory team with a leader and a teammate."""
    cleanup_shared_resources()
    db = get_shared_db(MemoryDatabaseConfig())
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
    cleanup_shared_resources()


@pytest.fixture
def make_descriptor() -> Callable[..., TeamJoinDescriptor]:
    """Return a factory for in-memory / in-process join descriptors."""

    def _factory(member: str = "dev-1", role: str = "teammate") -> TeamJoinDescriptor:
        return TeamJoinDescriptor(
            session_id=SESSION,
            team_name=TEAM,
            member_name=member,
            role=role,
            db_config=MemoryDatabaseConfig(),
            transport_config=MessagerTransportConfig(backend="inprocess", team_name=TEAM),
        )

    return _factory
