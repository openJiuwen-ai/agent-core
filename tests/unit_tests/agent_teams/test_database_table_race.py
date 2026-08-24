# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""并发建表回归：create_cur_session_tables 必须并发安全。

背景：成员惰性拉起时多个协程并发初始化同会话动态表，旧的
``table.create(checkfirst=True)`` 是"先查再建"两步、中间有窗口，
并发下炸 ``OperationalError: table ... already exists``。
修复后走原子 DDL ``CREATE TABLE IF NOT EXISTS``。
"""

import asyncio

import pytest
import pytest_asyncio

from openjiuwen.agent_teams.context import reset_session_id, set_session_id
from openjiuwen.agent_teams.tools.database import (
    DatabaseConfig,
    DatabaseType,
    TeamDatabase,
)
from openjiuwen.agent_teams.tools.database.engine import create_cur_session_tables


@pytest_asyncio.fixture
async def team_db():
    token = set_session_id("race-session")
    database = TeamDatabase(
        DatabaseConfig(db_type=DatabaseType.SQLITE, connection_string=":memory:")
    )
    try:
        await database.initialize()
        yield database
    finally:
        await database.close()
        reset_session_id(token)


@pytest.mark.asyncio
async def test_create_cur_session_tables_concurrent_is_race_free(team_db):
    """同一引擎上并发建同会话动态表：全部成功，无 already exists。"""
    results = await asyncio.gather(
        *[create_cur_session_tables(team_db.engine) for _ in range(8)],
        return_exceptions=True,
    )
    errors = [r for r in results if isinstance(r, BaseException)]
    assert errors == []


@pytest.mark.asyncio
async def test_create_cur_session_tables_idempotent_sequential(team_db):
    """顺序重复建表同样幂等（IF NOT EXISTS 语义）。"""
    await create_cur_session_tables(team_db.engine)
    await create_cur_session_tables(team_db.engine)
