#!/usr/bin/env python
# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
from unittest.mock import Mock, MagicMock, AsyncMock
import pytest
import pytest_asyncio
import logging
from enum import StrEnum

from sqlalchemy import Table, Column, String, MetaData
from sqlalchemy.ext.asyncio import AsyncEngine
from openjiuwen.core.memory.store.impl.default_db_store import DefaultDbStore
from openjiuwen.core.memory.store.sql_db_store import SqlDbStore

logger = logging.getLogger(__name__)


class ContextStoreColumnType(StrEnum):
    TEXT = 'TEXT'
    INTEGER = 'INTEGER'
    REAL = 'REAL'
    BLOB = 'BLOB'
    NUMERIC = 'NUMERIC'


CONTEXT_CONFIG = {
    'table': 'user_messages',
    'columns': {
        'user_id': ContextStoreColumnType.TEXT,
        'group_id': ContextStoreColumnType.TEXT,
        'session_id': ContextStoreColumnType.TEXT,
        'message_id': ContextStoreColumnType.TEXT,
        'role': ContextStoreColumnType.TEXT,
        'content': ContextStoreColumnType.TEXT,
        'timestamp': ContextStoreColumnType.TEXT
    }
}

data_list = [
    {
        "user_id": "u1",
        "group_id": "group1",
        "session_id": "s1",
        "message_id": "m1",
        "role": "user",
        "content": "Hello",
        "timestamp": "2025-11-19 09:00:00"
    },
    {
        "user_id": "u1",
        "group_id": "group1",
        "session_id": "s1",
        "message_id": "m2",
        "role": "user",
        "content": "World",
        "timestamp": "2025-11-19 10:00:00"
    },
    {
        "user_id": "u2",
        "group_id": "group2",
        "session_id": "s2",
        "message_id": "m3",
        "role": "assistant",
        "content": "Hi there",
        "timestamp": "2025-11-19 11:00:00"
    }
]


@pytest_asyncio.fixture
async def sql_db_store():
    # 创建模拟的数据库表
    metadata = MetaData()
    mock_table = Table(
        CONTEXT_CONFIG['table'],
        metadata,
        Column('id', String, primary_key=True),
        *[Column(col, String) for col in CONTEXT_CONFIG['columns']]
    )
    
    # 创建模拟的异步会话
    mock_result = MagicMock()
    mock_result.mappings.return_value.first.return_value = None
    mock_result.mappings.return_value.fetchall.return_value = []
    mock_result.first.return_value = None
    
    mock_session = AsyncMock()
    mock_session.execute.return_value = mock_result
    mock_session.begin.return_value.__aenter__.return_value = mock_session
    
    mock_async_sessionmaker = AsyncMock()
    mock_async_sessionmaker.return_value.__aenter__.return_value = mock_session
    
    # 创建模拟的AsyncEngine
    mock_engine = AsyncMock(spec=AsyncEngine)
    
    # 创建模拟的DefaultDbStore
    mock_db_store = Mock(spec=DefaultDbStore)
    mock_db_store.get_async_engine.return_value = mock_engine
    
    # 创建SqlDbStore实例
    store = SqlDbStore(mock_db_store)
    
    # 模拟_async_table_cache和_async_sessionmaker
    store._async_table_cache[CONTEXT_CONFIG['table']] = mock_table
    store.async_session = mock_async_sessionmaker
    
    # 模拟_get_table方法
    store._get_table = AsyncMock(return_value=mock_table)
    
    # 用于跟踪更新状态
    updated_content = {}
    
    # 设置模拟数据，用于特定测试
    def mock_condition_get_side_effect(table, conditions, columns=None):
        if 'message_id' in conditions and conditions['message_id'] == ['m1']:
            # 检查是否已更新
            if 'm1' in updated_content:
                return [{'content': updated_content['m1'], 'user_id': 'u1', 'message_id': 'm1'}]
            return [{'content': 'Hello', 'user_id': 'u1', 'message_id': 'm1'}]
        elif 'message_id' in conditions and conditions['message_id'] == ['m2']:
            # 检查是否已更新
            if 'm2' in updated_content:
                return [{'content': updated_content['m2'], 'user_id': 'u1', 'message_id': 'm2'}]
            return [{'content': 'Hello', 'user_id': 'u1', 'message_id': 'm2'}]
        elif 'message_id' in conditions and conditions['message_id'] == ['m3']:
            # 检查是否已更新
            if 'm3' in updated_content:
                return [{'content': updated_content['m3'], 'user_id': 'u2', 'message_id': 'm3'}]
            return [{'content': 'Hello', 'user_id': 'u2', 'message_id': 'm3'}]
        elif 'id' in conditions and conditions['id'] == ['m1']:
            return []
        return []
    
    def mock_exist_side_effect(table, conditions):
        if 'message_id' in conditions and conditions['message_id'] == 'm1':
            return True
        elif 'message_id' in conditions and conditions['message_id'] == 'not_exist':
            return False
        elif 'user_id' in conditions and conditions['user_id'] == 'u1' and conditions['content'] == 'Hello':
            return True
        elif 'user_id' in conditions and conditions['user_id'] == 'u1' and conditions['content'] == 'foo':
            return False
        elif 'user_id' in conditions and conditions['user_id'] == 'uX' and conditions['content'] == 'bar':
            return False
        return False
    
    def mock_get_with_sort_side_effect(table, filters, sort_by=None, order=None, limit=None):
        if filters == {}:
            return [{'message_id': 'm1', 'user_id': 'u1'},
                    {'message_id': 'm2', 'user_id': 'u1'},
                    {'message_id': 'm3', 'user_id': 'u2'}]
        elif 'user_id' in filters and filters['user_id'] == 'u1':
            return [{'message_id': 'm1', 'user_id': 'u1'}, {'message_id': 'm2', 'user_id': 'u1'}]
        elif 'user_id' in filters and filters['user_id'] == 'u2':
            return [{'message_id': 'm3', 'user_id': 'u2'}]
        return []
    
    def mock_update_side_effect(table, conditions, data):
        if 'message_id' in conditions:
            if isinstance(conditions['message_id'], list):
                for message_id in conditions['message_id']:
                    updated_content[message_id] = data['content']
            else:
                updated_content[conditions['message_id']] = data['content']
        return True
    
    # 设置方法的返回值
    store.write = AsyncMock(return_value=True)
    store.condition_get = AsyncMock(side_effect=mock_condition_get_side_effect)
    store.exist = AsyncMock(side_effect=mock_exist_side_effect)
    store.get_with_sort = AsyncMock(side_effect=mock_get_with_sort_side_effect)
    store.update = AsyncMock(side_effect=mock_update_side_effect)
    store.delete = AsyncMock(return_value=True)
    
    yield store


