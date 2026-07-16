# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unit tests for the ``send_message`` content-size guard.

Past ``MAX_CONTENT_CHARS`` a body is an artifact, not a message, and belongs
in a file with the message carrying only its path. The guard sits in
``invoke`` ahead of ``_dispatch``, so these tests pin that it holds on every
route (unicast / multicast / broadcast), in both variants, for every
recipient including the user, and that a rejected call delivers nothing.
"""

from unittest.mock import AsyncMock

import pytest
import pytest_asyncio

from openjiuwen.agent_teams.context import reset_session_id, set_session_id
from openjiuwen.agent_teams.messager import Messager
from openjiuwen.agent_teams.schema.status import MemberMode
from openjiuwen.agent_teams.tools.database import (
    DatabaseConfig,
    DatabaseType,
    TeamDatabase,
)
from openjiuwen.agent_teams.tools.team import TeamBackend
from openjiuwen.agent_teams.tools.tool_factory import create_team_tools
from openjiuwen.agent_teams.tools.tool_message import MAX_CONTENT_CHARS
from openjiuwen.core.single_agent import AgentCard

TEAM_NAME = "message_team"
LEADER_NAME = "team_leader"
DEV_1 = "dev-1"
DEV_2 = "dev-2"

OVERSIZE = "报" * (MAX_CONTENT_CHARS + 1)


@pytest_asyncio.fixture
async def db():
    """In-memory team DB with a leader and two teammates."""
    token = set_session_id("message_session")
    config = DatabaseConfig(db_type=DatabaseType.SQLITE, connection_string=":memory:")
    database = TeamDatabase(config)
    try:
        await database.initialize()
        await database.team.create_team(
            team_name=TEAM_NAME,
            display_name="Message Team",
            leader_member_name=LEADER_NAME,
        )
        for name in (LEADER_NAME, DEV_1, DEV_2):
            await database.member.create_member(
                member_name=name,
                team_name=TEAM_NAME,
                display_name=name,
                agent_card=AgentCard().model_dump_json(),
                status="READY",
                mode=MemberMode.BUILD_MODE.value,
            )
        yield database
    finally:
        reset_session_id(token)
        await database.close()


def _backend(db, member_name: str, is_leader: bool) -> TeamBackend:
    return TeamBackend(
        team_name=TEAM_NAME,
        member_name=member_name,
        is_leader=is_leader,
        db=db,
        messager=AsyncMock(spec=Messager),
    )


def _send_tool(tools):
    return next(tool for tool in tools if tool.card.name == "send_message")


def _leader_send(db, lang: str = "cn"):
    return _send_tool(create_team_tools(role="leader", agent_team=_backend(db, LEADER_NAME, True), lang=lang))


def _member_send(db, dispatch_mode: str = "autonomous"):
    return _send_tool(
        create_team_tools(
            role="teammate",
            agent_team=_backend(db, DEV_1, False),
            dispatch_mode=dispatch_mode,
        )
    )


@pytest.mark.asyncio
@pytest.mark.level0
async def test_oversize_content_rejected_on_every_route(db):
    """The guard precedes _dispatch, so no route can smuggle an artifact through."""
    send = _leader_send(db)

    unicast = await send.invoke({"to": DEV_1, "content": OVERSIZE})
    assert not unicast.success

    multicast = await send.invoke({"to": [DEV_1], "content": OVERSIZE})
    assert not multicast.success

    broadcast = await send.invoke({"to": "*", "content": OVERSIZE})
    assert not broadcast.success


@pytest.mark.asyncio
@pytest.mark.level0
async def test_rejection_tells_the_caller_how_to_fix_it(db):
    """An error that only says "no" would leave the LLM guessing; it must teach the file handoff."""
    send = _leader_send(db)

    result = await send.invoke({"to": DEV_1, "content": OVERSIZE})

    assert not result.success
    assert "write_file" in result.error
    assert ".team/" in result.error
    assert str(MAX_CONTENT_CHARS) in result.error
    assert str(len(OVERSIZE)) in result.error
    # map_result is what actually reaches the model.
    assert "write_file" in send.map_result(result)


@pytest.mark.asyncio
@pytest.mark.level0
async def test_rejected_message_is_not_delivered(db):
    """A rejected call must leave no message row behind, on any route."""
    send = _leader_send(db)

    for to in (DEV_1, [DEV_1], "*"):
        result = await send.invoke({"to": to, "content": OVERSIZE})
        assert not result.success

    assert await db.message.get_team_messages(team_name=TEAM_NAME) == []


@pytest.mark.asyncio
@pytest.mark.level0
async def test_limit_boundary_is_inclusive(db):
    """At the limit the message goes; one character past it, it does not."""
    send = _leader_send(db)

    at_limit = await send.invoke({"to": DEV_1, "content": "x" * MAX_CONTENT_CHARS})
    assert at_limit.success

    over_limit = await send.invoke({"to": DEV_1, "content": "x" * (MAX_CONTENT_CHARS + 1)})
    assert not over_limit.success


@pytest.mark.asyncio
@pytest.mark.level0
async def test_no_recipient_is_exempt_including_user(db):
    """The rule is about the shape of the content, so the recipient never buys an exemption.

    The user reads a handed-off path through their own assistant agent, so
    they are not a special case either.
    """
    send = _member_send(db)

    result = await send.invoke({"to": "user", "content": OVERSIZE})

    assert not result.success
    assert "write_file" in result.error


@pytest.mark.asyncio
@pytest.mark.level0
async def test_guard_holds_in_the_scheduled_variant(db):
    """Both variants share the base, so the guard is not a leader-only rule."""
    send = _member_send(db, dispatch_mode="scheduled")

    for to in ("leader", "user"):
        result = await send.invoke({"to": to, "content": OVERSIZE})
        assert not result.success
        assert "write_file" in result.error


@pytest.mark.asyncio
@pytest.mark.level0
@pytest.mark.parametrize("lang", ["cn", "en"])
async def test_description_states_the_limit_in_both_languages(db, lang):
    """The bound is only useful up front — hitting the wall to learn it costs a round trip.

    Also catches drift: raise MAX_CONTENT_CHARS without touching the shared
    artifact_handoff_policy fragment and this fails.
    """
    send = _leader_send(db, lang=lang)

    assert str(MAX_CONTENT_CHARS) in send.card.description
