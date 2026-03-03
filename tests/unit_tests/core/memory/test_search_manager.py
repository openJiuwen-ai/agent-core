#!/usr/bin/env python
# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import pytest
from openjiuwen.core.memory.manage.search.search_manager import SearchManager
from openjiuwen.core.memory.manage.index.variable_manager import VariableManager
from openjiuwen.core.foundation.store.kv.in_memory_kv_store import InMemoryKVStore
from openjiuwen.core.memory.manage.mem_model.memory_unit import MemoryType
from openjiuwen.core.memory.manage.mem_model.user_mem_store import UserMemStore


class TestSearchManager:
    @pytest.mark.asyncio
    async def test_get_user_variable_with_empty_var_name(self):
        # Create necessary dependencies
        mock_kv_store = InMemoryKVStore()
        # Use encryption key of correct length (32 bytes)
        crypto_key = b"test_key_32_bytes_long_enough_12"
        
        # Create VariableManager instance
        variable_manager = VariableManager(mock_kv_store, crypto_key)
        
        # Create UserMemStore instance
        user_mem_store = UserMemStore(mock_kv_store)
        
        # Create SearchManager instance
        managers = {
            MemoryType.VARIABLE.value: variable_manager
        }
        search_manager = SearchManager(managers, user_mem_store, crypto_key)
        
        # Test data
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
        
        # Create VariableManager instance
        variable_manager = VariableManager(mock_kv_store, crypto_key)
        
        # Create UserMemStore instance
        user_mem_store = UserMemStore(mock_kv_store)
        
        # Create SearchManager instance
        managers = {
            MemoryType.VARIABLE.value: variable_manager
        }
        search_manager = SearchManager(managers, user_mem_store, crypto_key)
        
        # Test data
        user_id = "test_user_id"
        scope_id = "test_scope_id"
        whitespace_var_name = "   "
        
        # Test if get_user_variable returns None without error when var_name is whitespace string
        result = await search_manager.get_user_variable(user_id, scope_id, whitespace_var_name)
        
        # Verify result
        assert result is None
    
    @pytest.mark.asyncio
    async def test_get_user_variable_with_valid_var_name(self):
        # Create necessary dependencies
        mock_kv_store = InMemoryKVStore()
        # Use encryption key of correct length (32 bytes)
        crypto_key = b"test_key_32_bytes_long_enough_12"
        
        # Create VariableManager instance
        variable_manager = VariableManager(mock_kv_store, crypto_key)
        
        # Create UserMemStore instance
        user_mem_store = UserMemStore(mock_kv_store)
        
        # Create SearchManager instance
        managers = {
            MemoryType.VARIABLE.value: variable_manager
        }
        search_manager = SearchManager(managers, user_mem_store, crypto_key)
        
        # Test data
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
        await variable_manager.add_memories(user_id, scope_id, [variable_unit])
        
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
        
        # Create VariableManager instance
        variable_manager = VariableManager(mock_kv_store, crypto_key)
        
        # Create UserMemStore instance
        user_mem_store = UserMemStore(mock_kv_store)
        
        # Create SearchManager instance
        managers = {
            MemoryType.VARIABLE.value: variable_manager
        }
        search_manager = SearchManager(managers, user_mem_store, crypto_key)
        
        # Test data
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
        await variable_manager.add_memories(user_id, scope_id, [variable_unit])
        
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
