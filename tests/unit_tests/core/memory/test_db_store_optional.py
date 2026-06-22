# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from typing import Any

import pytest

from openjiuwen.core.foundation.store.base_kv_store import BaseKVStore
from openjiuwen.core.foundation.store.kv.in_memory_kv_store import InMemoryKVStore
from openjiuwen.core.foundation.store.base_db_store import BaseDbStore
from openjiuwen.core.foundation.store.base_vector_store import BaseVectorStore, CollectionSchema
from openjiuwen.core.foundation.store.base_message_store import BaseMessageStore
from openjiuwen.core.memory.long_term_memory import LongTermMemory
from openjiuwen.core.memory.config.config import MemoryEngineConfig, MemoryScopeConfig, AgentMemoryConfig
from openjiuwen.core.memory.manage.mem_model.scope_user_mapping_manager import KvScopeUserMappingManager
from openjiuwen.core.common.exception.errors import BaseError
from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.foundation.llm import BaseMessage, UserMessage, AssistantMessage
from openjiuwen.core.common.logging import memory_logger


class MockVectorStore(BaseVectorStore):
    def __init__(self):
        self._collections = {}
        self._metadata = {}

    async def create_collection(self, collection_name: str, schema, **kwargs):
        self._collections[collection_name] = schema

    async def delete_collection(self, collection_name: str, **kwargs):
        self._collections.pop(collection_name, None)

    async def collection_exists(self, collection_name: str, **kwargs) -> bool:
        return collection_name in self._collections

    async def get_schema(self, collection_name: str, **kwargs) -> CollectionSchema:
        return CollectionSchema()

    async def add_docs(self, collection_name: str, docs, **kwargs):
        pass

    async def search(self, collection_name: str, query_vector, vector_field, top_k=5, filters=None, **kwargs):
        return []

    async def delete_docs_by_ids(self, collection_name: str, ids, **kwargs):
        pass

    async def delete_docs_by_filters(self, collection_name: str, filters, **kwargs):
        pass

    async def list_collection_names(self):
        return list(self._collections.keys())

    async def update_schema(self, collection_name: str, operations):
        pass

    async def get_collection_schema_version(self, collection_name: str, **kwargs) -> int | None:
        return None

    async def update_collection_schema_version(self, collection_name: str, schema_version: int, **kwargs):
        pass

    async def update_collection_metadata(self, collection_name: str, metadata: dict) -> None:
        self._metadata[collection_name] = metadata

    async def get_collection_metadata(self, collection_name: str) -> dict:
        return self._metadata.get(collection_name, {"schema_version": 0})


class MockMessageStore(BaseMessageStore):
    def __init__(self):
        self._messages: list[dict] = []
        self._next_id = 0

    async def add_message(self, message_dict: dict) -> str:
        self._next_id += 1
        msg_id = str(self._next_id)
        message_dict["msg_id"] = msg_id
        self._messages.append(message_dict)
        return msg_id

    async def add_messages(self, message_dicts: list) -> list[str]:
        return [await self.add_message(m) for m in message_dicts]

    async def get_messages(self, filters: dict, limit: int = 10, order_direction: str = "desc") -> list:
        result = []
        for msg in self._messages:
            match = True
            for k, v in filters.items():
                if v is not None and msg.get(k) != v:
                    match = False
                    break
            if match:
                result.append((msg["message"], type("Metadata", (), {"timestamp": msg.get("timestamp")})()))
        return result[-limit:]

    async def get_message_by_id(self, msg_id: str):
        for msg in self._messages:
            if msg.get("msg_id") == msg_id:
                return msg["message"], type("Metadata", (), {"timestamp": msg.get("timestamp")})()
        raise ValueError(f"Message {msg_id} not found")

    async def delete_messages(self, filters: dict) -> int:
        count_before = len(self._messages)
        self._messages = [
            msg for msg in self._messages
            if not all(v is None or msg.get(k) == v for k, v in filters.items())
        ]
        return count_before - len(self._messages)

    async def delete_message_by_id(self, msg_id: str) -> bool:
        for i, msg in enumerate(self._messages):
            if msg.get("msg_id") == msg_id:
                self._messages.pop(i)
                return True
        return False

    async def count_messages(self, filters: dict) -> int:
        return len(self._messages)

    async def update_message(self, msg_id: str, updates: dict) -> bool:
        for msg in self._messages:
            if msg.get("msg_id") == msg_id:
                msg.update(updates)
                return True
        return False

    async def get_schema_version(self) -> int | None:
        return None

    async def set_schema_version(self, version: int):
        pass


@pytest.fixture(scope="function", autouse=True)
def reset_singleton():
    LongTermMemory._instances.pop(LongTermMemory, None)
    yield


@pytest.mark.asyncio
async def test_register_store_without_db_store_success():
    """方案1验证: register_store(kv, vector) 不传 db_store 可正常初始化"""
    kv_store = InMemoryKVStore()
    vector_store = MockVectorStore()
    ltm = LongTermMemory()
    await ltm.register_store(kv_store=kv_store, vector_store=vector_store)
    assert ltm.kv_store is not None
    assert ltm.vector_store is not None
    assert ltm.db_store is None
    assert ltm.memory_index is not None


