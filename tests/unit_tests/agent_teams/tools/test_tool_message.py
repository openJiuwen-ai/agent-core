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
from openjiuwen.agent_teams.schema.status import MemberMode, MemberStatus
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
async def test_leader_cannot_send_message_to_user(db):
    """Leader output is already visible to the user, so no user mailbox row is created."""
    send = _leader_send(db, lang="en")

    result = await send.invoke({"to": "user", "content": "hello user"})

    assert not result.success
    assert "Reply to the user directly" in result.error
    assert await db.message.get_team_messages(team_name=TEAM_NAME) == []


@pytest.mark.asyncio
@pytest.mark.level0
async def test_member_can_send_message_to_user(db):
    """Members still need the user pseudo-recipient to answer human-originated messages."""
    send = _member_send(db)

    result = await send.invoke({"to": "user", "content": "hello user"})

    assert result.success
    messages = await db.message.get_team_messages(team_name=TEAM_NAME)
    assert len(messages) == 1
    assert messages[0].to_member_name == "user"


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


DEV_3 = "dev-3"
DEV_4 = "dev-4"


async def _spawn_unstarted(db, name: str) -> None:
    await db.member.create_member(
        member_name=name,
        team_name=TEAM_NAME,
        display_name=name,
        agent_card=AgentCard().model_dump_json(),
        status=MemberStatus.UNSTARTED.value,
        mode=MemberMode.BUILD_MODE.value,
    )


def _leader_send_with_spawn(db, on_started):
    backend = TeamBackend(
        team_name=TEAM_NAME,
        member_name=LEADER_NAME,
        is_leader=True,
        db=db,
        messager=AsyncMock(spec=Messager),
        on_member_started=on_started,
    )
    return _send_tool(
        create_team_tools(role="leader", agent_team=backend, on_teammate_created=on_started)
    )


@pytest.mark.asyncio
@pytest.mark.level0
async def test_unicast_starts_only_the_recipient(db):
    """A direct message launches the addressed member and nobody else —
    the whole roster must not come up for one targeted note."""
    await _spawn_unstarted(db, DEV_3)
    await _spawn_unstarted(db, DEV_4)
    on_started = AsyncMock()
    send = _leader_send_with_spawn(db, on_started)

    result = await send.invoke({"to": DEV_3, "content": "hello"})

    assert result.success
    on_started.assert_awaited_once_with(DEV_3)
    m3 = await db.member.get_member(DEV_3, TEAM_NAME)
    m4 = await db.member.get_member(DEV_4, TEAM_NAME)
    assert m3.status == MemberStatus.STARTING.value
    assert m4.status == MemberStatus.UNSTARTED.value


@pytest.mark.asyncio
@pytest.mark.level0
async def test_multicast_starts_only_valid_recipients(db):
    """Multicast starts the validated recipients only; a typo'd name is
    reported failed and never launches a process."""
    await _spawn_unstarted(db, DEV_3)
    await _spawn_unstarted(db, DEV_4)
    on_started = AsyncMock()
    send = _leader_send_with_spawn(db, on_started)

    result = await send.invoke({"to": [DEV_3, "ghost"], "content": "hello"})

    assert not result.success  # strict multicast: partial failure is a failure
    on_started.assert_awaited_once_with(DEV_3)
    m4 = await db.member.get_member(DEV_4, TEAM_NAME)
    assert m4.status == MemberStatus.UNSTARTED.value


@pytest.mark.asyncio
@pytest.mark.level0
async def test_broadcast_still_starts_everyone(db):
    """Broadcast semantics address the whole roster, so the full-roster
    startup is retained there and only there."""
    await _spawn_unstarted(db, DEV_3)
    await _spawn_unstarted(db, DEV_4)
    on_started = AsyncMock()
    send = _leader_send_with_spawn(db, on_started)

    result = await send.invoke({"to": "*", "content": "global announcement"})

    assert result.success
    assert {call.args[0] for call in on_started.await_args_list} == {DEV_3, DEV_4}


@pytest.mark.asyncio
@pytest.mark.level0
async def test_member_send_never_starts_anyone(db):
    """The auto-start funnel is leader-only; a teammate's send is a no-op
    for spawning even when it has a callback wired."""
    await _spawn_unstarted(db, DEV_3)
    on_started = AsyncMock()
    backend = TeamBackend(
        team_name=TEAM_NAME,
        member_name=DEV_1,
        is_leader=False,
        db=db,
        messager=AsyncMock(spec=Messager),
        on_member_started=on_started,
    )
    send = _send_tool(
        create_team_tools(role="member", agent_team=backend, on_teammate_created=on_started)
    )

    result = await send.invoke({"to": DEV_3, "content": "hello"})

    assert result.success
    on_started.assert_not_awaited()
    m3 = await db.member.get_member(DEV_3, TEAM_NAME)
    assert m3.status == MemberStatus.UNSTARTED.value
