#!/usr/bin/env python
# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
import asyncio
import uuid
import logging
from pathlib import Path
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy.ext.asyncio import create_async_engine

from openjiuwen.core.foundation.store.db.default_db_store import DefaultDbStore
from openjiuwen.core.memory.manage.mem_model.sql_db_store import SqlDbStore
from openjiuwen.core.memory.manage.mem_model.db_model import create_tables
from openjiuwen.core.memory.manage.mem_model.sql_message_store import SqlMessageStore
from openjiuwen.core.memory.manage.mem_model.message_manager import MessageManager, MessageAddRequest
from openjiuwen.core.foundation.llm.schema.message import UserMessage, AssistantMessage, SystemMessage
from openjiuwen.core.common.exception.errors import ExecutionError
from openjiuwen.core.memory.long_term_memory import LongTermMemory
from openjiuwen.core.memory.config.config import MemoryEngineConfig, AgentMemoryConfig
from openjiuwen.core.foundation.store.kv.in_memory_kv_store import InMemoryKVStore
from openjiuwen.core.foundation.store.base_vector_store import BaseVectorStore

logger = logging.getLogger(__name__)


@ pytest.fixture(name="sql_db_store")
def sql_db_store_fixture():
    utc_now = datetime.now(timezone.utc)
    time_str = utc_now.strftime("%Y%m%d%H%M%S")
    uuid_str = uuid.uuid4().hex[:6]
    path = Path(f"./test_message_store_{time_str}_{uuid_str}.db").resolve()

    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
    db_store = DefaultDbStore(engine)
    asyncio.run(create_tables(db_store))
    sql_store = SqlDbStore(db_store)

    yield sql_store

    # teardown
    asyncio.run(engine.dispose())
    if path.exists():
        path.unlink()


@ pytest.fixture(name="crypto_key")
def crypto_key_fixture():
    return b"test_crypto_key_1234560000000000"


@ pytest.fixture(name="sql_message_store")
def sql_message_store_fixture(sql_db_store, crypto_key):
    return SqlMessageStore(crypto_key=crypto_key, sql_db_store=sql_db_store)


@ pytest.fixture(name="message_manager")
def message_manager_fixture(sql_db_store, crypto_key):
    sql_message_store = SqlMessageStore(
        crypto_key=crypto_key,
        sql_db_store=sql_db_store
    )
    return MessageManager(store=sql_message_store)


