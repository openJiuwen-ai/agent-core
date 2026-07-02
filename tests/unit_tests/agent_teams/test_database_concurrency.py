# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Concurrency tests for the SQLite team database.

Covers the write-serialisation model added to fight ``QueuePool limit``
pool exhaustion under multi-member in-process workloads: WAL + NORMAL
pragmas, a process-wide write lock (``DbSessions``), batched mark-read,
and the locked-database retry helper.
"""

import asyncio

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from openjiuwen.agent_teams.context import reset_session_id, set_session_id
from openjiuwen.agent_teams.schema.status import MemberStatus, TaskStatus
from openjiuwen.agent_teams.tools.database import (
    DatabaseConfig,
    DatabaseType,
    TeamDatabase,
)
from openjiuwen.agent_teams.tools.database.engine import DbSessions, retry_on_locked
from openjiuwen.core.single_agent import AgentCard


@pytest_asyncio.fixture
async def file_db(tmp_path):
    """Initialized file-backed SQLite db (WAL enabled, unlike ``:memory:``)."""
    token = set_session_id("pool_session")
    config = DatabaseConfig(
        db_type=DatabaseType.SQLITE,
        connection_string=str(tmp_path / "team.db"),
    )
    database = TeamDatabase(config)
    try:
        await database.initialize()
        yield database
    finally:
        await database.close()
        reset_session_id(token)


@pytest.mark.asyncio
@pytest.mark.level0
async def test_sqlite_pragmas_enable_wal_normal_sync_and_read_tuning(file_db: TeamDatabase) -> None:
    """A file-backed db runs WAL + NORMAL plus the read / I/O tuning pragmas."""
    async with file_db.session_local() as session:
        journal_mode = (await session.execute(text("PRAGMA journal_mode"))).scalar()
        synchronous = (await session.execute(text("PRAGMA synchronous"))).scalar()
        temp_store = (await session.execute(text("PRAGMA temp_store"))).scalar()
        cache_size = (await session.execute(text("PRAGMA cache_size"))).scalar()
        mmap_size = (await session.execute(text("PRAGMA mmap_size"))).scalar()
    assert journal_mode == "wal"
    # SQLite synchronous levels: 0=OFF, 1=NORMAL, 2=FULL, 3=EXTRA.
    assert synchronous == 1
    # temp_store levels: 0=DEFAULT, 1=FILE, 2=MEMORY.
    assert temp_store == 2
    assert cache_size == -65536
    assert mmap_size == 268435456


@pytest.mark.asyncio
@pytest.mark.level0
async def test_file_db_splits_read_and_write_engines(file_db: TeamDatabase) -> None:
    """File-backed SQLite runs a separate reader pool from the single writer."""
    assert file_db.engine is not None
    assert file_db.read_engine is not None
    assert file_db.read_engine is not file_db.engine
    assert file_db.read_session_local is not file_db.session_local


@pytest.mark.asyncio
@pytest.mark.level0
async def test_reader_connection_uses_smaller_cache(file_db: TeamDatabase) -> None:
    """Reader connections carry the small per-connection cache (default 8 MiB)."""
    async with file_db.read_session_local() as session:
        cache_size = (await session.execute(text("PRAGMA cache_size"))).scalar()
    # read_cache_size_kb default 8192 -> negative KiB form.
    assert cache_size == -8192


@pytest.mark.asyncio
@pytest.mark.level0
async def test_memory_db_shares_single_engine(tmp_path) -> None:
    """A :memory: db cannot split — read/write share one StaticPool engine."""
    token = set_session_id("mem_split_session")
    database = TeamDatabase(DatabaseConfig(db_type=DatabaseType.SQLITE, connection_string=":memory:"))
    try:
        await database.initialize()
        assert database.engine is not None
        assert database.read_engine is database.engine
        assert database.read_session_local is database.session_local
    finally:
        await database.close()
        reset_session_id(token)


@pytest.mark.asyncio
@pytest.mark.level1
async def test_pool_and_cache_knobs_are_honored(tmp_path) -> None:
    """DatabaseConfig pool / cache knobs flow through to the engines."""
    token = set_session_id("knob_session")
    config = DatabaseConfig(
        db_type=DatabaseType.SQLITE,
        connection_string=str(tmp_path / "team.db"),
        read_pool_size=3,
        read_cache_size_kb=4096,
        write_cache_size_kb=32768,
    )
    database = TeamDatabase(config)
    try:
        await database.initialize()
        assert database.read_engine.pool.size() == 3
        async with database.read_session_local() as session:
            read_cache = (await session.execute(text("PRAGMA cache_size"))).scalar()
        async with database.session_local() as session:
            write_cache = (await session.execute(text("PRAGMA cache_size"))).scalar()
        assert read_cache == -4096
        assert write_cache == -32768
    finally:
        await database.close()
        reset_session_id(token)


@pytest.mark.asyncio
@pytest.mark.level1
async def test_default_config_keeps_in_commit_autocheckpoint(file_db: TeamDatabase) -> None:
    """Default (wal_checkpoint_interval_s=0): no background task, autocheckpoint=1000."""
    assert file_db._checkpoint_task is None
    async with file_db.session_local() as session:
        writer_ckpt = (await session.execute(text("PRAGMA wal_autocheckpoint"))).scalar()
    assert writer_ckpt == 1000


@pytest.mark.asyncio
@pytest.mark.level1
async def test_background_checkpointer_moves_checkpoint_off_write_path(tmp_path) -> None:
    """wal_checkpoint_interval_s>0 disables the writer's in-commit checkpoint.

    The writer connection reports ``wal_autocheckpoint=0`` (so no commit ever
    stalls on a checkpoint) while a background task drives PASSIVE checkpoints;
    the reader keeps the configured threshold. ``close()`` cancels the task.
    """
    token = set_session_id("ckpt_session")
    config = DatabaseConfig(
        db_type=DatabaseType.SQLITE,
        connection_string=str(tmp_path / "team.db"),
        wal_checkpoint_interval_s=0.05,
    )
    database = TeamDatabase(config)
    try:
        await database.initialize()
        assert database._checkpoint_task is not None
        assert not database._checkpoint_task.done()

        async with database.session_local() as session:
            writer_ckpt = (await session.execute(text("PRAGMA wal_autocheckpoint"))).scalar()
        async with database.read_session_local() as session:
            reader_ckpt = (await session.execute(text("PRAGMA wal_autocheckpoint"))).scalar()
        assert writer_ckpt == 0
        assert reader_ckpt == 1000

        # The loop ticks at least once without crashing.
        await asyncio.sleep(0.12)
        assert not database._checkpoint_task.done()
    finally:
        await database.close()
        reset_session_id(token)
    assert database._checkpoint_task is None


@pytest.mark.asyncio
@pytest.mark.level0
async def test_concurrent_claims_do_not_exhaust_pool(file_db: TeamDatabase) -> None:
    """More concurrent writers than pool_size must all succeed, not time out."""
    team = "team1"
    await file_db.team.create_team(team, "Team 1", "leader")
    count = 12  # > pool_size (5)
    for i in range(count):
        await file_db.task.create_task(f"task{i}", team, f"Task {i}", "content", TaskStatus.PENDING.value)

    results = await asyncio.gather(
        *[file_db.task.claim_task(f"task{i}", f"m{i}") for i in range(count)]
    )
    assert all(results)


@pytest.mark.asyncio
@pytest.mark.level0
async def test_concurrent_claim_same_task_single_winner(file_db: TeamDatabase) -> None:
    """Racing claims on ONE pending task: the CAS lets exactly one win.

    Guards the single-statement claim (WHERE assignee IS NULL AND status =
    pending): only the first caller matches the row, later callers see it no
    longer pending and get rowcount 0, so exactly one True comes back.
    """
    team = "team1"
    await file_db.team.create_team(team, "Team 1", "leader")
    await file_db.task.create_task("t1", team, "T1", "content", TaskStatus.PENDING.value)

    results = await asyncio.gather(
        *[file_db.task.claim_task("t1", f"m{i}") for i in range(10)]
    )
    assert sum(results) == 1

    task = await file_db.task.get_task("t1")
    assert task.status == TaskStatus.CLAIMED.value
    assert task.assignee is not None


@pytest.mark.asyncio
@pytest.mark.level0
async def test_concurrent_create_messages_succeed(file_db: TeamDatabase) -> None:
    """High-frequency concurrent message writes do not exhaust the pool."""
    team = "team1"
    await file_db.team.create_team(team, "Team 1", "leader")
    await file_db.member.create_member("m1", team, "M1", "{}", MemberStatus.UNSTARTED.value)
    count = 16
    results = await asyncio.gather(
        *[
            file_db.message.create_message(f"msg{i}", team, "leader", f"content{i}", to_member_name="m1")
            for i in range(count)
        ]
    )
    assert all(results)


@pytest.mark.asyncio
@pytest.mark.level1
async def test_write_lock_serializes_writes(file_db: TeamDatabase) -> None:
    """``DbSessions.write`` holds a process-wide lock: writers never overlap."""
    sessions = DbSessions(file_db.session_local)
    order: list[tuple[str, int]] = []

    async def writer(i: int) -> None:
        async with sessions.write():
            order.append(("enter", i))
            await asyncio.sleep(0.05)
            order.append(("exit", i))

    await asyncio.gather(writer(1), writer(2), writer(3))

    # Serialised: each enter is immediately followed by its own exit.
    assert len(order) == 6
    for idx in range(0, len(order), 2):
        assert order[idx][0] == "enter"
        assert order[idx + 1][0] == "exit"
        assert order[idx][1] == order[idx + 1][1]


@pytest.mark.asyncio
@pytest.mark.level1
async def test_reads_run_concurrently(file_db: TeamDatabase) -> None:
    """``DbSessions.read`` takes no write lock, so reads overlap."""
    sessions = DbSessions(file_db.session_local)
    active = 0
    peak = 0

    async def reader() -> None:
        nonlocal active, peak
        async with sessions.read():
            active += 1
            peak = max(peak, active)
            await asyncio.sleep(0.05)
            active -= 1

    await asyncio.gather(*[reader() for _ in range(3)])
    assert peak >= 2


@pytest.mark.asyncio
@pytest.mark.level0
async def test_mark_messages_read_batch_direct_and_broadcast(file_db: TeamDatabase) -> None:
    """Batch mark covers direct (is_read) and broadcast (watermark) in one call."""
    team = "team1"
    await file_db.team.create_team(team, "Team 1", "leader")
    await file_db.member.create_member("m1", team, "M1", "{}", MemberStatus.UNSTARTED.value)
    await file_db.message.create_message("d1", team, "leader", "hi1", to_member_name="m1")
    await file_db.message.create_message("d2", team, "leader", "hi2", to_member_name="m1")
    await file_db.message.create_message("b1", team, "leader", "all", broadcast=True)

    marked = await file_db.message.mark_messages_read(["d1", "d2", "b1"], "m1")
    assert marked == 3

    assert (await file_db.message.get_message("d1")).is_read is True
    assert (await file_db.message.get_message("d2")).is_read is True
    unread_broadcast = await file_db.message.get_broadcast_messages(team, "m1", unread_only=True)
    assert unread_broadcast == []


@pytest.mark.asyncio
@pytest.mark.level0
async def test_mark_messages_read_batch_multiple_broadcasts(file_db: TeamDatabase) -> None:
    """Two broadcasts in one DAO batch collapse to a single watermark row.

    Regression: the broadcast branch INSERTs the (member, team) watermark row
    on first sight. The session runs autoflush=False, so before the fix a
    second broadcast in the same transaction did not see the pending INSERT,
    re-inserted the same PK, and the commit raised an ``IntegrityError`` on
    the UNIQUE (member_name, team_name) constraint. The manager layer now
    collapses broadcasts before the DAO, but the DAO must stay safe on its
    own for direct callers — hence this test drives the DAO directly.
    """
    team = "team1"
    await file_db.team.create_team(team, "Team 1", "leader")
    await file_db.member.create_member("m1", team, "M1", "{}", MemberStatus.UNSTARTED.value)
    await file_db.message.create_message("b1", team, "leader", "all-1", broadcast=True)
    await file_db.message.create_message("b2", team, "leader", "all-2", broadcast=True)

    marked = await file_db.message.mark_messages_read(["b1", "b2"], "m1")
    assert marked == 2
    assert await file_db.message.get_broadcast_messages(team, "m1", unread_only=True) == []


@pytest.mark.asyncio
@pytest.mark.level1
async def test_mark_messages_read_skips_missing(file_db: TeamDatabase) -> None:
    """Missing ids are skipped; the count reflects only applied marks."""
    team = "team1"
    await file_db.team.create_team(team, "Team 1", "leader")
    await file_db.member.create_member("m1", team, "M1", "{}", MemberStatus.UNSTARTED.value)
    await file_db.message.create_message("d1", team, "leader", "hi", to_member_name="m1")

    marked = await file_db.message.mark_messages_read(["d1", "does-not-exist"], "m1")
    assert marked == 1
    assert (await file_db.message.get_message("d1")).is_read is True


@pytest.mark.asyncio
@pytest.mark.level1
async def test_mark_messages_read_empty_is_noop(file_db: TeamDatabase) -> None:
    """An empty id list returns zero without opening a transaction."""
    assert await file_db.message.mark_messages_read([], "m1") == 0


@pytest.mark.asyncio
@pytest.mark.level0
async def test_nested_write_no_deadlock(file_db: TeamDatabase) -> None:
    """A public write that delegates to another write must not self-deadlock.

    ``add_task_with_bidirectional_dependencies`` calls
    ``mutate_dependency_graph`` internally; only the latter opens the
    write session, so the non-reentrant lock is acquired once. A regression
    (double acquire) would hang, which ``wait_for`` surfaces as a timeout.
    """
    team = "team1"
    await file_db.team.create_team(team, "Team 1", "leader")
    await file_db.task.create_task("base", team, "Base", "content", TaskStatus.PENDING.value)

    ok = await asyncio.wait_for(
        file_db.task.add_task_with_bidirectional_dependencies(
            "child",
            team,
            "Child",
            "content",
            TaskStatus.PENDING.value,
            dependencies=["base"],
        ),
        timeout=5.0,
    )
    assert ok is True


@pytest.mark.asyncio
@pytest.mark.level1
async def test_retry_on_locked_returns_fallback_after_exhaustion(monkeypatch) -> None:
    """A persistently locked op exhausts attempts and returns the fallback."""
    import openjiuwen.agent_teams.tools.database.engine as engine_module

    monkeypatch.setattr(engine_module, "_DB_RETRY_BASE_DELAY", 0.0)
    calls = 0

    async def op() -> bool:
        nonlocal calls
        calls += 1
        raise OperationalError("stmt", {}, Exception("database is locked"))

    result = await retry_on_locked(op, on_locked_result=False, label="test_op")
    assert result is False
    assert calls == engine_module._DB_RETRY_ATTEMPTS


@pytest.mark.asyncio
@pytest.mark.level1
async def test_retry_on_locked_succeeds_first_try() -> None:
    """A succeeding op runs exactly once and returns its value."""
    calls = 0

    async def op() -> str:
        nonlocal calls
        calls += 1
        return "ok"

    result = await retry_on_locked(op, on_locked_result="fallback", label="test_op")
    assert result == "ok"
    assert calls == 1


@pytest.mark.asyncio
@pytest.mark.level1
async def test_mark_messages_read_batch(file_db: TeamDatabase) -> None:
    """The batch mark-read API marks a whole mailbox drain in one call."""
    db = file_db
    await db.team.create_team(team_name="t1", display_name="T1", leader_member_name="leader")
    await db.member.create_member(
        member_name="dev",
        team_name="t1",
        display_name="dev",
        agent_card=AgentCard().model_dump_json(),
        status="ready",
    )
    await db.message.create_message(
        message_id="d1", team_name="t1", from_member_name="leader", content="a", to_member_name="dev"
    )
    await db.message.create_message(
        message_id="b1", team_name="t1", from_member_name="leader", content="all", broadcast=True
    )

    marked = await db.message.mark_messages_read(["d1", "b1", "missing"], "dev")
    assert marked == 2
    assert await db.message.has_unread_messages("t1") is False
