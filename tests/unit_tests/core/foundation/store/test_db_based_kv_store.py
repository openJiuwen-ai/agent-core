# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import asyncio
import os
import shutil
from types import SimpleNamespace
from unittest import mock

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.dialects import mysql, postgresql, sqlite
from sqlalchemy.ext.asyncio import create_async_engine

from openjiuwen.core.foundation.store.kv.db_based_kv_store import DbBasedKVStore, KVStoreTable


@pytest.fixture(name="sqlite_kv_store")
def get_sqlite_kv_store():
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    resource_dir = os.path.join(project_root, "resources")
    os.makedirs(resource_dir, exist_ok=True)
    kv_path = os.path.join(resource_dir, "kv_store.db")
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{kv_path}",
        pool_pre_ping=True,
        echo=False,
    )
    kv_store = DbBasedKVStore(engine)
    yield kv_store

    try:
        asyncio.run(engine.dispose())
    except RuntimeError:
        pass
    if os.path.exists(resource_dir):
        try:
            shutil.rmtree(resource_dir)
        except PermissionError:
            pass  # Windows: file still in use by another process
        except OSError as e:
            if getattr(e, "winerror", None) != 32:
                raise


@pytest.fixture(name="mysql_kv_store")
def get_mysql_kv_store():
    db_user = os.getenv("DB_USER", "xxxx")
    db_passport = os.getenv("DB_PASSWORD", "xxxx")
    db_host = os.getenv("DB_HOST", "xxxx")
    db_port = os.getenv("DB_PORT", "xxxx")
    agent_db_name = os.getenv("AGENT_DB_NAME", "xxxx")
    engine = create_async_engine(
        f"mysql+aiomysql://{db_user}:{db_passport}@{db_host}:{db_port}/{agent_db_name}?charset=utf8mb4",
        pool_size=20,
        max_overflow=20
    )
    kv_store = DbBasedKVStore(engine)
    yield kv_store

    engine.dispose()


async def run_default_kv_store(kv_store):
    await kv_store.set("key1", "value1")
    assert await kv_store.get("key1") == "value1"
    await kv_store.set("key1", "update_value1")
    assert await kv_store.get("key1") == "update_value1"
    assert not await kv_store.exclusive_set("key1", "update_value2")
    assert await kv_store.get("key1") == "update_value1"

    await kv_store.set("key2", "value2")
    await kv_store.set("key3", "value3")
    await kv_store.set("key345", "value345")
    await kv_store.set("key3456", "value3456")
    await kv_store.set("key4", "value4")

    assert await kv_store.get("key2") == "value2"
    await kv_store.delete("key2")
    assert not await kv_store.exists("key2")
    assert (await kv_store.get_by_prefix("key3") ==
            {'key3': 'value3', 'key345': 'value345', 'key3456': 'value3456'})
    await kv_store.delete_by_prefix("key3")
    assert await kv_store.get_by_prefix("key3") == {}
    assert await kv_store.mget(["key4", "key53245", "key1"]) == ['value4', None, 'update_value1']

    assert await kv_store.exclusive_set("exclusive_key", "exclusive_value", 1)
    value = await kv_store.get("exclusive_key")
    assert value == "exclusive_value"
    assert not await kv_store.exclusive_set("exclusive_key", "update_exclusive_value", 1)
    await asyncio.sleep(1)
    assert await kv_store.exclusive_set("exclusive_key", "update_exclusive_value", 1)
    value = await kv_store.get("exclusive_key")
    assert value == "update_exclusive_value"

    await kv_store.set("key56", "10")
    value = await kv_store.get("key56")
    assert value == "10"


@pytest.mark.asyncio
async def test_sqlite_kv_store(sqlite_kv_store):
    await run_default_kv_store(sqlite_kv_store)


@pytest.mark.skip(reason="no mysql environment")
@pytest.mark.asyncio
async def test_mysql_kv_store(mysql_kv_store):
    await run_default_kv_store(mysql_kv_store)


# The upsert and the value column follow the database dialect. SQLite ignores
# VARCHAR lengths and accepts any insert construct, so the SQLite tests above
# cannot catch a PostgreSQL or MySQL regression; these tests compile for each
# dialect without needing a database or its driver.

_DIALECTS = {
    "postgresql": (postgresql.dialect(), ("ON CONFLICT", "DO UPDATE"), "TEXT"),
    "mysql": (mysql.dialect(), ("ON DUPLICATE KEY UPDATE",), "LONGTEXT"),
    "sqlite": (sqlite.dialect(), ("ON CONFLICT", "DO UPDATE"), "TEXT"),
}


def _store_for(dialect):
    return DbBasedKVStore(SimpleNamespace(dialect=dialect))


@pytest.mark.parametrize("name", sorted(_DIALECTS))
def test_upsert_statement_compiles_for_dialect(name):
    dialect, clauses, _ = _DIALECTS[name]
    sql = str(_store_for(dialect)._get_upsert_stmt("key", "value").compile(dialect=dialect))
    assert all(clause in sql for clause in clauses), sql


@pytest.mark.parametrize("name", sorted(_DIALECTS))
def test_value_column_is_not_length_limited(name):
    dialect, _, column_type = _DIALECTS[name]
    assert KVStoreTable.__table__.c.value.type.compile(dialect=dialect) == column_type


_INSERT_IF_ABSENT = {
    "postgresql": ("ON CONFLICT", "DO NOTHING"),
    "mysql": ("INSERT IGNORE INTO",),
    "sqlite": ("ON CONFLICT", "DO NOTHING"),
}