@pytest.mark.asyncio
async def test_register_store_without_db_store_and_vector_fails():
    """没有 db_store 且没有 vector_store 时 memory_index 缺失应报错"""
    kv_store = InMemoryKVStore()
    ltm = LongTermMemory()
    with pytest.raises(BaseError) as exc:
        await ltm.register_store(kv_store=kv_store)
    assert exc.value.status == StatusCode.MEMORY_SET_CONFIG_EXECUTION_ERROR


@pytest.mark.asyncio
async def test_scope_user_mapping_kv_backend_used_when_no_db():
    """方案2验证: db_store 缺失时自动使用 KvScopeUserMappingManager"""
    kv_store = InMemoryKVStore()
    vector_store = MockVectorStore()
    ltm = LongTermMemory()
    await ltm.register_store(kv_store=kv_store, vector_store=vector_store)
    assert ltm.scope_user_mapping_manager is not None
    assert isinstance(ltm.scope_user_mapping_manager, KvScopeUserMappingManager)


@pytest.mark.asyncio
async def test_scope_user_mapping_kv_add_and_get_by_scope():
    """KvScopeUserMappingManager 的 add/get_by_scope 验证"""
    kv_store = InMemoryKVStore()
    manager = KvScopeUserMappingManager(kv_store)
    await manager.add(user_id="user1", scope_id="scope1")
    await manager.add(user_id="user2", scope_id="scope1")
    await manager.add(user_id="user3", scope_id="scope2")
    result1 = await manager.get_by_scope_id("scope1")
    assert result1 is not None
    user_ids = {item["user_id"] for item in result1}
    assert user_ids == {"user1", "user2"}
    result2 = await manager.get_by_scope_id("scope2")
    assert result2 is not None
    assert len(result2) == 1
    assert result2[0]["user_id"] == "user3"
    result3 = await manager.get_by_scope_id("nonexistent")
    assert result3 is None


@pytest.mark.asyncio
async def test_scope_user_mapping_kv_add_idempotent():
    """KvScopeUserMappingManager.add 幂等性验证"""
    kv_store = InMemoryKVStore()
    manager = KvScopeUserMappingManager(kv_store)
    await manager.add(user_id="user1", scope_id="scope1")
    await manager.add(user_id="user1", scope_id="scope1")
    result = await manager.get_by_scope_id("scope1")
    assert result is not None
    assert len(result) == 1


@pytest.mark.asyncio
async def test_scope_user_mapping_kv_delete_by_scope():
    """KvScopeUserMappingManager.delete_by_scope_id 验证"""
    kv_store = InMemoryKVStore()
    manager = KvScopeUserMappingManager(kv_store)
    await manager.add(user_id="user1", scope_id="scope1")
    await manager.add(user_id="user2", scope_id="scope1")
    await manager.add(user_id="user3", scope_id="scope2")
    await manager.delete_by_scope_id("scope1")
    result1 = await manager.get_by_scope_id("scope1")
    assert result1 is None
    result2 = await manager.get_by_scope_id("scope2")
    assert result2 is not None
    assert len(result2) == 1


@pytest.mark.asyncio
async def test_delete_mem_by_scope_without_db_store():
    """方案3: db_store 缺失时 delete_mem_by_scope 正常工作的完整链路"""
    kv_store = InMemoryKVStore()
    vector_store = MockVectorStore()
    ltm = LongTermMemory()
    await ltm.register_store(kv_store=kv_store, vector_store=vector_store)
    await ltm.scope_user_mapping_manager.add(user_id="user_a", scope_id="scope_del")
    result = await ltm.delete_mem_by_scope(scope_id="scope_del")
    assert result is True
    remaining = await ltm.scope_user_mapping_manager.get_by_scope_id("scope_del")
    assert remaining is None


@pytest.mark.asyncio
async def test_delete_mem_by_scope_with_empty_mapping():
    """scope_user_mapping 为空时 delete_mem_by_scope 正常返回"""
    kv_store = InMemoryKVStore()
    vector_store = MockVectorStore()
    ltm = LongTermMemory()
    await ltm.register_store(kv_store=kv_store, vector_store=vector_store)
    result = await ltm.delete_mem_by_scope(scope_id="empty_scope")
    assert result is True


@pytest.mark.asyncio
async def test_add_messages_without_message_manager():
    """方案4: message_manager 为 None 时 add_messages 跳过 message 写入，不报错"""
    kv_store = InMemoryKVStore()
    vector_store = MockVectorStore()
    ltm = LongTermMemory()
    await ltm.register_store(kv_store=kv_store, vector_store=vector_store)
    assert ltm.message_manager is None

    fake_llm = AsyncMock()
    fake_llm.invoke = AsyncMock(return_value=AsyncMock())
    fake_llm.invoke.return_value.content = "{}"
    ltm._base_llm = fake_llm

    agent_config = AgentMemoryConfig(enable_long_term_mem=False)
    messages = [UserMessage(content="hello")]
    result = await ltm.add_messages(
        messages=messages,
        agent_config=agent_config,
        user_id="test_user",
        scope_id="test_scope",
        gen_mem=False,
    )
    assert result is not None