@pytest.mark.asyncio
async def test_get_table_columns(sql_db_store):
    """Verify that _get_table correctly retrieves the table schema."""
    table_name = CONTEXT_CONFIG["table"]
    table = await sql_db_store._get_table(table_name)

    expected_cols = list(CONTEXT_CONFIG["columns"].keys())

    # Verify that all columns exist.
    for col in expected_cols:
        assert col in table.c

    # Verify that each column type is correctly loaded.
    for col in table.c:
        assert hasattr(col.type, "python_type")


@pytest.mark.asyncio
async def test_add(sql_db_store):
    for data in data_list:
        assert await sql_db_store.write(CONTEXT_CONFIG["table"], data)


@pytest.mark.asyncio
async def test_get(sql_db_store):
    filters = {}
    filters['message_id'] = ["m1"]
    row = await sql_db_store.condition_get(
        CONTEXT_CONFIG["table"],
        conditions=filters
    )
    assert row is not None
    assert row[0]["content"] == "Hello"
    assert row[0]["user_id"] == "u1"


@pytest.mark.asyncio
async def test_get_with_sort(sql_db_store):
    """Verify that get_with_sort correctly performs query sorting."""

    table = CONTEXT_CONFIG["table"]

    rows = await sql_db_store.get_with_sort(
        table=table,
        filters={"user_id": "u1"},
        sort_by="timestamp",
        order="ASC",
        limit=10
    )
    assert len(rows) == 2
    assert rows[0]["message_id"] == "m1"  # 最旧
    assert rows[1]["message_id"] == "m2"  # 最新

    # user_id = u2
    rows_u2 = await sql_db_store.get_with_sort(
        table=table,
        filters={"user_id": "u2"},
        sort_by="timestamp",
        order="ASC",
        limit=10
    )

    assert len(rows_u2) >= 1
    assert rows_u2[0]["message_id"] == "m3"


@pytest.mark.asyncio
async def test_exist(sql_db_store):
    all_data = await sql_db_store.get_with_sort(
        table=CONTEXT_CONFIG["table"], filters={})
    logger.info(f"all_data: {all_data}")
    assert len(all_data) > 0
    assert await sql_db_store.exist(CONTEXT_CONFIG["table"], {"message_id": "m1"})
    assert not await sql_db_store.exist(CONTEXT_CONFIG["table"], {"message_id": "not_exist"})
    assert await sql_db_store.exist(CONTEXT_CONFIG["table"], {"user_id": "u1", "content": "Hello"})
    assert not await sql_db_store.exist(CONTEXT_CONFIG["table"], {"user_id": "u1", "content": "foo"})
    assert not await sql_db_store.exist(CONTEXT_CONFIG["table"], {"user_id": "uX", "content": "bar"})


@pytest.mark.asyncio
async def test_update(sql_db_store):
    ok = await sql_db_store.update(CONTEXT_CONFIG["table"], {"message_id": "m1"}, {"content": "hi"})
    assert ok
    row = await sql_db_store.condition_get(CONTEXT_CONFIG["table"], {"message_id": ["m1"]})
    assert row[0]["content"] == "hi"
    ok = await sql_db_store.update(CONTEXT_CONFIG["table"], {"message_id": ["m2", "m3"]}, {"content": "batch"})
    assert ok
    row2 = await sql_db_store.condition_get(CONTEXT_CONFIG["table"], {"message_id": ["m2"]})
    row3 = await sql_db_store.condition_get(CONTEXT_CONFIG["table"], {"message_id": ["m3"]})
    assert row2[0]["content"] == "batch"
    assert row3[0]["content"] == "batch"


@pytest.mark.asyncio
async def test_delete(sql_db_store):
    ok = await sql_db_store.delete(CONTEXT_CONFIG["table"], {"message_id": "m1"})
    assert ok
    row = await sql_db_store.condition_get(CONTEXT_CONFIG["table"], {"id": ["m1"]})
    assert row == []
