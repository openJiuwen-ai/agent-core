# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""TeamBackend.set_member_status 测试（DAO 直写点补发 MemberStatusChangedEvent）。

背景：spawn 重试耗尽（ERROR）、kernel pause/stop 标记（PAUSED/STOPPED）等写点
此前直接走 DAO，不发 status_changed 事件，前端只能等快照轮询才发现成员失败/
暂停。set_member_status 与 TeamMemberHandle.update_status 同语义：
读旧值 → DAO 校验写 → 成功后发事件；事件失败不影响写结果。
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
from openjiuwen.core.single_agent import AgentCard

TEAM_NAME = "status_event_team"
LEADER_NAME = "team_leader"
DEV_1 = "dev-1"


@pytest_asyncio.fixture
async def db():
    token = set_session_id("status_event_session")
    config = DatabaseConfig(db_type=DatabaseType.SQLITE, connection_string=":memory:")
    database = TeamDatabase(config)
    try:
        await database.initialize()
        await database.team.create_team(
            team_name=TEAM_NAME,
            display_name="Status Event Team",
            leader_member_name=LEADER_NAME,
        )
        for name in (LEADER_NAME, DEV_1):
            await database.member.create_member(
                member_name=name,
                team_name=TEAM_NAME,
                display_name=name,
                agent_card=AgentCard().model_dump_json(),
                status=MemberStatus.READY.value,
                mode=MemberMode.BUILD_MODE.value,
            )
        yield database
    finally:
        reset_session_id(token)
        await database.close()


def _backend(db, messager) -> TeamBackend:
    return TeamBackend(
        team_name=TEAM_NAME,
        member_name=LEADER_NAME,
        is_leader=True,
        db=db,
        messager=messager,
    )


@pytest.mark.asyncio
async def test_set_member_status_writes_and_publishes(db):
    messager = AsyncMock(spec=Messager)
    backend = _backend(db, messager)

    ok = await backend.set_member_status(DEV_1, MemberStatus.PAUSED)

    assert ok is True
    assert await db.member.get_member_status(TEAM_NAME, DEV_1) == "paused"
    messager.publish.assert_awaited_once()
    event_msg = messager.publish.await_args.kwargs["message"]
    event = event_msg.event if hasattr(event_msg, "event") else event_msg
    payload = getattr(event, "payload", None) or getattr(event, "data", None) or event
    # 载荷含 member/新旧状态（EventMessage 包装形态不锁死，锁语义字段）
    text = str(payload)
    assert DEV_1 in text
    assert "paused" in text


@pytest.mark.asyncio
async def test_set_member_status_same_status_is_noop_no_event(db):
    messager = AsyncMock(spec=Messager)
    backend = _backend(db, messager)

    ok = await backend.set_member_status(DEV_1, MemberStatus.READY)

    assert ok is True
    messager.publish.assert_not_awaited()


@pytest.mark.asyncio
async def test_set_member_status_unknown_member_no_write_no_event(db):
    messager = AsyncMock(spec=Messager)
    backend = _backend(db, messager)

    ok = await backend.set_member_status("ghost", MemberStatus.ERROR)

    assert ok is False
    messager.publish.assert_not_awaited()


@pytest.mark.asyncio
async def test_set_member_status_publish_failure_keeps_write(db):
    messager = AsyncMock(spec=Messager)
    messager.publish.side_effect = RuntimeError("bus down")
    backend = _backend(db, messager)

    ok = await backend.set_member_status(DEV_1, MemberStatus.PAUSED)

    assert ok is True
    assert await db.member.get_member_status(TEAM_NAME, DEV_1) == "paused"
