# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Verify HumanAgentInbox dumb-routes typed payloads.

Top-level ``parse_interact_str`` already strips channel and mention
prefixes, so the inbox itself never inspects the body. Routing
decisions follow the explicit ``to`` argument:

- ``to is None`` → drive the avatar's DeepAgent.
- ``to`` ∈ ``BROADCAST_TARGETS`` → broadcast as ``sender``.
- ``to=<member>`` → validated direct message from ``sender``.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
import pytest_asyncio

from openjiuwen.agent_teams.context import (
    reset_session_id,
    set_session_id,
)
from openjiuwen.agent_teams.interaction.human_agent_inbox import (
    HumanAgentInbox,
    HumanAgentNotEnabledError,
    UnknownHumanAgentError,
)
from openjiuwen.agent_teams.messager import Messager
from openjiuwen.agent_teams.schema.status import MemberMode
from openjiuwen.agent_teams.schema.team import (
    TeamMemberSpec,
    TeamRole,
)
from openjiuwen.agent_teams.tools.database import (
    DatabaseConfig,
    DatabaseType,
    TeamDatabase,
)
from openjiuwen.agent_teams.tools.team import TeamBackend
from openjiuwen.core.single_agent import AgentCard

HUMAN = "human_alice"
TEAMMATE = "dev_bob"


@pytest_asyncio.fixture
async def db():
    token = set_session_id("inbox_session")
    config = DatabaseConfig(db_type=DatabaseType.SQLITE, connection_string=":memory:")
    database = TeamDatabase(config)
    try:
        await database.initialize()
        await database.team.create_team(
            team_name="hitt_team",
            display_name="HITT",
            leader_member_name="team_leader",
        )
        for name, role in ((HUMAN, TeamRole.HUMAN_AGENT.value), (TEAMMATE, TeamRole.TEAMMATE.value)):
            await database.member.create_member(
                member_name=name,
                team_name="hitt_team",
                display_name=name,
                agent_card=AgentCard().model_dump_json(),
                status="READY",
                role=role,
                mode=MemberMode.BUILD_MODE.value,
            )
        yield database
    finally:
        await database.close()
        reset_session_id(token)


@pytest_asyncio.fixture
async def messager():
    yield AsyncMock(spec=Messager)


@pytest_asyncio.fixture
async def team_backend(db, messager):
    backend = TeamBackend(
        team_name="hitt_team",
        member_name="team_leader",
        is_leader=True,
        db=db,
        messager=messager,
        predefined_members=[
            TeamMemberSpec(
                member_name=HUMAN,
                display_name="Alice",
                role_type=TeamRole.HUMAN_AGENT,
                persona="user avatar",
            ),
        ],
        enable_hitt=True,
    )
    # ``predefined_members`` is consumed by ``build_team``; this fixture
    # short-circuits that path by writing rows directly in the ``db``
    # fixture, so human_agent_names/is_human_agent queries DB directly
    # and find the rows without any cache-refresh step.
    yield backend


@pytest.mark.asyncio
@pytest.mark.level0
async def test_send_to_none_drives_avatar(team_backend):
    """``to=None`` feeds the body verbatim to the avatar's DeepAgent."""
    avatar = AsyncMock()
    inbox = HumanAgentInbox(
        team_backend,
        team_backend.message_manager,
        agent_lookup=AsyncMock(return_value=avatar),
    )

    result = await inbox.send("read design.md and summarise it")

    assert result.ok
    assert result.message_id is None
    avatar.deliver_input.assert_awaited_once_with("read design.md and summarise it")


@pytest.mark.asyncio
@pytest.mark.level0
async def test_send_inbox_does_not_parse_at_in_body(team_backend):
    """Body starting with ``@`` is delivered literally — parsing lives upstairs."""
    avatar = AsyncMock()
    inbox = HumanAgentInbox(
        team_backend,
        team_backend.message_manager,
        agent_lookup=AsyncMock(return_value=avatar),
    )

    result = await inbox.send(f"@{TEAMMATE} ping me when done")

    assert result.ok
    assert result.message_id is None
    avatar.deliver_input.assert_awaited_once_with(f"@{TEAMMATE} ping me when done")