@pytest.mark.parametrize("name", sorted(_DIALECTS))
def test_exclusive_insert_statement_never_overwrites_for_dialect(name):
    dialect = _DIALECTS[name][0]
    sql = str(_store_for(dialect)._get_insert_if_absent_stmt("key", "value").compile(dialect=dialect))
    assert all(clause in sql for clause in _INSERT_IF_ABSENT[name]), sql
    assert "UPDATE" not in sql, sql


class PauseBeforeWrite:
    """Hold one store's first insert until the test releases it.

    Wraps the store's session factory, so the pause sits at the database boundary
    and the store's own logic runs unchanged on both sides of it. SQLite takes
    its write lock at the first insert, so this is the last point where another
    writer can still run.
    """

    def __init__(self, store: DbBasedKVStore) -> None:
        self._factory = store.async_session
        self.reached = asyncio.Event()
        self.proceed = asyncio.Event()
        store.async_session = self

    def __call__(self):
        session = self._factory()
        execute = session.execute

        async def paused(statement, *args, **kwargs):
            if getattr(statement, "is_insert", False) and not self.reached.is_set():
                self.reached.set()
                await self.proceed.wait()
            return await execute(statement, *args, **kwargs)

        session.execute = paused
        return session

    async def wait_reached(self, timeout: float = 5.0) -> None:
        await asyncio.wait_for(self.reached.wait(), timeout)


@pytest_asyncio.fixture(name="two_sqlite_stores")
async def get_two_sqlite_stores(tmp_path):
    """Two stores, each with its own engine, over one SQLite file."""
    url = f"sqlite+aiosqlite:///{tmp_path / 'kv.db'}"
    engines = [create_async_engine(url), create_async_engine(url)]
    stores = [DbBasedKVStore(engine) for engine in engines]
    await stores[0]._create_table_if_not_exist()
    yield stores
    for engine in engines:
        await engine.dispose()


@pytest.mark.asyncio
async def test_concurrent_exclusive_set_admits_exactly_one_writer(two_sqlite_stores):
    first, second = two_sqlite_stores
    pause = PauseBeforeWrite(second)
    late = asyncio.create_task(second.exclusive_set("owner", "second"))
    await pause.wait_reached()
    assert await first.exclusive_set("owner", "first")
    pause.proceed.set()
    assert not await late
    assert await first.get("owner") == "first"


@pytest.mark.asyncio
async def test_expired_exclusive_entry_is_taken_over_once(two_sqlite_stores):
    first, second = two_sqlite_stores
    assert await first.exclusive_set("owner", "stale", 1)
    await asyncio.sleep(1.1)
    pause = PauseBeforeWrite(second)
    late = asyncio.create_task(second.exclusive_set("owner", "second"))
    await pause.wait_reached()
    assert await first.exclusive_set("owner", "first")
    pause.proceed.set()
    assert not await late
    assert await first.get("owner") == "first"


@pytest.mark.asyncio
async def test_exclusive_set_refuses_an_existing_plain_value(sqlite_kv_store):
    await sqlite_kv_store.set("plain", "10")
    assert not await sqlite_kv_store.exclusive_set("plain", "taken")
    assert await sqlite_kv_store.get("plain") == "10"


@pytest.mark.asyncio
async def test_pipeline_set_uses_the_dialect_upsert(sqlite_kv_store):
    with mock.patch.object(sqlite_kv_store, "_get_upsert_stmt",
                           wraps=sqlite_kv_store._get_upsert_stmt) as upsert:
        pipeline = sqlite_kv_store.pipeline()
        await pipeline.set("pipeline_key", "pipeline_value")
        await pipeline.execute()
    upsert.assert_called_once()
    assert await sqlite_kv_store.get("pipeline_key") == "pipeline_value"


# A checkpoint-sized value round trip, on a real database when one is given:
# KV_STORE_TEST_URLS="postgresql+asyncpg://...;mysql+aiomysql://..."

_LARGE_VALUE = "x" * 100_000
_TEST_URLS = [url for url in os.getenv("KV_STORE_TEST_URLS", "").split(";") if url]


async def _large_value_round_trip(kv_store):
    await kv_store.set("large", _LARGE_VALUE)
    assert await kv_store.get("large") == _LARGE_VALUE
    await kv_store.set("large", _LARGE_VALUE + "y")
    assert await kv_store.get("large") == _LARGE_VALUE + "y"
    pipeline = kv_store.pipeline()
    await pipeline.set("piped", _LARGE_VALUE)
    await pipeline.execute()
    assert await kv_store.get("piped") == _LARGE_VALUE


@pytest.mark.asyncio
async def test_sqlite_large_value(sqlite_kv_store):
    await _large_value_round_trip(sqlite_kv_store)


@pytest.mark.skipif(not _TEST_URLS, reason="set KV_STORE_TEST_URLS to run against real databases")
@pytest.mark.parametrize("url", _TEST_URLS or ["unused"])
@pytest.mark.asyncio
async def test_database_kv_store(url):
    engine = create_async_engine(url)
    try:
        async with engine.begin() as connection:
            await connection.execute(text("DROP TABLE IF EXISTS kv_store"))
        kv_store = DbBasedKVStore(engine)
        await run_default_kv_store(kv_store)
        await _large_value_round_trip(kv_store)
    finally:
        await engine.dispose()
