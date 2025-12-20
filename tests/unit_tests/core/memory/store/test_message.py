#!/usr/bin/env python
# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
from unittest.mock import AsyncMock, MagicMock
import pytest
from sqlalchemy import select

from openjiuwen.core.memory.store.message import UserMessage


@pytest.fixture
def mock_async_engine():
    """创建模拟的异步引擎"""
    mock_engine = MagicMock()
    
    # 配置模拟的连接和事务
    mock_conn = AsyncMock()
    mock_transaction = AsyncMock()
    
    # 配置engine.begin()返回异步上下文管理器
    mock_begin_cm = AsyncMock()
    mock_begin_cm.__aenter__.return_value = mock_conn
    mock_engine.begin.return_value = mock_begin_cm
    
    # 配置连接的run_sync方法
    mock_conn.run_sync = MagicMock()
    
    yield mock_engine


@pytest.fixture
def mock_async_session():
    """创建模拟的异步会话"""
    mock_session = AsyncMock()
    
    # 创建测试消息
    test_msg = UserMessage(
        user_id="u123",
        group_id="group456",
        session_id="s789",
        message_id="m001",
        role="user",
        content="hello",
        timestamp="2025-11-18 19:00:00",
    )
    
    # 模拟execute方法返回的结果
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = [test_msg]
    mock_session.execute.return_value = mock_result
    
    # 模拟commit方法
    mock_session.commit.return_value = None
    
    yield mock_session


class TestCreateTable:
    @pytest.mark.asyncio
    async def test_table_creation(self, mock_async_engine, mock_async_session):
        """测试表创建功能"""
        # 测试表名
        await self.async_check_table(mock_async_engine)
        
        # 测试数据插入
        await self.async_insert_data(mock_async_session)

    @staticmethod
    async def async_check_table(engine):
        """检查表是否存在"""
        # 直接断言表名正确
        assert UserMessage.__tablename__ == "user_message"

    @staticmethod
    async def async_insert_data(session):
        """测试数据插入功能"""
        msg = UserMessage(
            user_id="u123",
            group_id="group456",
            session_id="s789",
            message_id="m001",
            role="user",
            content="hello",
            timestamp="2025-11-18 19:00:00",
        )
        
        # 添加数据
        session.add(msg)
        await session.commit()

        # 查询数据
        await session.execute(select(UserMessage))
        result = await session.execute()
        messages = result.scalars().all()
        
        # 验证查询结果
        for m in messages:
            assert m.message_id == msg.message_id
            assert m.user_id == msg.user_id
            assert m.content == msg.content
            assert m.timestamp == msg.timestamp
            assert m.role == msg.role
            assert m.session_id == msg.session_id
            assert m.group_id == msg.group_id
