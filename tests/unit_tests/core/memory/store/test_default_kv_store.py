# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
import os
import shutil

import pytest
from sqlalchemy.ext.asyncio import create_async_engine

from openjiuwen.core.memory.store.impl.default_kv_store import DefaultKVStore


@pytest.fixture(name="kv_store")
def get_default_kv_store():
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    resource_dir = os.path.join(project_root, "resources")
    os.makedirs(resource_dir, exist_ok=True)
    kv_path = os.path.join(resource_dir, "kv_store.db")
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{kv_path}",
        pool_pre_ping=True,
        echo=False,
    )
    kv_store = DefaultKVStore(engine)
    yield kv_store

    shutil.rmtree(resource_dir)


class TestDefaultKVStore:
    @pytest.mark.asyncio
    @pytest.mark.skip(reason="need aiosqlite lib")
    async def test_default_kv_store(self, kv_store):
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
        assert await kv_store.get_by_prefix("key3") == {'key3': 'value3', 'key345': 'value345', 'key3456': 'value3456'}
        await kv_store.delete_by_prefix("key3")
        assert await kv_store.get_by_prefix("key3") == {}
        assert await kv_store.mget(["key4", "key53245", "key1"]) == ['value4', None, 'update_value1']
