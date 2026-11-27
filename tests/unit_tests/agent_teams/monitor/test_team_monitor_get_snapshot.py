# coding: utf-8
"""TeamMonitor.get_snapshot（快照三读合一）回归测试。

背景：客户端快照（ws 刷新端点 / 卡死看门狗 45s 巡检 / 流末补发）此前
经 ``get_members`` + ``get_team_info`` + ``get_tasks`` 串行 3 次读池
checkout。``get_snapshot`` 改走 ``TeamDatabase.fetch_team_snapshot_probe``
单连接取齐（同时消除三读不同瞬间的错位），行为与三读结果一致。
"""

import pytest
import pytest_asyncio

from openjiuwen.agent_teams.context import set_session_id
from openjiuwen.agent_teams.monitor.team_monitor import TeamMonitor
from openjiuwen.agent_teams.schema.status import MemberStatus
from openjiuwen.agent_teams.tools.database import (
    DatabaseConfig,
    DatabaseType,
    TeamDatabase,
)

_SESSION_ID = "monitor_get_snapshot_session"
_TEAM_NAME = "team"


@pytest.fixture
def db_config() -> DatabaseConfig:
    return DatabaseConfig(db_type=DatabaseType.SQLITE, connection_string=":memory:")


@pytest_asyncio.fixture
async def db(db_config):
    # 同 test_team_monitor_get_message.py：DB 操作收拢在同一个 fixture 协程里
    # （session_id 走 ContextVar，跨 Context 复位 token 必抛 ValueError 的
    # 既有夹具病；token 随 setup Context 结束即销毁，不复位无泄漏）。
    token = set_session_id(_SESSION_ID)  # noqa: F841
    database = TeamDatabase(db_config)
    try:
        await database.initialize()
        await database.team.create_team(
            team_name=_TEAM_NAME,
            display_name="Snapshot Team",
            leader_member_name="team_leader",
        )
        await database.member.create_member(
            member_name="team_leader",
            team_name=_TEAM_NAME,
            display_name="Leader",
            agent_card="{}",
            status=MemberStatus.BUSY.value,
            role="leader",
        )
        await database.member.create_member(
            member_name="worker-1",
            team_name=_TEAM_NAME,
            display_name="Worker",
            agent_card="{}",
            status=MemberStatus.READY.value,
        )
        await database.task.create_task(
            task_id="t-snap-1",
            team_name=_TEAM_NAME,
            title="research",
            content="body",
            status="in_progress",
        )
        yield database
    finally:
        await database.close()


def _make_monitor(database) -> TeamMonitor:
    return TeamMonitor(
        team_name=_TEAM_NAME,
        session_id=_SESSION_ID,
        db=database,
        team_agent=None,  # type: ignore[arg-type]
    )


@pytest.mark.asyncio
@pytest.mark.level1
async def test_get_snapshot_matches_separate_reads(db):
    """快照内容与三个独立读结果一致（成员/团队信息/任务）。"""
    monitor = _make_monitor(db)

    snapshot = await monitor.get_snapshot()

    assert snapshot is not None
    team_info, members, tasks = snapshot
    expected_team = await monitor.get_team_info()
    assert team_info is not None and expected_team is not None
    assert team_info.leader_member_name == expected_team.leader_member_name
    assert {m.member_name for m in members} == {
        m.member_name for m in await monitor.get_members()
    }
    assert {t.task_id for t in tasks} == {
        t.task_id for t in await monitor.get_tasks()
    }
    assert {t.task_id for t in tasks} == {"t-snap-1"}


@pytest.mark.asyncio
@pytest.mark.level1
async def test_get_snapshot_uses_single_read_connection(db, monkeypatch):
    """三视图出自同一读块：整次 get_snapshot 只 checkout 一次。"""
    monitor = _make_monitor(db)

    checkout_count = 0
    original_read = db._sessions.read

    def counting_read():
        nonlocal checkout_count
        checkout_count += 1
        return original_read()

    monkeypatch.setattr(db._sessions, "read", counting_read)

    snapshot = await monitor.get_snapshot()

    assert snapshot is not None
    assert checkout_count == 1


@pytest.mark.asyncio
@pytest.mark.level1
async def test_get_snapshot_returns_none_when_probe_unavailable(db, monkeypatch):
    """探针重试耗尽 → None（调用方按"快照不可得"处理，与既有
    "任一读异常→整体 None"契约一致）。"""
    from unittest.mock import AsyncMock

    monitor = _make_monitor(db)
    monkeypatch.setattr(db, "fetch_team_snapshot_probe", AsyncMock(return_value=None))

    assert await monitor.get_snapshot() is None
