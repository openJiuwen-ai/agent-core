# coding: utf-8
"""TeamMonitor.get_message（主键点查）回归测试。

背景：jiuwenswarm 团队监控此前对每个 monitor 事件都 ``get_messages()``
全量拉取消息表再按 id 内存过滤，是 team.db 读池最大的固定负载。
``get_message`` 改为走 ``MessageDao.get_message`` 主键点查，并逐字保持
``hide_dm`` 语义（开启时非广播消息视为不存在）。
"""

import pytest
import pytest_asyncio

from openjiuwen.agent_teams.context import set_session_id
from openjiuwen.agent_teams.monitor.team_monitor import TeamMonitor
from openjiuwen.agent_teams.tools.database import (
    DatabaseConfig,
    DatabaseType,
    TeamDatabase,
)

_SESSION_ID = "monitor_get_message_session"
_TEAM_NAME = "team"


@pytest.fixture
def db_config() -> DatabaseConfig:
    return DatabaseConfig(db_type=DatabaseType.SQLITE, connection_string=":memory:")


@pytest_asyncio.fixture
async def db(db_config):
    # 所有 DB 操作必须收拢在同一个 fixture 协程里：session_id 走 ContextVar，
    # pytest-asyncio 的每个 fixture/测试各跑一个 coroutine（不同 Context），
    # 拆出第二个 fixture 会导致 seeded 协程丢失会话上下文（表名哈希错位）。
    token = set_session_id(_SESSION_ID)  # noqa: F841 — 见 finally 注释，刻意不复位
    database = TeamDatabase(db_config)
    try:
        await database.initialize()
        await database.team.create_team(
            team_name=_TEAM_NAME,
            display_name="Monitor Team",
            leader_member_name="team_leader",
        )
        ok_direct = await database.message.create_message(
            message_id="msg-dm",
            team_name=_TEAM_NAME,
            from_member_name="team_leader",
            content="direct body",
            to_member_name="worker-1",
            broadcast=False,
        )
        ok_broadcast = await database.message.create_message(
            message_id="msg-bc",
            team_name=_TEAM_NAME,
            from_member_name="team_leader",
            content="broadcast body",
            broadcast=True,
        )
        # 同 session 表内另一团队的消息行（表按 session 建、行按 team_name 区分；
        # team_name 有 team_info 外键，须先建行）
        await database.team.create_team(
            team_name="team-other",
            display_name="Other Team",
            leader_member_name="other_leader",
        )
        ok_foreign = await database.message.create_message(
            message_id="msg-foreign",
            team_name="team-other",
            from_member_name="other_leader",
            content="foreign team body",
            broadcast=True,
        )
        assert ok_direct and ok_broadcast and ok_foreign
        yield database
    finally:
        # 不在 teardown 里 reset_session_id(token)：pytest-asyncio 的
        # fixture setup/teardown 跑在不同 coroutine（不同 Context），
        # 跨 Context 复位 token 必抛 ValueError（本仓既有夹具病，
        # test_member_revive_on_dispatch.py 等存量文件同款 ERROR）。
        # token 所属的 setup Context 随 coroutine 结束即销毁，不复位无泄漏。
        await database.close()


def _make_monitor(database, *, hide_dm: bool = False) -> TeamMonitor:
    # team_agent is only touched by start()/stop() (event listener wiring);
    # query-only tests never start the monitor, so a placeholder suffices.
    return TeamMonitor(
        team_name=_TEAM_NAME,
        session_id=_SESSION_ID,
        db=database,
        team_agent=None,  # type: ignore[arg-type]
        hide_dm=hide_dm,
    )


@pytest.mark.asyncio
async def test_get_message_returns_row_by_id(db):
    monitor = _make_monitor(db)

    info = await monitor.get_message("msg-dm")
    assert info is not None
    assert info.message_id == "msg-dm"
    assert info.content == "direct body"


@pytest.mark.asyncio
async def test_get_message_unknown_id_returns_none(db):
    monitor = _make_monitor(db)

    assert await monitor.get_message("msg-missing") is None


@pytest.mark.asyncio
async def test_get_message_hide_dm_hides_direct_but_keeps_broadcast(db):
    monitor = _make_monitor(db, hide_dm=True)

    assert await monitor.get_message("msg-dm") is None
    broadcast = await monitor.get_message("msg-bc")
    assert broadcast is not None
    assert broadcast.content == "broadcast body"


@pytest.mark.asyncio
async def test_get_message_other_team_in_same_session_is_invisible(db):
    """团队作用域：同 session 表内他团队的消息按不存在处理（检视修订）。

    消息表按 session 建、行按 team_name 区分；monitor 绑定单团队，
    拿到他团队的 message_id 也不得返回其内容（包括广播正文）。
    """
    monitor = _make_monitor(db)

    assert await monitor.get_message("msg-foreign") is None


@pytest.mark.asyncio
async def test_get_message_is_point_query_not_full_scan(db, monkeypatch):
    """The point query must not route through the full-mailbox scan APIs."""

    async def _explode(*args, **kwargs):  # pragma: no cover - failure marker
        raise AssertionError("get_message must not use the full-scan DAO path")

    monkeypatch.setattr(db.message, "get_messages", _explode)
    monkeypatch.setattr(db.message, "get_team_messages", _explode)
    monitor = _make_monitor(db)

    info = await monitor.get_message("msg-dm")
    assert info is not None
    assert info.message_id == "msg-dm"
