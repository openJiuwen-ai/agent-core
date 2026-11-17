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


@pytest.mark.asyncio
async def test_reading_newer_broadcast_preserves_old_deferred_for_same_recipient(db):
    send = _leader_send(db)
    old = (await send.invoke({"to": "*", "content": "Useful later"})).data["message_id"]
    new = (await send.invoke({"to": "*", "content": "Urgent correction"})).data["message_id"]
    await db.message.record_delivery(old, DEV_1, "deferred", mode="follow_up", target_tasks={"A": 1})
    await db.message.record_delivery(new, DEV_1, "submitted", mode="steer")
    await send.message_manager.mark_messages_read([await db.message.get_message(new)], DEV_1)
    unread = await db.message.get_broadcast_messages(TEAM_NAME, DEV_1, unread_only=True)
    assert [m.message_id for m in unread] == [old]
    assert DEV_1 in await db.message.members_with_unread(TEAM_NAME, [DEV_1])
    await send.message_manager.mark_messages_read([await db.message.get_message(old)], DEV_1)
    assert DEV_1 not in await db.message.members_with_unread(TEAM_NAME, [DEV_1])
    assert DEV_2 in await db.message.members_with_unread(TEAM_NAME, [DEV_2])


@pytest.mark.asyncio
async def test_private_delivery_status_respects_sender_and_leader_access(db):
    sender = _send_tool(create_team_tools(role="teammate", agent_team=_backend(db, DEV_1, False), lang="cn"))
    other = _send_tool(create_team_tools(role="teammate", agent_team=_backend(db, DEV_2, False), lang="en"))
    sent = await sender.invoke({"to": DEV_2, "content": "Later request", "mode": "follow_up"})
    message_id = sent.data["message_id"]
    for viewer in (sender, _leader_send(db)):
        status = await viewer.invoke({"action": "status", "message_id": message_id})
        assert status.success
        assert status.data["recipients"][DEV_2]["state"] == "pending"
    assert not (await other.invoke({"action": "status", "message_id": message_id})).success


@pytest.mark.asyncio
async def test_abort_and_resume_are_ordered_even_with_same_clock_tick(db, monkeypatch):
    monkeypatch.setattr("openjiuwen.agent_teams.tools.database.message_dao.get_current_time", lambda: 100)
    send = _leader_send(db)
    stop = (await send.invoke({"to": DEV_1, "content": "Stop", "mode": "abort"})).data["message_id"]
    resume = (await send.invoke({"to": DEV_1, "content": "New work", "mode": "steer"})).data["message_id"]
    assert (await db.message.get_message(resume)).timestamp > (await db.message.get_message(stop)).timestamp


@pytest.mark.asyncio
async def test_old_terminal_feedback_without_handled_flag_does_not_replay(db):
    send = _leader_send(db)
    message_id = (await send.invoke({"to": "*", "content": "Already delivered before upgrade"})).data["message_id"]
    await db.message.record_delivery(message_id, DEV_1, "submitted", mode="steer")
    assert not await db.message.get_broadcast_messages(TEAM_NAME, DEV_1, unread_only=True)
    assert not await db.message.claim_delivery(message_id, DEV_1)
    await db.message.record_delivery(message_id, DEV_1, "pending")
    feedback = (await send.invoke({"action": "status", "message_id": message_id})).data["recipients"]
    assert feedback[DEV_1]["state"] == "submitted"