@pytest.mark.asyncio
async def test_add_messages_with_external_message_store():
    """外部 message_store 传入时，message_manager 正常初始化和工作"""
    kv_store = InMemoryKVStore()
    vector_store = MockVectorStore()
    ext_msg_store = MockMessageStore()

    ltm = LongTermMemory()
    await ltm.register_store(
        kv_store=kv_store,
        vector_store=vector_store,
        message_store=ext_msg_store,
    )
    assert ltm.message_manager is not None

    fake_llm = AsyncMock()
    fake_llm.invoke = AsyncMock(return_value=AsyncMock())
    fake_llm.invoke.return_value.content = "{}"
    ltm._base_llm = fake_llm

    agent_config = AgentMemoryConfig(enable_long_term_mem=False)
    messages = [UserMessage(content="hi"), AssistantMessage(content="hello")]
    result = await ltm.add_messages(
        messages=messages,
        agent_config=agent_config,
        user_id="u1",
        scope_id="s1",
        gen_mem=False,
    )
    assert result is not None

    recent = await ltm.get_recent_messages(user_id="u1", scope_id="s1", num=5)
    assert len(recent) == 2


@pytest.mark.asyncio
async def test_get_recent_messages_without_message_manager():
    """方案4: message_manager 为 None 时 get_recent_messages 返回空列表"""
    kv_store = InMemoryKVStore()
    vector_store = MockVectorStore()
    ltm = LongTermMemory()
    await ltm.register_store(kv_store=kv_store, vector_store=vector_store)
    result = await ltm.get_recent_messages(user_id="u1", scope_id="s1")
    assert result == []


@pytest.mark.asyncio
async def test_delete_messages_without_message_manager():
    """方案4: message_manager 为 None 时 delete_messages_by_user_and_scope 不报错"""
    kv_store = InMemoryKVStore()
    vector_store = MockVectorStore()
    ltm = LongTermMemory()
    await ltm.register_store(kv_store=kv_store, vector_store=vector_store)
    await ltm.delete_messages_by_user_and_scope(user_id="u1", scope_id="s1")


@pytest.mark.asyncio
async def test_get_message_by_id_without_message_manager():
    """message_manager 为 None 时 get_message_by_id 抛错"""
    kv_store = InMemoryKVStore()
    vector_store = MockVectorStore()
    ltm = LongTermMemory()
    await ltm.register_store(kv_store=kv_store, vector_store=vector_store)
    with pytest.raises(BaseError) as exc:
        await ltm.get_message_by_id("some_id")
    assert exc.value.status == StatusCode.MEMORY_GET_MEMORY_EXECUTION_ERROR


@pytest.mark.asyncio
async def test_get_history_messages_without_message_manager():
    """_get_history_messages 在 message_manager 为 None 时返回空列表"""
    kv_store = InMemoryKVStore()
    vector_store = MockVectorStore()
    ltm = LongTermMemory()
    await ltm.register_store(kv_store=kv_store, vector_store=vector_store)
    result = await ltm._get_history_messages(
        user_id="u1", scope_id="s1", session_id="", history_window_size=2
    )
    assert result == []


@pytest.mark.asyncio
async def test_register_store_with_only_kv_fails_due_to_missing_index():
    """只有 kv_store 不传其他任何 store 时报错（memory_index 缺失）"""
    kv_store = InMemoryKVStore()
    ltm = LongTermMemory()
    with pytest.raises(BaseError) as exc:
        await ltm.register_store(kv_store=kv_store)
    assert exc.value.status == StatusCode.MEMORY_SET_CONFIG_EXECUTION_ERROR


@pytest.mark.asyncio
async def test_register_store_with_optional_db_still_works():
    """传入 db_store 的原始流程仍然正常工作"""
    kv_store = InMemoryKVStore()
    vector_store = MockVectorStore()
    db_store = MagicMock(spec=BaseDbStore)
    db_store.execute_sql = AsyncMock()
    db_store.execute_sql_batch = AsyncMock()
    mock_engine = MagicMock()
    mock_engine.begin = MagicMock()
    mock_engine.begin.return_value.__aenter__ = AsyncMock()
    mock_engine.begin.return_value.__aexit__ = AsyncMock(return_value=None)
    db_store.get_async_engine = MagicMock(return_value=mock_engine)

    ltm = LongTermMemory()
    await ltm.register_store(kv_store=kv_store, vector_store=vector_store, db_store=db_store)
    assert ltm.db_store is not None
    assert ltm.scope_user_mapping_manager is not None
    assert not isinstance(ltm.scope_user_mapping_manager, KvScopeUserMappingManager)