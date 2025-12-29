#!/usr/bin/env python
# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
import shutil
import os
import pytest
from openjiuwen.core.memory.store.impl.dbm_kv_store import DbmKVStore


@pytest.fixture(scope="function")
def dbm_store():
    # Setup
    test_dir = "test_dbm"
    os.makedirs(test_dir, exist_ok=True)
    db_path = os.path.join(test_dir, "testdb")
    store = DbmKVStore(db_path)

    yield store

    # Teardown
    shutil.rmtree(test_dir, ignore_errors=True)


class TestDBMStore:
    @pytest.mark.asyncio
    async def test_set_and_get(self, dbm_store):
        await dbm_store.set("a", "123")
        assert await dbm_store.get("a") == "123"

    @pytest.mark.asyncio
    async def test_exists(self, dbm_store):
        await dbm_store.set("b", "hello")
        assert await dbm_store.exists("b")
        assert not await dbm_store.exists("xxx")

    @pytest.mark.asyncio
    async def test_delete(self, dbm_store):
        await dbm_store.set("c", "delme")
        assert await dbm_store.exists("c")
        await dbm_store.delete("c")
        assert not await dbm_store.exists("c")
        assert await dbm_store.get("c") is None

    @pytest.mark.asyncio
    async def test_mget(self, dbm_store):
        await dbm_store.set("k1", "v1")
        await dbm_store.set("k2", "v2")
        res = await dbm_store.mget(["k1", "k2", "k3"])
        assert res == ["v1", "v2", None]

    @pytest.mark.asyncio
    async def test_db_files_created(self, dbm_store):
        await dbm_store.set("a", "1")
        test_dir = "test_dbm"
        files = os.listdir(test_dir)
        assert len(files) > 0, "dbm files should be created"

    @pytest.mark.asyncio
    async def test_get_by_prefix(self, dbm_store):
        await dbm_store.set("session_summary\x1Fuser1+.*\x1Fapp1\x1Fsession1", "mock_value")
        await dbm_store.set("session_summary\x1Fuser1\x1Fapp2\x1Fsession2", "mock_value")
        await dbm_store.set("session_summary\x1Fuser1\x1Fapp1\x1Fsession3", "mock_value")

        res = await dbm_store.get_by_prefix("session_summary\x1Fuser1")
        assert res == {
            "session_summary\x1Fuser1+.*\x1Fapp1\x1Fsession1": "mock_value",
            "session_summary\x1Fuser1\x1Fapp2\x1Fsession2": "mock_value",
            "session_summary\x1Fuser1\x1Fapp1\x1Fsession3": "mock_value"
        }

    @pytest.mark.asyncio
    async def test_delete_by_prefix(self, dbm_store):
        await dbm_store.set("session_summary\x1Fuser1+.*\x1Fapp1\x1Fsession1", "mock_value")
        await dbm_store.set("session_summary\x1Fuser1+\x1Fapp2\x1Fsession2", "mock_value")
        await dbm_store.set("session_summary\x1Fuser1\x1Fapp1\x1Fsession3", "mock_value")
        await dbm_store.set("session_summary\x1Fuser2\x1Fapp1\x1Fsession4", "mock_value")

        await dbm_store.delete_by_prefix("session_summary\x1Fuser1")
        assert not await dbm_store.exists("session_summary\x1Fuser1+.*\x1Fapp1\x1Fsession1")
        assert not await dbm_store.exists("session_summary\x1Fuser1+\x1Fapp2\x1Fsession2")
        assert not await dbm_store.exists("session_summary\x1Fuser1\x1Fapp1\x1Fsession3")
        assert await dbm_store.exists("session_summary\x1Fuser2\x1Fapp1\x1Fsession4")