class TestSqlMessageStore:

    @ pytest.mark.asyncio
    async def test_add_message(self, sql_message_store):
        message = UserMessage(content="Hello, world!")
        message_add = {
            'message': message,
            'user_id': "user1",
            'scope_id': "scope1",
            'session_id': "session1"
        }
        message_id = await sql_message_store.add_message(message_add)
        assert message_id is not None
        assert message_id.startswith("msg_")

    @ pytest.mark.asyncio
    async def test_add_messages(self, sql_message_store):
        message_adds = [
            {
                'message': UserMessage(content="Hello"),
                'user_id': "user1", 'scope_id': "scope1", 'session_id': "session1",
            },
            {
                'message': AssistantMessage(content="Hi there!"),
                'user_id': "user1", 'scope_id': "scope1", 'session_id': "session1",
            },
            {
                'message': SystemMessage(content="System prompt"),
                'user_id': "user1", 'scope_id': "scope1", 'session_id': "session1",
            },
        ]

        message_ids = await sql_message_store.add_messages(message_adds)
        
        assert len(message_ids) == 3
        for message_id in message_ids:
            assert message_id.startswith("msg_")

    @ pytest.mark.asyncio
    async def test_get_message_by_id(self, sql_message_store):
        message = UserMessage(content="Test message")
        message_add = {
            'message': message,
            'user_id': "user1",
            'scope_id': "scope1",
            'session_id': "session1"
        }
        message_id = await sql_message_store.add_message(message_add)
        retrieved_message, metadata = await sql_message_store.get_message_by_id(message_id)
        
        assert retrieved_message is not None
        assert metadata is not None
        assert retrieved_message.content == "Test message"
        assert metadata.message_id == message_id
        assert metadata.user_id == "user1"
        assert metadata.scope_id == "scope1"
        assert metadata.session_id == "session1"

    @ pytest.mark.asyncio
    async def test_get_messages(self, sql_message_store):
        message_adds = [
            {'message': UserMessage(content="Message 1"), 'user_id': "user1", 'scope_id': "scope1", 'session_id': "session1"},
            {'message': AssistantMessage(content="Response 1"), 'user_id': "user1", 'scope_id': "scope1", 'session_id': "session1"},
            {'message': UserMessage(content="Message 2"), 'user_id': "user1", 'scope_id': "scope1", 'session_id': "session1"},
            {'message': AssistantMessage(content="Response 2"), 'user_id': "user1", 'scope_id': "scope1", 'session_id': "session1"},
        ]

        await sql_message_store.add_messages(message_adds)
        
        message_filter = {
            'user_id': "user1",
            'scope_id': "scope1",
            'session_id': "session1",
        }

        messages_with_metadata = await sql_message_store.get_messages(
            message_filter, limit=10, order_direction="asc"
        )
        
        assert len(messages_with_metadata) == 4

        assert messages_with_metadata[0][0].content == "Message 1"
        assert messages_with_metadata[1][0].content == "Response 1"
        assert messages_with_metadata[2][0].content == "Message 2"
        assert messages_with_metadata[3][0].content == "Response 2"

    @ pytest.mark.asyncio
    async def test_update_message(self, sql_message_store):
        message = UserMessage(content="Original content")
        message_add = {
            'message': message,
            'user_id': "user1",
            'scope_id': "scope1",
            'session_id': "session1"
        }
        message_id = await sql_message_store.add_message(message_add)
        
        success = await sql_message_store.update_message(message_id, "Updated content")
        
        assert success
        
        retrieved_message, _ = await sql_message_store.get_message_by_id(message_id)
        assert retrieved_message.content == "Updated content"

    @ pytest.mark.asyncio
    async def test_delete_message(self, sql_message_store):
        message = UserMessage(content="Message to delete")
        message_add = {
            'message': message,
            'user_id': "user1",
            'scope_id': "scope1",
            'session_id': "session1"
        }
        message_id = await sql_message_store.add_message(message_add)
        
        success = await sql_message_store.delete_message_by_id(message_id)
        
        assert success
        
        try:
            await sql_message_store.get_message_by_id(message_id)
            assert False, "Message should have been deleted"
        except ExecutionError:
            pass


class TestMessageManagerWithStore:

    @ pytest.mark.asyncio
    async def test_add_message(self, message_manager):
        req = MessageAddRequest(
            user_id="user1",
            scope_id="scope1",
            content="Hello from MessageManager",
            role="user",
            session_id="session1"
        )
        
        message_id = await message_manager.add(req)
        
        assert message_id is not None

    @ pytest.mark.asyncio
    async def test_get_messages(self, message_manager):
        for i in range(3):
            req = MessageAddRequest(
                user_id="user1",
                scope_id="scope1",
                content=f"Message {i+1}",
                role="user" if i % 2 == 0 else "assistant",
                session_id="session1"
            )
            await message_manager.add(req)
        
        messages = await message_manager.get(
            user_id="user1",
            scope_id="scope1",
            session_id="session1",
            message_len=10
        )
        
        assert len(messages) == 3
        
        assert messages[0][0].content == "Message 1"
        assert messages[1][0].content == "Message 2"
        assert messages[2][0].content == "Message 3"

    @ pytest.mark.asyncio
    async def test_get_message_by_id(self, message_manager):
        req = MessageAddRequest(
            user_id="user1",
            scope_id="scope1",
            content="Test message",
            role="user",
            session_id="session1"
        )
        message_id = await message_manager.add(req)
        
        message, timestamp = await message_manager.get_by_id(message_id)
        
        assert message is not None
        assert timestamp is not None
        assert message.content == "Test message"

    @ pytest.mark.asyncio
    async def test_delete_messages(self, message_manager):
        req = MessageAddRequest(
            user_id="user1",
            scope_id="scope1",
            content="Message to delete",
            role="user",
            session_id="session1"
        )
        await message_manager.add(req)
        
        success = await message_manager.delete_by_user_and_scope(
            user_id="user1",
            scope_id="scope1"
        )
        
        assert success
        
        messages = await message_manager.get(
            user_id="user1",
            scope_id="scope1",
            session_id="session1"
        )
        assert len(messages) == 0

