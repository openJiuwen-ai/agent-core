#!/usr/bin/env python
# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
import shutil
import struct
import secrets
import time
import asyncio
import unittest
import os
from datetime import datetime, timezone
from openjiuwen.core.memory.store.impl.dbm_kv_store import DbmKVStore
from openjiuwen.core.memory.store.user_mem_store import UserMemStore


class TestUserMemStore(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.test_dir = "test_dbm"
        os.makedirs(self.test_dir, exist_ok=True)
        db_path = os.path.join(self.test_dir, "testdb")
        self.kv_store = DbmKVStore(db_path)
        self.store = UserMemStore(kv_store_instance=self.kv_store)

    async def asyncTearDown(self):
        shutil.rmtree(self.test_dir)

    @staticmethod
    def _generate_next_id(user_id) -> str:
        t = int(time.time() * 1000) & 0xFFFFFFFFFFFF
        r = secrets.token_bytes(3)
        h = hash(user_id) & 0xFFFFFF
        t_bytes = struct.pack(">Q", t)[2:]
        h_bytes = struct.pack(">I", h)[1:]
        raw = t_bytes + r + h_bytes
        return raw.hex()

    async def basic(self):
        user_profile_mem_type = "user_profile"
        episodic_mem_type = "episodic_mem"

        # Test write and get
        user_id1 = "user1"
        group_id1 = "group1"
        mem_id1 = TestUserMemStore._generate_next_id(user_id1)
        profile_type1 = "personal_information"
        data1 = {
            "id": mem_id1,
            "user_id": user_id1,
            "group_id": group_id1,
            "profile_type": profile_type1,
            "profile_mem": "user profile1",
            "mem_type": user_profile_mem_type,
            "time": str(datetime.now(tz=timezone.utc)),
        }
        self.assertTrue(await self.store.write(user_id1, group_id1, mem_id1, data1))
        user_profile_data1 = await self.store.get(user_id1, group_id1, mem_id1)
        self.assertEqual(user_profile_data1, data1)

        mem_id2 = TestUserMemStore._generate_next_id(user_id1)
        data2 = {
            "id": mem_id2,
            "user_id": user_id1,
            "group_id": group_id1,
            "content": "episodic memory 1",
            "mem_type": episodic_mem_type,
            "time": str(datetime.now(tz=timezone.utc)),
        }
        self.assertTrue(await self.store.write(user_id1, group_id1, mem_id2, data2))

        user_id2 = "user2"
        group_id2 = "group2"
        mem_id3 = TestUserMemStore._generate_next_id(user_id1)
        data3 = {
            "id": mem_id3,
            "user_id": user_id2,
            "group_id": group_id2,
            "content": "episodic memory 2",
            "mem_type": episodic_mem_type,
            "time": str(datetime.now(tz=timezone.utc)),
        }
        self.assertTrue(await self.store.write(user_id2, group_id2, mem_id3, data3))

        # Test update and get
        modify_user_profile_mem = "user profile 2"
        self.assertTrue(await self.store.update(user_id1, group_id1, mem_id1, {"profile_mem": modify_user_profile_mem}))
        user_profile_update_data1 = await self.store.get(user_id1, group_id1, mem_id1)
        self.assertEqual(user_profile_update_data1.get("profile_mem"), modify_user_profile_mem)

        # Test get_all
        all_user1_data_list = await self.store.get_all(user_id1, group_id1)
        self.assertEqual(len(all_user1_data_list), 2)
        # get all user profile data
        all_user1_profile_list = await self.store.get_all(user_id1, group_id1, user_profile_mem_type)
        self.assertEqual(len(all_user1_profile_list), 1)
        # get all episodic mem data
        all_user1_episodic_list = await self.store.get_all(user_id1, group_id1, episodic_mem_type)
        self.assertEqual(len(all_user1_episodic_list), 1)

        all_user2_data_list = await self.store.get_all(user_id2, group_id2)
        self.assertEqual(len(all_user2_data_list), 1)

        # Test batch_get
        batch_get_user1_data_list = await self.store.batch_get(user_id1, group_id1, [mem_id1, mem_id2])
        self.assertEqual(len(batch_get_user1_data_list), 2)
        self.assertNotEqual(batch_get_user1_data_list[0], None)
        self.assertNotEqual(batch_get_user1_data_list[1], None)

        # Test get_by_topic
        topic_data = await self.store.get_by_topic(user_id1, group_id1, profile_type1)
        self.assertEqual(len(topic_data), 1)

        # Test get_in_range
        range_data = await self.store.get_in_range(user_id1, group_id1, 0, 1)
        self.assertEqual(len(range_data), 1)
        range_data = await self.store.get_in_range(user_id1, group_id1, -1, 2)
        self.assertEqual(len(range_data), 2)

        # Test delete and batch_delete
        all_user1_mem_ids = [data["id"] for data in all_user1_data_list]
        await self.store.batch_delete(user_id1, group_id1, all_user1_mem_ids)
        await self.store.delete(user_id2, group_id2, mem_id3)
        self.assertEqual(await self.store.get_all(user_id1, group_id1), None)
        self.assertEqual(await self.store.get_all(user_id2, group_id2), None)

    def test_basic(self):
        asyncio.run(self.basic())

if __name__ == "__main__":
    unittest.main()