@pytest.mark.asyncio
@pytest.mark.level0
async def test_send_to_member_routes_direct(team_backend, db):
    """``to=<member>`` posts a validated point-to-point bus message."""
    inbox = HumanAgentInbox(team_backend, team_backend.message_manager)

    result = await inbox.send("ping me when done", to=TEAMMATE)

    assert result.ok
    assert result.message_id is not None
    messages = await team_backend.message_manager.get_messages(to_member_name=TEAMMATE)
    assert any(m.from_member_name == HUMAN and "ping me when done" in m.content for m in messages)


@pytest.mark.asyncio
@pytest.mark.level0
async def test_send_to_all_broadcasts_as_human_agent(team_backend, db):
    """``to="all"`` and ``to="*"`` both broadcast as the human member."""
    inbox = HumanAgentInbox(team_backend, team_backend.message_manager)

    first = await inbox.send("status sync", to="all")
    assert first.ok

    star = await inbox.send("heads up", to="*")
    assert star.ok

    casts = await team_backend.message_manager.get_team_messages("hitt_team")
    bodies = {m.content for m in casts if m.broadcast and m.from_member_name == HUMAN}
    assert "status sync" in bodies
    assert "heads up" in bodies


@pytest.mark.asyncio
@pytest.mark.level0
async def test_send_to_unknown_member_returns_unknown_member(team_backend):
    """Unknown explicit target surfaces a stable failure code."""
    inbox = HumanAgentInbox(team_backend, team_backend.message_manager)

    result = await inbox.send("hi", to="ghost")

    assert not result.ok
    assert result.reason == "unknown_member:ghost"


@pytest.mark.asyncio
@pytest.mark.level0
async def test_send_no_agent_lookup_returns_agent_unavailable(team_backend):
    """No mention + no agent_lookup → the avatar is reported unavailable."""
    inbox = HumanAgentInbox(
        team_backend,
        team_backend.message_manager,
    )

    result = await inbox.send("do the thing")
    assert not result.ok
    assert result.reason == "agent_unavailable"


@pytest.mark.asyncio
@pytest.mark.level0
async def test_send_unknown_sender_raises(team_backend):
    """An unregistered ``sender`` must raise instead of silently routing."""
    avatar = AsyncMock()
    inbox = HumanAgentInbox(
        team_backend,
        team_backend.message_manager,
        agent_lookup=AsyncMock(return_value=avatar),
    )

    with pytest.raises(UnknownHumanAgentError):
        await inbox.send("hi", sender="nope")


@pytest.mark.asyncio
@pytest.mark.level0
async def test_send_no_human_agent_registered_raises(db, messager):
    """An empty human-agent roster makes any send call illegal."""
    await db.team.create_team(
        team_name="empty_hitt_team",
        display_name="Empty HITT",
        leader_member_name="team_leader",
    )
    backend = TeamBackend(
        team_name="empty_hitt_team",
        member_name="team_leader",
        is_leader=True,
        db=db,
        messager=messager,
    )
    inbox = HumanAgentInbox(backend, backend.message_manager)

    with pytest.raises(HumanAgentNotEnabledError):
        await inbox.send("hello")


@pytest.mark.asyncio
@pytest.mark.level0
async def test_register_human_agent_inbound_unknown_member_raises(team_backend):
    """Registering against an unknown member must fail loudly."""
    with pytest.raises(KeyError):
        await team_backend.register_human_agent_inbound("ghost", lambda evt: None)


@pytest.mark.asyncio
@pytest.mark.level0
async def test_register_human_agent_inbound_clear(team_backend):
    """Passing ``None`` clears a prior registration."""

    def cb(evt):
        return None

    await team_backend.register_human_agent_inbound(HUMAN, cb)
    assert team_backend.get_human_agent_inbound(HUMAN) is cb

    await team_backend.register_human_agent_inbound(HUMAN, None)
    assert team_backend.get_human_agent_inbound(HUMAN) is None