class _FakeVectorStore(BaseVectorStore):
    """Minimal mock vector store that satisfies LongTermMemory.register_store."""

    async def create_collection(self, *a, **kw):
        pass

    async def delete_collection(self, *a, **kw):
        pass

    async def get_schema(self, *a, **kw):
        return {}

    async def add_docs(self, *a, **kw):
        return []

    async def search(self, *a, **kw):
        return []

    async def delete_docs_by_ids(self, *a, **kw):
        pass

    async def delete_docs_by_filters(self, *a, **kw):
        pass

    async def collection_exists(self, *a, **kw):
        return False

    async def list_collection_names(self, *a, **kw):
        return []

    async def get_collection_metadata(self, *a, **kw):
        return {}

    async def update_collection_metadata(self, *a, **kw):
        pass

    async def update_schema(self, *a, **kw):
        pass


@pytest.fixture(name="long_term_memory")
def long_term_memory_fixture(sql_db_store):
    # Reset singleton state for test isolation
    LongTermMemory._instances.pop(LongTermMemory, None)
    engine = LongTermMemory()

    kv_store = InMemoryKVStore()
    vector_store = _FakeVectorStore()

    crypto_key = b"test_crypto_key_1234560000000000"

    async def _setup():
        await engine.register_store(
            kv_store=kv_store,
            vector_store=vector_store,
            db_store=sql_db_store.db_store,
        )
        engine.set_config(MemoryEngineConfig(crypto_key=crypto_key))

    asyncio.run(_setup())
    yield engine

    # cleanup singleton
    LongTermMemory._instances.pop(LongTermMemory, None)


class TestLongTermMemoryMessageStore:

    @pytest.mark.asyncio
    @patch.object(LongTermMemory, "_get_scope_llm", new_callable=AsyncMock)
    async def test_add_messages_and_get_recent(self, mock_get_llm, long_term_memory):
        mock_get_llm.return_value = AsyncMock()
        agent_config = AgentMemoryConfig()
        messages = [
            UserMessage(content="Hello from LTM test"),
            AssistantMessage(content="Hi from LTM test"),
        ]
        result = await long_term_memory.add_messages(
            messages=messages,
            agent_config=agent_config,
            user_id="user_ltm",
            scope_id="scope_ltm",
            session_id="session_ltm",
            gen_mem=False,
        )
        assert result is not None

        recent = await long_term_memory.get_recent_messages(
            user_id="user_ltm",
            scope_id="scope_ltm",
            session_id="session_ltm",
            num=10,
        )
        assert len(recent) == 2
        assert recent[0].content == "Hello from LTM test"
        assert recent[1].content == "Hi from LTM test"

    @pytest.mark.asyncio
    @patch.object(LongTermMemory, "_get_scope_llm", new_callable=AsyncMock)
    async def test_get_message_by_id(self, mock_get_llm, long_term_memory):
        mock_get_llm.return_value = AsyncMock()
        agent_config = AgentMemoryConfig()
        messages = [UserMessage(content="Find me by ID")]
        await long_term_memory.add_messages(
            messages=messages,
            agent_config=agent_config,
            user_id="user_ltm2",
            scope_id="scope_ltm2",
            session_id="session_ltm2",
            gen_mem=False,
        )

        # Use underlying SqlMessageStore to retrieve the message_id from metadata
        msg_tuples = await long_term_memory.message_store.get_messages(
            {'user_id': "user_ltm2", 'scope_id': "scope_ltm2", 'session_id': "session_ltm2"},
            limit=1,
        )
        msg_id = msg_tuples[0][1].message_id

        retrieved = await long_term_memory.get_message_by_id(msg_id)
        assert retrieved is not None
        msg, ts = retrieved
        assert msg.content == "Find me by ID"
        assert ts is not None

    @pytest.mark.asyncio
    @patch.object(LongTermMemory, "_get_scope_llm", new_callable=AsyncMock)
    async def test_delete_messages(self, mock_get_llm, long_term_memory):
        mock_get_llm.return_value = AsyncMock()
        agent_config = AgentMemoryConfig()
        messages = [UserMessage(content="To be deleted")]
        await long_term_memory.add_messages(
            messages=messages,
            agent_config=agent_config,
            user_id="user_ltm3",
            scope_id="scope_ltm3",
            session_id="session_ltm3",
            gen_mem=False,
        )

        await long_term_memory.delete_messages_by_user_and_scope(
            user_id="user_ltm3",
            scope_id="scope_ltm3",
        )

        recent = await long_term_memory.get_recent_messages(
            user_id="user_ltm3",
            scope_id="scope_ltm3",
            session_id="session_ltm3",
        )
        assert len(recent) == 0


if __name__ == "__main__":
    pytest.main([__file__])
