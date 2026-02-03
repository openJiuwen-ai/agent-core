# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
import asyncio
import os
import shutil

import pytest
from sqlalchemy.ext.asyncio import create_async_engine

from openjiuwen.core.foundation.store.db_based_kv_store import DbBasedKVStore


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

    if os.path.exists(resource_dir):
        shutil.rmtree(resource_dir)


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


class TestDefaultKVStore:
    @pytest.mark.asyncio
    @pytest.mark.skip(reason="need aiosqlite lib")
    async def test_kv_store(self, sqlite_kv_store, mysql_kv_store):
        async def test_default_kv_store(kv_store):
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

        await test_default_kv_store(sqlite_kv_store)
        await test_default_kv_store(mysql_kv_store)