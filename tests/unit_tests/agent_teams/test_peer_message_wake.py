# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""The leader brings up the recipients of member-to-member messages (F_126).

Only the leader can spawn, so a member's ``send_message`` reaches a peer that
never ran — or that is DORMANT / ERROR — only through the leader, which sees
every team message event. These tests pin the wake rules against a real
in-memory team DB: direct and multicast recipients are started or recovered,
a broadcast starts UNSTARTED members only, and the leader's own mail, mail to
``user`` and mail to a running member trigger nothing.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio

from openjiuwen.agent_teams.agent.coordination.handlers.message import MessageHandler
from openjiuwen.agent_teams.context import reset_session_id, set_session_id
from openjiuwen.agent_teams.messager import Messager
from openjiuwen.agent_teams.schema.events import BroadcastEvent, EventMessage, MessageEvent
from openjiuwen.agent_teams.schema.status import MemberMode, MemberStatus
from openjiuwen.agent_teams.schema.team import TeamRole
from openjiuwen.agent_teams.tools.database import DatabaseConfig, DatabaseType, TeamDatabase
from openjiuwen.agent_teams.tools.team import TeamBackend
from openjiuwen.core.single_agent import AgentCard

TEAM_NAME = "wake_team"
LEADER = "team_leader"
SENDER = "dev-1"
UNSTARTED = "dev-unstarted"
DORMANT = "dev-dormant"
FAILED = "dev-error"
RUNNING = "dev-running"


@pytest_asyncio.fixture
async def team():
    """A leader backend over an in-memory DB holding one member per status."""
    token = set_session_id("wake_session")
    database = TeamDatabase(DatabaseConfig(db_type=DatabaseType.SQLITE, connection_string=":memory:"))
    try:
        await database.initialize()
        await database.team.create_team(team_name=TEAM_NAME, display_name="Wake Team", leader_member_name=LEADER)
        statuses = {
            LEADER: MemberStatus.READY,
            SENDER: MemberStatus.BUSY,
            UNSTARTED: MemberStatus.UNSTARTED,
            DORMANT: MemberStatus.DORMANT,
            FAILED: MemberStatus.ERROR,
            RUNNING: MemberStatus.READY,
        }
        for name, status in statuses.items():
            await database.member.create_member(
                member_name=name,
                team_name=TEAM_NAME,
                display_name=name,
                agent_card=AgentCard().model_dump_json(),
                status=status.value,
                mode=MemberMode.BUILD_MODE.value,
            )
        started = AsyncMock()
        restarted = AsyncMock(return_value=True)
        backend = TeamBackend(
            team_name=TEAM_NAME,
            member_name=LEADER,
            is_leader=True,
            db=database,
            messager=AsyncMock(spec=Messager),
            on_member_started=started,
            on_member_restarted=restarted,
        )
        handler = MessageHandler(
            SimpleNamespace(),
            SimpleNamespace(member_name=LEADER, role=TeamRole.LEADER),
            SimpleNamespace(team_backend=backend, message_manager=backend.message_manager),
            AsyncMock(),
        )
        yield SimpleNamespace(db=database, backend=backend, handler=handler, started=started, restarted=restarted)
    finally:
        reset_session_id(token)
        await database.close()


def _direct(to: str, *, sender: str = SENDER) -> EventMessage:
    return EventMessage.from_event(
        MessageEvent(team_name=TEAM_NAME, message_id=f"m-{to}", from_member_name=sender, to_member_name=to)
    )


def _broadcast(*, sender: str = SENDER) -> EventMessage:
    return EventMessage.from_event(BroadcastEvent(team_name=TEAM_NAME, message_id="b-1", from_member_name=sender))


async def _status(team, name: str) -> str:
    return await team.backend.get_member_status(name)


@pytest.mark.asyncio
@pytest.mark.level0
async def test_direct_message_starts_an_unstarted_recipient(team):
    await team.handler._wake_peer_recipients(_direct(UNSTARTED))

    team.started.assert_awaited_once_with(UNSTARTED)
    assert await _status(team, UNSTARTED) == MemberStatus.STARTING.value


@pytest.mark.asyncio
@pytest.mark.level0
@pytest.mark.parametrize("recipient", [DORMANT, FAILED])
async def test_direct_message_recovers_a_dormant_or_failed_recipient(team, recipient):
    await team.handler._wake_peer_recipients(_direct(recipient))

    team.restarted.assert_awaited_once_with(recipient)
    team.started.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.level0
async def test_running_recipient_is_left_alone(team):
    await team.handler._wake_peer_recipients(_direct(RUNNING))

    team.started.assert_not_awaited()
    team.restarted.assert_not_awaited()
    assert await _status(team, RUNNING) == MemberStatus.READY.value


@pytest.mark.asyncio
@pytest.mark.level0
async def test_broadcast_starts_unstarted_members_only(team):
    await team.handler._wake_peer_recipients(_broadcast())

    team.started.assert_awaited_once_with(UNSTARTED)
    team.restarted.assert_not_awaited()
    assert await _status(team, DORMANT) == MemberStatus.DORMANT.value
    assert await _status(team, FAILED) == MemberStatus.ERROR.value


@pytest.mark.asyncio
@pytest.mark.level0
@pytest.mark.parametrize(
    "event",
    [
        _direct(UNSTARTED, sender=LEADER),
        _broadcast(sender=LEADER),
        _direct("user"),
        _direct(LEADER),
    ],
    ids=["leader-direct", "leader-broadcast", "to-user", "to-leader"],
)
async def test_mail_that_needs_no_wake_is_skipped(team, event):
    await team.handler._wake_peer_recipients(event)

    team.started.assert_not_awaited()
    team.restarted.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.level0
async def test_wake_failure_does_not_break_the_handler(team):
    team.started.side_effect = RuntimeError("spawn failed")

    await team.handler._wake_peer_recipients(_direct(UNSTARTED))

    # startup_member rolls the CAS back, so a later message can retry.
    assert await _status(team, UNSTARTED) == MemberStatus.UNSTARTED.value


@pytest.mark.asyncio
@pytest.mark.level0
async def test_leader_message_handling_wakes_the_recipient(team, monkeypatch):
    monkeypatch.setattr(team.handler, "_process_unread_messages", AsyncMock())

    await team.handler.on_message_or_broadcast(_direct(UNSTARTED))

    team.started.assert_awaited_once_with(UNSTARTED)


@pytest.mark.asyncio
@pytest.mark.level0
async def test_teammate_backend_never_wakes(team):
    backend = TeamBackend(
        team_name=TEAM_NAME,
        member_name=SENDER,
        is_leader=False,
        db=team.db,
        messager=AsyncMock(spec=Messager),
        on_member_started=team.started,
    )

    assert await backend.wake_member(UNSTARTED) is False
    team.started.assert_not_awaited()