@pytest.mark.asyncio
async def test_broadcast_feedback_is_persistent_per_recipient_and_query_is_read_only(db):
    import asyncio
    send = _leader_send(db)
    sent = await send.invoke({"to": "*", "content": "contract update"})
    message_id = sent.data["message_id"]
    before = await db.message.get_team_messages(TEAM_NAME)
    status = await send.invoke({"action": "status", "message_id": message_id})
    assert status.success
    assert set(status.data["recipients"]) == {DEV_1, DEV_2}
    assert all(v["state"] == "pending" for v in status.data["recipients"].values())
    await asyncio.gather(
        db.message.record_delivery(message_id, DEV_1, "deferred", mode="follow_up"),
        db.message.record_delivery(message_id, DEV_2, "dropped", mode="drop"),
    )
    status = await send.invoke({"action": "status", "message_id": message_id})
    assert status.data["recipients"][DEV_1]["state"] == "deferred"
    assert status.data["recipients"][DEV_2]["state"] == "dropped"
    await db.message.record_delivery(message_id, DEV_1, "submitted", mode="follow_up")
    await db.message.record_delivery(message_id, DEV_1, "deferred", mode="follow_up")
    status = await send.invoke({"action": "status", "message_id": message_id})
    assert status.data["recipients"][DEV_1]["state"] == "submitted"
    assert "NOT acknowledgment" in send.render_for_llm(status)
    assert len(await db.message.get_team_messages(TEAM_NAME)) == len(before)
    other = _send_tool(create_team_tools(role="teammate", agent_team=_backend(db, DEV_1, False), lang="en"))
    assert not (await other.invoke({"action": "status", "message_id": message_id})).success
    assert not (await send.invoke({"action": "status"})).success


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

    multicast = await send.invoke({"targets": [DEV_1], "content": OVERSIZE})
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
    assert "产物目录" in result.error
    assert str(MAX_CONTENT_CHARS) in result.error
    assert str(len(OVERSIZE)) in result.error
    # render_for_llm is what actually reaches the model.
    assert "write_file" in send.render_for_llm(result)


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
async def test_direct_message_recovers_error_recipient_before_delivery(db):
    """A targeted message is an explicit nudge that may recover its ERROR recipient."""
    failed_member = "failed-dev"
    await db.member.create_member(
        member_name=failed_member,
        team_name=TEAM_NAME,
        display_name=failed_member,
        agent_card=AgentCard().model_dump_json(),
        status=MemberStatus.ERROR.value,
        mode=MemberMode.BUILD_MODE.value,
    )
    on_restarted = AsyncMock(return_value=True)
    backend = TeamBackend(
        team_name=TEAM_NAME,
        member_name=LEADER_NAME,
        is_leader=True,
        db=db,
        messager=AsyncMock(spec=Messager),
        on_member_restarted=on_restarted,
    )
    send = _send_tool(create_team_tools(role="leader", agent_team=backend, lang="cn"))

    result = await send.invoke({"to": failed_member, "content": "retry this task"})

    assert result.success
    on_restarted.assert_awaited_once_with(failed_member)
    member = await db.member.get_member(failed_member, TEAM_NAME)
    assert member.status == MemberStatus.RESTARTING.value


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


# ---------------------------------------------------------------------------
# F_118: addressing a dormant member wakes it; a broadcast does not
# ---------------------------------------------------------------------------


async def _dormant(db, name: str) -> None:
    await db.member.create_member(
        member_name=name,
        team_name=TEAM_NAME,
        display_name=name,
        agent_card=AgentCard().model_dump_json(),
        status=MemberStatus.DORMANT.value,
        mode=MemberMode.BUILD_MODE.value,
    )


def _leader_backend(db, on_restarted: AsyncMock) -> TeamBackend:
    return TeamBackend(
        team_name=TEAM_NAME,
        member_name=LEADER_NAME,
        is_leader=True,
        db=db,
        messager=AsyncMock(spec=Messager),
        on_member_restarted=on_restarted,
    )


@pytest.mark.asyncio
@pytest.mark.level0
async def test_direct_message_wakes_dormant_recipient(db):
    """A unicast is an explicit address: the dormant recipient is claimed for restart first."""
    await _dormant(db, "sleepy")
    on_restarted = AsyncMock(return_value=True)
    send = _send_tool(create_team_tools(role="leader", agent_team=_leader_backend(db, on_restarted), lang="cn"))

    result = await send.invoke({"to": "sleepy", "content": "one more thing"})

    assert result.success
    on_restarted.assert_awaited_once_with("sleepy")
    assert (await db.member.get_member("sleepy", TEAM_NAME)).status == MemberStatus.RESTARTING.value


@pytest.mark.asyncio
@pytest.mark.level0
async def test_multicast_wakes_named_dormant_recipients(db):
    await _dormant(db, "sleepy")
    on_restarted = AsyncMock(return_value=True)
    send = _send_tool(create_team_tools(role="leader", agent_team=_leader_backend(db, on_restarted), lang="cn"))

    result = await send.invoke({"targets": ["sleepy", DEV_1], "content": "sync"})

    assert result.success, result.error
    on_restarted.assert_awaited_once_with("sleepy")


@pytest.mark.asyncio
@pytest.mark.level0
async def test_broadcast_leaves_dormant_members_asleep(db):
    """Fan-out never wakes: the broadcast lands in the mailbox, read on the next explicit address."""
    await _dormant(db, "sleepy")
    on_restarted = AsyncMock(return_value=True)
    send = _send_tool(create_team_tools(role="leader", agent_team=_leader_backend(db, on_restarted), lang="cn"))

    result = await send.invoke({"to": "*", "content": "all hands"})

    assert result.success, result.error
    on_restarted.assert_not_awaited()
    assert (await db.member.get_member("sleepy", TEAM_NAME)).status == MemberStatus.DORMANT.value


