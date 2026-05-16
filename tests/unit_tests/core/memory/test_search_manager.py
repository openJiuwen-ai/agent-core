#!/usr/bin/env python
# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import pytest
from typing import Any
from openjiuwen.core.memory.manage.search.search_manager import SearchManager
from openjiuwen.core.memory.manage.index.variable_manager import VariableManager
from openjiuwen.core.foundation.store.kv.in_memory_kv_store import InMemoryKVStore
from openjiuwen.core.foundation.store.base_memory_index import BaseMemoryIndex, MemoryDoc
from openjiuwen.core.memory.manage.mem_model.memory_unit import MemoryType


class MockMemoryIndex(BaseMemoryIndex):

    def __init__(self):
        self._data: dict[str, dict[str, dict[str, MemoryDoc]]] = {}

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

    async def list_memories(self, user_id: str, scope_id: str, offset: int, limit: int,
                            mem_types: list[str]) -> list[MemoryDoc]:
        if user_id not in self._data or scope_id not in self._data[user_id]:
            return []
        docs = sorted(self._data[user_id][scope_id].values(), key=lambda d: d.timestamp, reverse=True)
        return docs[offset:offset + limit]

    async def cleanup_backup(self, backup_id: str) -> None:
        pass

    async def list_user_scopes(self) -> list[tuple[str, str]]:
        scopes = []
        for uid, scope_dict in self._data.items():
            for sid in scope_dict.keys():
                scopes.append((uid, sid))
        return scopes


class TestSearchManager:
    @pytest.mark.asyncio
    async def test_get_user_variable_with_empty_var_name(self):
        # Create necessary dependencies
        mock_kv_store = InMemoryKVStore()
        # Use encryption key of correct length (32 bytes)
        crypto_key = b"test_key_32_bytes_long_enough_12"
        mock_memory_index = MockMemoryIndex()

        # Create VariableManager instance
        variable_manager = VariableManager(mock_kv_store, crypto_key)

        # Create SearchManager instance
        managers = {
            MemoryType.VARIABLE.value: variable_manager
        }
        search_manager = SearchManager(managers, crypto_key, mock_memory_index)

        user_id = "test_user_id"
        scope_id = "test_scope_id"
        empty_var_name = ""

        # Test if get_user_variable returns None without error when var_name is empty string
        result = await search_manager.get_user_variable(user_id, scope_id, empty_var_name)

        # Verify result
        assert result is None

    @pytest.mark.asyncio
    async def test_get_user_variable_with_whitespace_var_name(self):
        # Create necessary dependencies
        mock_kv_store = InMemoryKVStore()
        # Use encryption key of correct length (32 bytes)
        crypto_key = b"test_key_32_bytes_long_enough_12"
        mock_memory_index = MockMemoryIndex()

        # Create VariableManager instance
        variable_manager = VariableManager(mock_kv_store, crypto_key)

        # Create SearchManager instance
        managers = {
            MemoryType.VARIABLE.value: variable_manager
        }
        search_manager = SearchManager(managers, crypto_key, mock_memory_index)

        user_id = "test_user_id"
        scope_id = "test_scope_id"
        whitespace_var_name = "   "

        result = await search_manager.get_user_variable(user_id, scope_id, whitespace_var_name)

        assert result is None

    @pytest.mark.asyncio
    async def test_get_user_variable_with_valid_var_name(self):
        mock_kv_store = InMemoryKVStore()
        # Use encryption key of correct length (32 bytes)
        crypto_key = b"test_key_32_bytes_long_enough_12"
        mock_memory_index = MockMemoryIndex()

        # Create VariableManager instance
        variable_manager = VariableManager(mock_kv_store, crypto_key)

        managers = {
            MemoryType.VARIABLE.value: variable_manager
        }
        search_manager = SearchManager(managers, crypto_key, mock_memory_index)

        user_id = "test_user_id"
        scope_id = "test_scope_id"
        valid_var_name = "test_variable"
        var_value = "test_value"

        # First add a variable
        from openjiuwen.core.memory.manage.mem_model.memory_unit import VariableUnit
        variable_unit = VariableUnit(
            variable_name=valid_var_name,
            variable_mem=var_value
        )
        await variable_manager.add_memories(user_id, scope_id, {MemoryType.VARIABLE.value: [variable_unit]})

        # Test if get_user_variable returns correct value when var_name is valid string
        result = await search_manager.get_user_variable(user_id, scope_id, valid_var_name)

        # Verify result
        assert result == var_value

    @pytest.mark.asyncio
    async def test_long_term_memory_get_variables_with_empty_string_name(self):
        # Create necessary dependencies
        mock_kv_store = InMemoryKVStore()
        # Use encryption key of correct length (32 bytes)
        crypto_key = b"test_key_32_bytes_long_enough_12"
        mock_memory_index = MockMemoryIndex()

        # Create VariableManager instance
        variable_manager = VariableManager(mock_kv_store, crypto_key)
        # Create SearchManager instance
        managers = {
            MemoryType.VARIABLE.value: variable_manager
        }
        search_manager = SearchManager(managers, crypto_key, mock_memory_index)

        user_id = "test_user_id"
        scope_id = "test_scope_id"
        valid_var_name = "test_variable"
        var_value = "test_value"

        # First add a variable
        from openjiuwen.core.memory.manage.mem_model.memory_unit import VariableUnit
        variable_unit = VariableUnit(
            variable_name=valid_var_name,
            variable_mem=var_value
        )
        await variable_manager.add_memories(user_id, scope_id, {MemoryType.VARIABLE.value: [variable_unit]})

        # Create LongTermMemory instance and manually set necessary properties
        from openjiuwen.core.memory.long_term_memory import LongTermMemory
        long_term_memory = LongTermMemory()
        long_term_memory.search_manager = search_manager
        long_term_memory.variable_manager = variable_manager

        # Test if get_variables can execute successfully when names is empty string
        result = await long_term_memory.get_variables(names="", user_id=user_id, scope_id=scope_id)

        # Verify result
        assert isinstance(result, dict)  # Ensure return type is dictionary
        # Since empty string is treated as a single variable name,
        # but VariableManager.query_variable returns all variables.
        # The returned dictionary should contain empty string as key with None value,
        # because no variable is named empty string.
        assert "" in result
        assert result[""] is None
