#!/usr/bin/env python
# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
import os
from datetime import datetime, timezone
from typing import Any

import pytest

os.environ['HF_ENDPOINT'] = "https://hf-mirror.com"
from openjiuwen.core.memory.manage.index.fragment_memory_manager import FragmentMemoryManager
from openjiuwen.core.memory.manage.index.variable_manager import VariableManager
from openjiuwen.core.memory.manage.index.write_manager import WriteManager
from openjiuwen.core.memory.manage.mem_model.memory_unit import FragmentMemoryUnit, \
    VariableUnit, MemoryType
from openjiuwen.core.foundation.store.base_memory_index import BaseMemoryIndex, MemoryDoc
from openjiuwen.core.foundation.store.kv.in_memory_kv_store import InMemoryKVStore


class MockMemoryIndex(BaseMemoryIndex):
    """In-memory mock implementation of BaseMemoryIndex for testing."""

    def __init__(self):
        self._data: dict[str, dict[str, dict[str, MemoryDoc]]] = {}
        self._schema_version = 0
        self._backups: dict[str, dict[str, Any]] = {}

    def _ensure_user_scope(self, user_id: str, scope_id: str):
        if user_id not in self._data:
            self._data[user_id] = {}
        if scope_id not in self._data[user_id]:
            self._data[user_id][scope_id] = {}

    async def add_memories(self, user_id: str, scope_id: str, memories: list[MemoryDoc]):
        self._ensure_user_scope(user_id, scope_id)
        for doc in memories:
            self._data[user_id][scope_id][doc.id] = doc

    async def update_memories(self, user_id: str, scope_id: str, memories: list[MemoryDoc]):
        """Update memories by deleting old ones then adding new ones."""
        if not memories:
            return
        ids = [m.id for m in memories]
        await self.delete_memories(user_id, scope_id, ids)
        await self.add_memories(user_id, scope_id, memories)

    async def delete_memories(self, user_id: str, scope_id: str, ids: list[str]):
        if user_id in self._data and scope_id in self._data[user_id]:
            for mid in ids:
                self._data[user_id][scope_id].pop(mid, None)

    async def delete_by_user(self, user_id: str):
        self._data.pop(user_id, None)

    async def delete_by_scope(self, scope_id: str):
        for uid in list(self._data.keys()):
            self._data[uid].pop(scope_id, None)

    async def delete_by_user_and_scope(self, user_id: str, scope_id: str):
        if user_id in self._data:
            self._data[user_id].pop(scope_id, None)

    async def search(self, user_id: str, scope_id: str, query: str,
                     mem_types: list[str] | None = None, top_k: int = 10) -> list[tuple[MemoryDoc, float]]:
        if user_id not in self._data or scope_id not in self._data[user_id]:
            return []
        results = []
        for doc in self._data[user_id][scope_id].values():
            if mem_types and doc.type not in mem_types:
                continue
            score = 1.0 if query in doc.text else 0.5
            results.append((doc, score))
        results.sort(key=lambda x: x[1], reverse=True)
        return results[:top_k]

    async def get_by_id(self, user_id: str, scope_id: str, mem_id: str) -> MemoryDoc | None:
        if user_id in self._data and scope_id in self._data[user_id]:
            return self._data[user_id][scope_id].get(mem_id)
        return None

    async def list_memories(self, user_id: str, scope_id: str, offset: int = 0, limit: int = 100, mem_types: list[str] | None = None) -> list[MemoryDoc]:
        if user_id not in self._data or scope_id not in self._data[user_id]:
            return []
        docs = sorted(self._data[user_id][scope_id].values(), key=lambda d: d.timestamp, reverse=True)
        if mem_types:
            docs = [d for d in docs if d.type in mem_types]
        return docs[offset:offset + limit]

    def get_schema_version(self) -> int:
        return self._schema_version

    def update_schema_version(self, version: int) -> None:
        self._schema_version = version

    async def create_backup(self) -> str:
        import uuid
        bid = str(uuid.uuid4())
        self._backups[bid] = {"schema_version": self._schema_version}
        return bid

    async def restore_backup(self, backup_id: str) -> None:
        if backup_id not in self._backups:
            raise ValueError(f"Backup {backup_id} not found")
        self._schema_version = self._backups[backup_id]["schema_version"]

    async def cleanup_backup(self, backup_id: str) -> None:
        self._backups.pop(backup_id, None)

    async def list_user_scopes(self) -> list[tuple[str, str]]:
        scopes = []
        for uid, scope_dict in self._data.items():
            for sid in scope_dict.keys():
                scopes.append((uid, sid))
        return scopes