async def _idle_task_scoped_member(db: TeamDatabase, member_name: str) -> TeamBackend:
    """A leader backend over the team plus an UNSTARTED task-scoped member with no open task."""
    on_created = AsyncMock()
    backend = TeamBackend(
        team_name=TEAM_NAME,
        member_name=LEADER_NAME,
        is_leader=True,
        db=db,
        messager=AsyncMock(spec=Messager),
        on_member_started=on_created,
    )
    result = await backend.spawn_member(
        member_name=member_name,
        display_name=member_name,
        agent_card=AgentCard(),
        lifetime="shutdown",
    )
    assert result.ok
    return backend


@pytest.mark.asyncio
@pytest.mark.level0
async def test_direct_message_starts_idle_task_scoped_recipient(db):
    """The roster-wide start skips an idle task-scoped member; naming it in a message still starts it."""
    backend = await _idle_task_scoped_member(db, "tmp-1")
    send = _send_tool(create_team_tools(role="leader", agent_team=backend, lang="cn"))

    result = await send.invoke({"to": "tmp-1", "content": "please check"})

    assert result.success
    started = [call.args[0] for call in backend._on_member_started.await_args_list]
    assert "tmp-1" in started


@pytest.mark.asyncio
@pytest.mark.level0
async def test_broadcast_does_not_start_idle_task_scoped_member(db):
    """The tool does not wake an unstarted recipient before its own decision."""
    backend = await _idle_task_scoped_member(db, "tmp-1")
    send = _send_tool(create_team_tools(role="leader", agent_team=backend, lang="cn"))

    result = await send.invoke({"to": "*", "content": "status, everyone"})

    assert result.success
    started = [call.args[0] for call in backend._on_member_started.await_args_list]
    assert "tmp-1" not in started
    rows = await db.message.get_team_messages(team_name=TEAM_NAME)
    assert any(row.broadcast and '"receiver_routed": true' in row.meta for row in rows)


@pytest.mark.asyncio
@pytest.mark.parametrize("lang", ["cn", "en"])
async def test_group_reply_cannot_disappear_in_private_user_mailbox(db, lang):
    backend = _backend(db, DEV_1, False)
    backend.conversation_mode = "group_chat"
    send = _send_tool(create_team_tools(role="teammate", agent_team=backend, lang=lang))
    result = await send.invoke({"to": "user", "content": "Received"})
    assert not result.success
    assert "group_send_message" in result.error
    assert await db.message.get_team_messages(team_name=TEAM_NAME) == []
    coordinated = await send.invoke({"to": DEV_2, "content": "Private coordination"})
    assert coordinated.success

@pytest.mark.asyncio
async def test_private_modes_enforce_abort_role_and_single_recipient(db):
    member = _member_send(db)
    assert set(member.card.input_params["properties"]["mode"]["enum"]) == {"steer", "follow_up"}
    rejected = await member.invoke({"to": DEV_2, "content": "Stop", "mode": "abort"})
    assert not rejected.success and "leader" in rejected.error

    leader = _leader_send(db)
    assert "abort" in leader.card.input_params["properties"]["mode"]["enum"]
    for recipients in ({"to": "*"}, {"targets": [DEV_1, DEV_2]}, {"to": "user"}):
        result = await leader.invoke({**recipients, "content": "Stop", "mode": "abort"})
        assert not result.success

    scheduled = _member_send(db, dispatch_mode="scheduled")
    scheduled.message_manager.send_message = AsyncMock(return_value="message-id")
    result = await scheduled.invoke({"to": "leader", "content": "Please stop task A", "mode": "steer"})
    assert result.success
    scheduled.message_manager.send_message.assert_awaited_once_with(
        content="Please stop task A",
        to_member_name=LEADER_NAME,
        meta={"private_delivery_mode": "steer", "target_tasks": {}},
    )

@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["follow_up", "abort"])
async def test_only_follow_up_binds_active_tasks(db, mode):
    from types import SimpleNamespace

    leader = _leader_send(db)
    leader._team.get_member_status = AsyncMock(return_value=MemberStatus.BUSY.value)
    leader._team.task_manager.get_tasks_by_assignee = AsyncMock(return_value=[
        SimpleNamespace(task_id="task-A", updated_at=123, status="in_progress"),
        SimpleNamespace(task_id="task-B", updated_at=456, status="completed"),
    ])
    leader.message_manager.send_message = AsyncMock(return_value="message-id")

    result = await leader.invoke({"to": DEV_1, "content": "Control current work", "mode": mode})

    assert result.success
    leader.message_manager.send_message.assert_awaited_once_with(
        content="Control current work",
        to_member_name=DEV_1,
        meta={"private_delivery_mode": mode, "target_tasks": {"task-A": 123} if mode == "follow_up" else {}},
    )
    if mode == "abort":
        leader._team.task_manager.get_tasks_by_assignee.assert_not_awaited()
