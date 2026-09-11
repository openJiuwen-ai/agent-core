# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""发配按需拉起（spawn-on-dispatch）测试：TeamMessageManager 的 member_reviver 钩子。

背景：ERROR（进程级失败）成员没有消费者，派给它的消息/任务静默堆积
（只能等停摆看门狗）。send_message / multicast_message 成功后挂后台拉起，
正常成员不触发；拉起互斥由 reviver 内部 CAS 裁决（TeamAgent._revive_error_teammate）。
"""

import asyncio
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
from openjiuwen.agent_teams.tools.message_manager import TeamMessageManager
from openjiuwen.core.single_agent import AgentCard

TEAM_NAME = "revive_team"
LEADER_NAME = "team_leader"
DEV_ERR = "dev-error"
DEV_OK = "dev-ok"


@pytest_asyncio.fixture
async def db():
    token = set_session_id("revive_session")
    config = DatabaseConfig(db_type=DatabaseType.SQLITE, connection_string=":memory:")
    database = TeamDatabase(config)
    try:
        await database.initialize()
        await database.team.create_team(
            team_name=TEAM_NAME,
            display_name="Revive Team",
            leader_member_name=LEADER_NAME,
        )
        for name, status in (
            (LEADER_NAME, MemberStatus.READY.value),
            (DEV_ERR, MemberStatus.READY.value),
            (DEV_OK, MemberStatus.READY.value),
        ):
            await database.member.create_member(
                member_name=name,
                team_name=TEAM_NAME,
                display_name=name,
                agent_card=AgentCard().model_dump_json(),
                status=status,
                mode=MemberMode.BUILD_MODE.value,
            )
        yield database
    finally:
        reset_session_id(token)
        await database.close()


async def _drain(reviver: AsyncMock | None = None) -> None:
    # ensure_future 的后台任务含 aiosqlite 读（工作线程切换），sleep(0) 不够，
    # 用真实小睡轮询等待其落定
    for _ in range(100):
        await asyncio.sleep(0.01)
        if reviver is not None and reviver.await_count > 0:
            return


@pytest.mark.asyncio
async def test_send_to_error_member_triggers_revive(db):
    await db.member.update_member_status(DEV_ERR, TEAM_NAME, MemberStatus.BUSY.value)
    await db.member.update_member_status(DEV_ERR, TEAM_NAME, MemberStatus.ERROR.value)
    reviver = AsyncMock()
    mm = TeamMessageManager(TEAM_NAME, LEADER_NAME, db, AsyncMock(spec=Messager), member_reviver=reviver)

    message_id = await mm.send_message("派活", to_member_name=DEV_ERR)
    assert message_id
    await _drain(reviver)

    reviver.assert_awaited_once_with(DEV_ERR)


@pytest.mark.asyncio
async def test_send_to_ready_member_no_revive(db):
    reviver = AsyncMock()
    mm = TeamMessageManager(TEAM_NAME, LEADER_NAME, db, AsyncMock(spec=Messager), member_reviver=reviver)

    await mm.send_message("正常派活", to_member_name=DEV_OK)
    await _drain()

    reviver.assert_not_awaited()


@pytest.mark.asyncio
async def test_send_without_reviver_is_noop(db):
    await db.member.update_member_status(DEV_ERR, TEAM_NAME, MemberStatus.BUSY.value)
    await db.member.update_member_status(DEV_ERR, TEAM_NAME, MemberStatus.ERROR.value)
    mm = TeamMessageManager(TEAM_NAME, LEADER_NAME, db, AsyncMock(spec=Messager))

    message_id = await mm.send_message("无钩子", to_member_name=DEV_ERR)
    assert message_id
    await _drain()  # 不抛异常即可


@pytest.mark.asyncio
async def test_multicast_revives_only_error_recipients(db):
    await db.member.update_member_status(DEV_ERR, TEAM_NAME, MemberStatus.BUSY.value)
    await db.member.update_member_status(DEV_ERR, TEAM_NAME, MemberStatus.ERROR.value)
    reviver = AsyncMock()
    mm = TeamMessageManager(TEAM_NAME, LEADER_NAME, db, AsyncMock(spec=Messager), member_reviver=reviver)

    ids = await mm.multicast_message("批量派活", [DEV_ERR, DEV_OK])
    assert len(ids) == 2
    await _drain(reviver)

    reviver.assert_awaited_once_with(DEV_ERR)


@pytest.mark.asyncio
async def test_broadcast_does_not_revive(db):
    await db.member.update_member_status(DEV_ERR, TEAM_NAME, MemberStatus.BUSY.value)
    await db.member.update_member_status(DEV_ERR, TEAM_NAME, MemberStatus.ERROR.value)
    reviver = AsyncMock()
    mm = TeamMessageManager(TEAM_NAME, LEADER_NAME, db, AsyncMock(spec=Messager), member_reviver=reviver)

    await mm.broadcast_message("公告")
    await _drain()

    reviver.assert_not_awaited()