class TestManage:
    @pytest.mark.asyncio
    async def test_basic(self):
        mock_kv_store = InMemoryKVStore()
        mock_memory_index = MockMemoryIndex()

        user_profile_manager = FragmentMemoryManager(
            memory_index=mock_memory_index,
            crypto_key=b""
        )
        variable_manager = VariableManager(mock_kv_store, b"")
        managers = {MemoryType.USER_PROFILE.value: user_profile_manager, MemoryType.VARIABLE.value: variable_manager}
        write_manager = WriteManager(managers, mock_memory_index)
        test_all_data = [
            {"mem_id": "1000", "mem_type": MemoryType.USER_PROFILE, "content": "用户非常喜欢川菜，尤其是水煮鱼和麻婆豆腐"},
            {"mem_id": "1001", "mem_type": MemoryType.USER_PROFILE, "content": "用户的职业是软件工程师，居住在北京市"},
            {"mem_id": "1002", "mem_type": MemoryType.USER_PROFILE, "content": "用户的副业是抖音直播"},
            {"mem_id": "1003", "mem_type": MemoryType.USER_PROFILE, "content": "用户的银行账户余额为10000元"},
            {"mem_id": "1004", "mem_type": MemoryType.USER_PROFILE, "content": "用户的朋友圈中有50个好友"},
            {"mem_id": "1005", "mem_type": MemoryType.USER_PROFILE, "content": "用户的宠物是一只金毛犬"},
        ]
        test_all_data1 = [
            {"mem_id": "019e0ad3b5acb22c931f1010", "mem_type": MemoryType.USER_PROFILE,
             "content": "用户喜欢打篮球和阅读历史小说"},
            {"mem_id": "019e0ad3b5acb22c931f1011", "mem_type": MemoryType.USER_PROFILE,
             "content": "用户的生日是1990年1月1日"},
            {"mem_id": "019e0ad3b5acb22c931f1012", "mem_type": MemoryType.USER_PROFILE,
             "content": "用户的汽车型号是特斯拉Model 3"},
            {"mem_id": "019e0ad3b5acb22c931f1013", "mem_type": MemoryType.USER_PROFILE,
             "content": "用户在Twitter上有200个关注者"},
        ]

        for item in test_all_data:
            mem_unit = FragmentMemoryUnit(**item)
            await write_manager.add_memories("usrZH2025", "fitnesstrackerv3",
                                             {mem_unit.mem_type.value: [mem_unit]}, None)
            mem_unit = VariableUnit(variable_name=item['mem_type'],
                                    variable_mem=item['content'])
            await write_manager.add_memories("usrZH2025", "fitnesstrackerv3",
                                             {mem_unit.mem_type.value: [mem_unit]}, None)

        for item in test_all_data1:
            mem_unit = FragmentMemoryUnit(**item)
            await write_manager.add_memories("usrZH2026", "fitnesstrackerv3",
                                             {mem_unit.mem_type.value: [mem_unit]}, None)
            mem_unit = VariableUnit(variable_name=item['mem_type'],
                                    variable_mem=item['content'])
            await write_manager.add_memories("usrZH2026", "fitnesstrackerv3",
                                             {mem_unit.mem_type.value: [mem_unit]}, None)

        query = "用户的职业"
        res = await user_profile_manager.search("usrZH2025", "fitnesstrackerv3", query, 5)
        assert len(res) == 5

        await user_profile_manager.update("usrZH2025", "fitnesstrackerv3", res[0]['id'],
                                          "用户不是软件工程师，是系统")
        ret = await user_profile_manager.get("usrZH2025", "fitnesstrackerv3", res[0]['id'])
        assert ret['mem'] == "用户不是软件工程师，是系统"

        res = await user_profile_manager.list_fragment_memories("usrZH2025", "fitnesstrackerv3", 0, 10)
        assert len(res) == 6
        for rr in res[0:2]:
            await write_manager.delete_mem_by_id("usrZH2025", "fitnesstrackerv3", rr["id"])

        res = await user_profile_manager.search("usrZH2025", "fitnesstrackerv3", query, 5)
        assert len(res) == 4
        await write_manager.delete_mem_by_user_id("usrZH2026", "fitnesstrackerv3")
        res = await user_profile_manager.search("usrZH2026", "fitnesstrackerv3", query, 5)
        assert len(res) == 0
