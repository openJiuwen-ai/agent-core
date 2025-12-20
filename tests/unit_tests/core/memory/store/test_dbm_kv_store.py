#!/usr/bin/env python
# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
import shutil
import os
import unittest
from openjiuwen.core.memory.store.impl.dbm_kv_store import DbmKVStore


class TestDBMStore(unittest.IsolatedAsyncioTestCase):

    async def asyncSetUp(self):
        self.test_dir = "test_dbm"
        os.makedirs(self.test_dir, exist_ok=True)
        self.db_path = os.path.join(self.test_dir, "testdb")
        self.store = DbmKVStore(self.db_path)

    async def asyncTearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    async def test_set_and_get(self):
        await self.store.set("a", "123")
        self.assertEqual(await self.store.get("a"), "123")

    async def test_exists(self):
        await self.store.set("b", "hello")
        self.assertTrue(await self.store.exists("b"))
        self.assertFalse(await self.store.exists("xxx"))

    async def test_delete(self):
        await self.store.set("c", "delme")
        self.assertTrue(await self.store.exists("c"))
        await self.store.delete("c")
        self.assertFalse(await self.store.exists("c"))
        self.assertIsNone(await self.store.get("c"))

    async def test_mget(self):
        await self.store.set("k1", "v1")
        await self.store.set("k2", "v2")
        res = await self.store.mget(["k1", "k2", "k3"])
        self.assertEqual(res, ["v1", "v2", None])

    async def test_db_files_created(self):
        await self.store.set("a", "1")
        files = os.listdir(self.test_dir)
        self.assertTrue(len(files) > 0, "dbm files should be created")

    async def test_get_by_prefix(self):
        await self.store.set("session_summary\x1Fuser1+.*\x1Fapp1\x1Fsession1", "mock_value")
        await self.store.set("session_summary\x1Fuser1\x1Fapp2\x1Fsession2", "mock_value")
        await self.store.set("session_summary\x1Fuser1\x1Fapp1\x1Fsession3", "mock_value")

        res = await self.store.get_by_prefix("session_summary\x1Fuser1")
        self.assertEqual(res, {
            "session_summary\x1Fuser1+.*\x1Fapp1\x1Fsession1": "mock_value",
            "session_summary\x1Fuser1\x1Fapp2\x1Fsession2": "mock_value",
            "session_summary\x1Fuser1\x1Fapp1\x1Fsession3": "mock_value"
        })

    async def test_delete_by_prefix(self):
        await self.store.set("session_summary\x1Fuser1+.*\x1Fapp1\x1Fsession1", "mock_value")
        await self.store.set("session_summary\x1Fuser1+\x1Fapp2\x1Fsession2", "mock_value")
        await self.store.set("session_summary\x1Fuser1\x1Fapp1\x1Fsession3", "mock_value")
        await self.store.set("session_summary\x1Fuser2\x1Fapp1\x1Fsession4", "mock_value")

        await self.store.delete_by_prefix("session_summary\x1Fuser1")
        self.assertFalse(await self.store.exists("session_summary\x1Fuser1+.*\x1Fapp1\x1Fsession1"))
        self.assertFalse(await self.store.exists("session_summary\x1Fuser1+\x1Fapp2\x1Fsession2"))
        self.assertFalse(await self.store.exists("session_summary\x1Fuser1\x1Fapp1\x1Fsession3"))
        self.assertTrue(await self.store.exists("session_summary\x1Fuser2\x1Fapp1\x1Fsession4"))


if __name__ == "__main__":
    unittest.main()
