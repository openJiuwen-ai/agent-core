# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""回归：bind 期会话建表 DDL 必须走写锁 + 瞬态重试 + 耗尽降级。

``create_cur_session_tables`` 旧实现裸
``engine.begin()`` 绕过 ``DbSessions`` 进程级写锁，多团队并发 bind 时
DDL 占满写池（QueuePool size 2 overflow 0 timeout 10s），检出超时直接
崩溃成员（sqlalchemy.exc.TimeoutError）。修复策略：
  1. DDL 在 ``sessions.write()`` 内执行，与全部 DAO 写串行；
  2. 瞬态错误（OperationalError / SATimeoutError / 看门狗 TimeoutError）
     指数退避重试，预算耗尽降级为 ERROR 日志而非崩溃；
  3. 迁移扫描收窄到当前会话候选表，新建表整体跳过（零 get_indexes PRAGMA）。
"""

import asyncio

import pytest
import pytest_asyncio
from sqlalchemy.exc import OperationalError
from sqlalchemy.exc import TimeoutError as SATimeoutError

from openjiuwen.agent_teams.context import reset_session_id, set_session_id
from openjiuwen.agent_teams.tools.database import (
    DatabaseConfig,
    DatabaseType,
    TeamDatabase,
)
from openjiuwen.agent_teams.tools.database import engine as engine_module


@pytest_asyncio.fixture
async def memory_db():
    # 不在夹具里 set_session_id：pytest-asyncio 下夹具与用例跑在不同
    # Context，夹具里 set 的值用例体看不到、teardown reset 还会抛
    # "created in a different Context"。会话绑定放到各用例体内。
    database = TeamDatabase(
        DatabaseConfig(db_type=DatabaseType.SQLITE, connection_string=":memory:")
    )
    try:
        await database.initialize()
        yield database
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_bind_ddl_runs_under_write_lock(memory_db, monkeypatch):
    """_ensure_session_tables 执行期间进程级写锁必须处于持有状态。"""
    token = set_session_id("lock")
    try:
        observed = []
        real = engine_module._ensure_session_tables

        async def spy(session):
            observed.append(memory_db._sessions._write_lock.locked())
            await real(session)

        monkeypatch.setattr(engine_module, "_ensure_session_tables", spy)
        await memory_db.create_cur_session_tables()
        assert observed == [True]
    finally:
        reset_session_id(token)


@pytest.mark.asyncio
async def test_bind_ddl_retries_transient_operational_error(memory_db, monkeypatch):
    """首次尝试撞 database is locked，退避后重试成功，整体不抛异常。"""
    token = set_session_id("retry-op")
    try:
        calls = {"n": 0}
        real = engine_module._ensure_session_tables

        async def flaky(session):
            calls["n"] += 1
            if calls["n"] == 1:
                raise OperationalError("CREATE TABLE", {}, Exception("database is locked"))
            await real(session)

        monkeypatch.setattr(engine_module, "_ensure_session_tables", flaky)
        monkeypatch.setattr(engine_module, "_DB_RETRY_BASE_DELAY", 0)
        await memory_db.create_cur_session_tables()
        assert calls["n"] == 2
    finally:
        reset_session_id(token)


@pytest.mark.asyncio
async def test_bind_ddl_retries_pool_timeout(memory_db, monkeypatch):
    """检出超时（SATimeoutError）同样走重试且可恢复。"""
    token = set_session_id("retry-pool")
    try:
        calls = {"n": 0}
        real = engine_module._ensure_session_tables

        async def flaky(session):
            calls["n"] += 1
            if calls["n"] == 1:
                raise SATimeoutError("SELECT 1", {}, Exception("pool checkout timeout"))
            await real(session)

        monkeypatch.setattr(engine_module, "_ensure_session_tables", flaky)
        monkeypatch.setattr(engine_module, "_DB_RETRY_BASE_DELAY", 0)
        await memory_db.create_cur_session_tables()
        assert calls["n"] == 2
    finally:
        reset_session_id(token)


@pytest.mark.asyncio
async def test_bind_ddl_budget_exhausted_degrades_without_raising(memory_db, monkeypatch):
    """重试预算耗尽：返回 None 不抛异常（成员不崩），且恰好尝试预算次数。"""
    token = set_session_id("exhausted")
    try:
        calls = {"n": 0}

        async def always_locked(session):
            calls["n"] += 1
            raise OperationalError("CREATE TABLE", {}, Exception("database is locked"))

        monkeypatch.setattr(engine_module, "_ensure_session_tables", always_locked)
        monkeypatch.setattr(engine_module, "_DB_RETRY_BASE_DELAY", 0)
        # 关键断言就是"不抛"——旧实现此处会把 TimeoutError 穿透成成员崩溃。
        await memory_db.create_cur_session_tables()
        assert calls["n"] == engine_module._DB_RETRY_ATTEMPTS
    finally:
        reset_session_id(token)


@pytest.mark.asyncio
async def test_fresh_tables_skip_migration_scan(memory_db, monkeypatch):
    """新建表不进迁移扫描（existing_before_create 为空）；重复 bind 才扫。"""
    token = set_session_id("snapshot")
    try:
        snapshots = []
        real = engine_module._ensure_dynamic_table_indexes

        def spy(sync_conn, candidates, existing_before_create):
            snapshots.append((list(candidates), set(existing_before_create)))
            return real(sync_conn, candidates, existing_before_create)

        monkeypatch.setattr(engine_module, "_ensure_dynamic_table_indexes", spy)

        await memory_db.create_cur_session_tables()
        assert len(snapshots) == 1
        candidates, existing = snapshots[0]
        assert len(candidates) == 3  # task / task_dependency / message
        assert existing == set()  # 全部新建 → 迁移整体跳过

        # 缓存后二次 bind 整体跳过 DDL——用"清缓存模拟进程重启"驱动第二次
        memory_db._sessions.forget_session_tables_ready("snapshot")
        await memory_db.create_cur_session_tables()
        assert len(snapshots) == 2
        _, existing_second = snapshots[1]
        assert existing_second == set(candidates)  # 二次 bind 三表均已存在 → 走迁移
    finally:
        reset_session_id(token)


@pytest.mark.asyncio
async def test_concurrent_binds_and_dao_writes_all_succeed(tmp_path):
    """文件后端（写池 2 槽）下并发 bind DDL 与 DAO 写交错：写锁串行化，零异常。

    旧实现（裸 engine.begin()）在此形态下并发 DDL 直接争抢 2 槽写池，
    是成员崩溃的触发形态。
    """
    db_path = tmp_path / "pm47_concurrent.db"
    database = TeamDatabase(
        DatabaseConfig(db_type=DatabaseType.SQLITE, connection_string=str(db_path))
    )
    token = set_session_id("concurrent")
    try:
        await database.initialize()
        await database.team.create_team("team_pm47", "PM47 Team", "leader1")

        async def bind():
            await database.create_cur_session_tables()

        async def dao_write(i):
            await database.member.create_member(
                member_name=f"member{i}",
                team_name="team_pm47",
                display_name=f"Member {i}",
                agent_card="",
                status="ready",
            )

        results = await asyncio.gather(
            *[bind() for _ in range(6)],
            *[dao_write(i) for i in range(6)],
            return_exceptions=True,
        )
        errors = [r for r in results if isinstance(r, BaseException)]
        assert errors == []
    finally:
        await database.close()
        reset_session_id(token)


def _model_index_names() -> dict[str, set[str]]:
    """当前模型声明的 {表名: 索引名集合}(按当前 session 上下文解析)。"""
    from openjiuwen.agent_teams.tools.models import (
        _get_message_model,
        _get_message_read_status_model,
        _get_review_vote_model,
        _get_task_dependency_model,
        _get_task_model,
    )

    out: dict[str, set[str]] = {}
    for factory in (
            _get_task_model,
            _get_task_dependency_model,
            _get_message_model,
            _get_message_read_status_model,
            _get_review_vote_model,
    ):
        table = factory().__table__
        out[table.name] = {idx.name for idx in table.indexes}
    return out


async def _actual_index_names(db, table_name: str) -> set[str]:
    from sqlalchemy import inspect as sa_inspect

    async with db.engine.begin() as conn:
        indexes = await conn.run_sync(lambda c: sa_inspect(c).get_indexes(table_name))
    return {idx["name"] for idx in indexes}


@pytest.mark.asyncio
async def test_fresh_bind_creates_all_model_indexes(memory_db):
    """首 bind 建表必须带齐模型声明的索引(含 review_vote 的 task_round)。

    回归锚:裸 CreateTable 只发 CREATE TABLE 不建索引——新会话零索引出生,
    第二次 bind 被迁移检测误读为 legacy;review_vote 不在迁移候选里,索引永失。
    """
    token = set_session_id("idx-fresh")
    try:
        await memory_db.create_cur_session_tables()
        expected = _model_index_names()
        for table_name, want in expected.items():
            got = await _actual_index_names(memory_db, table_name)
            missing = want - got
            assert not missing, f"{table_name} 缺索引: {missing}"
    finally:
        reset_session_id(token)


@pytest.mark.asyncio
async def test_second_bind_skips_ddl(memory_db, monkeypatch):
    """同会话第二次 bind 命中成功缓存,不再执行 DDL。"""
    token = set_session_id("cache-skip")
    try:
        calls = {"n": 0}
        real = engine_module._ensure_session_tables

        async def spy(session):
            calls["n"] += 1
            await real(session)

        monkeypatch.setattr(engine_module, "_ensure_session_tables", spy)
        await memory_db.create_cur_session_tables()
        await memory_db.create_cur_session_tables()
        await memory_db.create_cur_session_tables()
        assert calls["n"] == 1
    finally:
        reset_session_id(token)


@pytest.mark.asyncio
async def test_concurrent_first_binds_collapse(memory_db, monkeypatch):
    """并发首绑坍缩成一次首领运行,跟随者等完成事件。"""
    token = set_session_id("cache-collapse")
    try:
        calls = {"n": 0}
        real = engine_module._ensure_session_tables

        async def spy(session):
            calls["n"] += 1
            await asyncio.sleep(0.01)  # 放大并发窗口
            await real(session)

        monkeypatch.setattr(engine_module, "_ensure_session_tables", spy)
        await asyncio.gather(
            *[memory_db.create_cur_session_tables() for _ in range(10)]
        )
        assert calls["n"] == 1
    finally:
        reset_session_id(token)


@pytest.mark.asyncio
async def test_drop_invalidates_cache(memory_db, monkeypatch):
    """删表路径清缓存——删后重建必须重新执行 DDL,否则 no such table。"""
    token = set_session_id("cache-drop")
    try:
        calls = {"n": 0}
        real = engine_module._ensure_session_tables

        async def spy(session):
            calls["n"] += 1
            await real(session)

        monkeypatch.setattr(engine_module, "_ensure_session_tables", spy)
        await memory_db.create_cur_session_tables()
        assert calls["n"] == 1

        await memory_db.drop_session_tables_by_id("cache-drop")
        assert not memory_db._sessions.is_session_tables_ready("cache-drop")

        await memory_db.create_cur_session_tables()
        assert calls["n"] == 2
    finally:
        reset_session_id(token)


@pytest.mark.asyncio
async def test_concurrent_drop_during_bind_ddl_not_memoised(memory_db, monkeypatch):
    """drop 路径与 bind DDL 并发（drop 走裸 engine.begin() 不持写锁）：
    forget 落在 leader 的 DDL 与 memoize 之间时，memo 不得写回
    「版本齐但表已删」的脏缓存（否则后续 bind 全部跳过建表，
    DAO 写落 no such table）。drop 代数门：DDL 期间代数变化即不写。"""
    token = set_session_id("cache-race")
    try:
        calls = {"n": 0}
        real = engine_module._ensure_session_tables

        async def spy(session):
            calls["n"] += 1
            await real(session)
            if calls["n"] == 1:
                # 模拟并发 drop 路径的缓存失效（drop 本身走独立连接，
                # 此处只复现其对 memo 的 forget 副作用）
                memory_db._sessions.forget_session_tables_ready("cache-race")

        monkeypatch.setattr(engine_module, "_ensure_session_tables", spy)
        await memory_db.create_cur_session_tables()
        assert calls["n"] == 1
        assert not memory_db._sessions.is_session_tables_ready("cache-race"), (
            "DDL 期间发生 drop 时不得 memoize（脏缓存）"
        )

        # 下一次 bind 必须重跑 DDL 并正常入缓存
        await memory_db.create_cur_session_tables()
        assert calls["n"] == 2
        assert memory_db._sessions.is_session_tables_ready("cache-race")
    finally:
        reset_session_id(token)


@pytest.mark.asyncio
async def test_concurrent_drop_during_version_read_not_memoised(memory_db, monkeypatch):
    """drop 落在 memoize 前的 schema_version 读取期间（复检与写回之间的
    await 窗口）同样不得写入脏缓存——代数次序必须是「读版本→复检→同步写」，
    复检与写回之间无 await。

    钉注：本用例对「先复检、后在 memoize 实参内 await 读版本」的旧次序
    必红（forget 落在复检之后，脏版本被写回）——次序回退时它应当失败。
    """
    token = set_session_id("cache-race-read")
    try:
        sessions = memory_db._sessions
        real_read = sessions.current_schema_version
        read_calls = {"n": 0}

        async def spy_read():
            read_calls["n"] += 1
            version = await real_read()
            if read_calls["n"] == 1:
                # 模拟并发 drop 路径的缓存失效落在版本读取期间
                sessions.forget_session_tables_ready("cache-race-read")
            return version

        monkeypatch.setattr(sessions, "current_schema_version", spy_read)
        await memory_db.create_cur_session_tables()
        assert not sessions.is_session_tables_ready("cache-race-read"), (
            "版本读取期间发生 drop 时不得 memoize（脏缓存）"
        )

        # 下一次 bind 重跑 DDL 并正常入缓存
        await memory_db.create_cur_session_tables()
        assert sessions.is_session_tables_ready("cache-race-read")
    finally:
        reset_session_id(token)


@pytest.mark.asyncio
async def test_follower_recontends_after_degraded_leader(memory_db, monkeypatch):
    """follower 只等 Event 不校验结果 = leader 降级/取消时静默假成功。
    修复后：leader 预算耗尽（未 memoize）时 follower 重新竞首领、
    跑自己的重试预算直至建表成功入缓存。"""
    token = set_session_id("follower-recontend")
    try:
        calls = {"n": 0}
        real = engine_module._ensure_session_tables
        leader_started = asyncio.Event()
        leader_proceed = asyncio.Event()

        async def spy(session):
            calls["n"] += 1
            if calls["n"] == 1:
                # leader 首轮：挂起等 follower 就位后再失败
                leader_started.set()
                await leader_proceed.wait()
            if calls["n"] <= engine_module._DB_RETRY_ATTEMPTS:
                # leader 的全部预算尝试都失败（耗尽降级，不 memoize）；
                # follower 等的是 leader 收尾才放的 Event，不会插进前 N 次
                raise OperationalError("CREATE TABLE", {}, Exception("database is locked"))
            await real(session)

        monkeypatch.setattr(engine_module, "_ensure_session_tables", spy)
        monkeypatch.setattr(engine_module, "_DB_RETRY_BASE_DELAY", 0)

        leader_task = asyncio.create_task(memory_db.create_cur_session_tables())
        await leader_started.wait()
        follower_task = asyncio.create_task(memory_db.create_cur_session_tables())
        await asyncio.sleep(0.05)  # follower 注册为跟随者
        leader_proceed.set()
        await asyncio.gather(leader_task, follower_task)

        assert calls["n"] == engine_module._DB_RETRY_ATTEMPTS + 1, (
            "leader 耗尽后 follower 必须重跑一次 DDL"
        )
        assert memory_db._sessions.is_session_tables_ready("follower-recontend"), (
            "follower 重跑成功后必须入缓存（不得静默假成功）"
        )
    finally:
        reset_session_id(token)


@pytest.mark.asyncio
async def test_degraded_bind_not_memoised(memory_db, monkeypatch):
    """重试预算耗尽的降级运行不入缓存,下一次 bind 必须重试 DDL。"""
    token = set_session_id("cache-degrade")
    try:
        calls = {"n": 0}

        async def always_locked(session):
            calls["n"] += 1
            raise OperationalError("CREATE TABLE", {}, Exception("database is locked"))

        monkeypatch.setattr(engine_module, "_ensure_session_tables", always_locked)
        monkeypatch.setattr(engine_module, "_DB_RETRY_BASE_DELAY", 0)
        await memory_db.create_cur_session_tables()  # 降级,不抛
        assert calls["n"] == engine_module._DB_RETRY_ATTEMPTS
        assert not memory_db._sessions.is_session_tables_ready("cache-degrade")

        # 恢复真身后重试:必须再跑一次(成功入缓存)
        monkeypatch.undo()
        await memory_db.create_cur_session_tables()
        assert memory_db._sessions.is_session_tables_ready("cache-degrade")
    finally:
        reset_session_id(token)


@pytest.mark.asyncio
async def test_regression_window_indexless_table_healed(memory_db):
    """回归窗口存量表(零索引出生)自愈:首 bind 的 ensure 幂等补建。"""
    from openjiuwen.agent_teams.tools.models import _sanitize_session_id_for_table

    sid = "cache-heal"
    suffix = _sanitize_session_id_for_table(sid)
    token = set_session_id(sid)
    try:
        # 模拟回归窗口:手工建零索引裸表(模拟旧 CreateTable 行为的产物),
        # 列只带索引引用到的字段,其余列由列迁移/后续逻辑不管(本用例只验索引)
        async with memory_db.engine.begin() as conn:
            await conn.exec_driver_sql(
                f'CREATE TABLE IF NOT EXISTS "team_task_{suffix}" '
                "(id INTEGER PRIMARY KEY, assignee TEXT, status TEXT)"
            )
            await conn.exec_driver_sql(
                f'CREATE TABLE IF NOT EXISTS "team_message_{suffix}" '
                "(id INTEGER PRIMARY KEY, to_member_name TEXT, is_read INTEGER, "
                "timestamp INTEGER, broadcast INTEGER)"
            )
            await conn.exec_driver_sql(
                f'CREATE TABLE IF NOT EXISTS "team_review_vote_{suffix}" '
                "(id INTEGER PRIMARY KEY, task_id TEXT, review_round INTEGER)"
            )
        await memory_db.create_cur_session_tables()
        expected = _model_index_names()
        for table_name, want in expected.items():
            got = await _actual_index_names(memory_db, table_name)
            missing = want - got
            assert not missing, f"{table_name} 自愈失败,仍缺: {missing}"
    finally:
        reset_session_id(token)


@pytest.mark.asyncio
async def test_external_schema_change_invalidates_cache(memory_db, monkeypatch):
    """进程外动库(裸 engine 删表,绕过本进程失效钩子)→ 版本不符 → 重建。"""
    from openjiuwen.agent_teams.tools.models import (
        _get_task_model,
        _sanitize_session_id_for_table,
    )

    token = set_session_id("ver-external")
    try:
        calls = {"n": 0}
        real = engine_module._ensure_session_tables

        async def spy(session):
            calls["n"] += 1
            await real(session)

        monkeypatch.setattr(engine_module, "_ensure_session_tables", spy)
        await memory_db.create_cur_session_tables()
        assert calls["n"] == 1
        assert memory_db._sessions.is_session_tables_ready("ver-external")

        # 模拟进程外动库:直连 engine 裸删表(不走 TeamDatabase 失效钩子)
        table = _get_task_model().__table__.name
        async with memory_db.engine.begin() as conn:
            await conn.exec_driver_sql(f'DROP TABLE IF EXISTS "{table}"')

        # 下一次 bind:版本不符 → 重跑 DDL 重建
        await memory_db.create_cur_session_tables()
        assert calls["n"] == 2
        assert memory_db._sessions.is_session_tables_ready("ver-external")
        # 表确实被建回来了
        async with memory_db.engine.begin() as conn:
            exists = await conn.run_sync(
                lambda c: __import__("sqlalchemy").inspect(c).has_table(table)
            )
        assert exists
    finally:
        reset_session_id(token)


@pytest.mark.asyncio
async def test_version_read_failure_treated_as_miss(memory_db, monkeypatch):
    """版本读取失败按未命中处理:重跑幂等 DDL,不崩、不误跳过。"""
    token = set_session_id("ver-readfail")
    try:
        calls = {"n": 0}
        real = engine_module._ensure_session_tables

        async def spy(session):
            calls["n"] += 1
            await real(session)

        monkeypatch.setattr(engine_module, "_ensure_session_tables", spy)
        await memory_db.create_cur_session_tables()
        assert calls["n"] == 1

        # 第二次 bind 的版本比对读失败 → 按未命中重跑;成功后记录版本恢复
        real_version = memory_db._sessions.current_schema_version
        state = {"fail": True}

        async def flaky_version():
            if state["fail"]:
                raise RuntimeError("read pool wedged")
            return await real_version()

        monkeypatch.setattr(
            memory_db._sessions, "current_schema_version", flaky_version
        )
        await memory_db.create_cur_session_tables()  # 比对失败 → 重跑 → 记录又失败 → 不缓存
        assert calls["n"] == 2
        assert not memory_db._sessions.is_session_tables_ready("ver-readfail")

        state["fail"] = False
        await memory_db.create_cur_session_tables()  # 恢复后建成并缓存
        assert calls["n"] == 3
        assert memory_db._sessions.is_session_tables_ready("ver-readfail")
    finally:
        reset_session_id(token)


@pytest.mark.asyncio
async def test_unchanged_version_skips_ddl(memory_db, monkeypatch):
    """版本不变 → 第二次 bind 零 DDL(与现行缓存语义一致,只是多了版本证据)。"""
    token = set_session_id("ver-skip")
    try:
        calls = {"n": 0}
        real = engine_module._ensure_session_tables

        async def spy(session):
            calls["n"] += 1
            await real(session)

        monkeypatch.setattr(engine_module, "_ensure_session_tables", spy)
        await memory_db.create_cur_session_tables()
        await memory_db.create_cur_session_tables()
        assert calls["n"] == 1
        assert isinstance(
            memory_db._sessions._tables_ready.get("ver-skip"), int
        )
    finally:
        reset_session_id(token)


@pytest.mark.asyncio
async def test_schema_version_probe_disabled_on_non_sqlite():
    """非 SQLite 后端无 PRAGMA schema_version：探针一次性自检后停用，
    每次 bind 按未命中重跑幂等 DDL（预缓存期行为），不再发起注定失败的查询。"""
    from contextlib import asynccontextmanager

    sessions = engine_module.DbSessions(None, None)
    calls = {"n": 0}

    class _FakeConn:
        class dialect:
            name = "postgresql"

    class _FakeSession:
        async def connection(self):
            calls["n"] += 1
            return _FakeConn()

    @asynccontextmanager
    async def _fake_read():
        yield _FakeSession()

    sessions.read = _fake_read
    assert await sessions.current_schema_version() is None
    assert sessions._schema_version_unsupported
    assert await sessions.current_schema_version() is None
    assert calls["n"] == 1, "停用后不得再发起探测查询"


def test_bind_ddl_memo_accessors():
    """memo/并发闸封装：None 版本不 memo（无 cookie 后端）；
    inflight 首个调用者是 leader、后续为 follower，end 释放。"""
    sessions = engine_module.DbSessions(None, None)

    sessions.memoize_session_tables_ready("s1", None)
    assert sessions.get_session_tables_ready_version("s1") is None
    sessions.memoize_session_tables_ready("s1", 7)
    assert sessions.get_session_tables_ready_version("s1") == 7
    sessions.forget_session_tables_ready("s1")
    assert sessions.get_session_tables_ready_version("s1") is None

    ev1, leader1 = sessions.begin_session_tables_inflight("s1")
    assert leader1
    ev2, leader2 = sessions.begin_session_tables_inflight("s1")
    assert not leader2 and ev2 is ev1
    sessions.end_session_tables_inflight("s1", ev1)
    assert ev1.is_set()
    ev3, leader3 = sessions.begin_session_tables_inflight("s1")
    assert leader3 and ev3 is not ev1


def test_create_table_with_indexes_mysql_probes_existence(monkeypatch):
    """MySQL 无 CREATE INDEX IF NOT EXISTS：按方言先探测、只建缺失索引。"""
    import sqlalchemy as sa

    metadata = sa.MetaData()
    table = sa.Table(
        "team_task_demo",
        metadata,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("round", sa.Integer),
    )
    sa.Index("ix_team_task_demo_round", table.c.round)

    executed = []

    class _FakeDialect:
        name = "mysql"

    class _FakeConn:
        dialect = _FakeDialect()

        def execute(self, clause, *args, **kwargs):
            executed.append(clause)

    class _FakeInspector:
        def __init__(self, existing):
            self._existing = existing

        def get_indexes(self, name):
            return [{"name": n} for n in self._existing]

    # 索引不存在 → 补建（不带 IF NOT EXISTS）
    monkeypatch.setattr(
        engine_module, "inspect", lambda conn: _FakeInspector([])
    )
    engine_module._create_table_with_indexes(_FakeConn(), table)
    kinds = [type(c).__name__ for c in executed]
    assert kinds == ["CreateTable", "CreateIndex"]
    assert executed[0].if_not_exists is True
    assert executed[1].if_not_exists is False

    # 索引已存在 → 跳过
    executed.clear()
    monkeypatch.setattr(
        engine_module,
        "inspect",
        lambda conn: _FakeInspector(["ix_team_task_demo_round"]),
    )
    engine_module._create_table_with_indexes(_FakeConn(), table)
    assert [type(c).__name__ for c in executed] == ["CreateTable"]


def test_create_table_with_indexes_mysql_tolerates_concurrent_duplicate(monkeypatch):
    """MySQL 并发首绑竞态：探测后对方已建同名索引 → 1061 容忍（幂等同义
    成功）；非 1061 错误原样上抛（不得掩盖真问题）。"""
    import sqlalchemy as sa

    metadata = sa.MetaData()
    table = sa.Table(
        "team_task_demo2",
        metadata,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("round", sa.Integer),
    )
    sa.Index("ix_team_task_demo2_round", table.c.round)

    class _DbapiError(Exception):
        pass

    class _SaDupError(Exception):
        def __init__(self):
            super().__init__("Duplicate key name")
            self.orig = _DbapiError(1061, "Duplicate key name 'ix_team_task_demo2_round'")

    class _SaOtherError(Exception):
        def __init__(self):
            super().__init__("gone away")
            self.orig = _DbapiError(2006, "MySQL server has gone away")

    class _FakeDialect:
        name = "mysql"

    class _DupConn:
        dialect = _FakeDialect()
        boom = None

        def execute(self, clause, *args, **kwargs):
            if type(clause).__name__ == "CreateIndex" and self.boom is not None:
                raise self.boom

    class _FakeInspector:
        def get_indexes(self, name):
            return []  # 探测时索引不存在（对方尚未建）

    monkeypatch.setattr(
        engine_module, "inspect", lambda conn: _FakeInspector()
    )

    conn = _DupConn()
    conn.boom = _SaDupError()
    # 1061 并发重复 → 容忍，不抛
    engine_module._create_table_with_indexes(conn, table)

    conn.boom = _SaOtherError()
    with pytest.raises(_SaOtherError):
        engine_module._create_table_with_indexes(conn, table)
