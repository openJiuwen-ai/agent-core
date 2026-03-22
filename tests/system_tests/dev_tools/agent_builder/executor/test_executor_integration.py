# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""
System tests for agent_builder executor module.

Tests integration between executor components and history management.
"""
from datetime import datetime, timezone
from unittest.mock import MagicMock, Mock, patch

import pytest

from openjiuwen.dev_tools.agent_builder.executor.history_manager import (
    DialogueMessage,
    HistoryCache,
    HistoryManager,
)
from openjiuwen.dev_tools.agent_builder.utils.enums import AgentType
from openjiuwen.dev_tools.agent_builder.executor.history_manager import HistoryManager
from openjiuwen.dev_tools.agent_builder.executor.history_manager import HistoryManager


class TestHistoryManagerIntegration:
    @staticmethod
    def test_history_manager_multi_session_workflow():
        manager1 = HistoryManager()
        manager2 = HistoryManager()
        
        manager1.add_message("创建一个助手", "user")
        manager1.add_message("好的，请告诉我助手名称", "assistant")
        manager1.add_message("叫小助手", "user")
        
        manager2.add_message("创建一个工作流", "user")
        manager2.add_message("请描述工作流需求", "assistant")
        
        history_001 = manager1.get_history()
        history_002 = manager2.get_history()
        
        assert len(history_001) == 3
        assert len(history_002) == 2
        assert history_001[0]["content"] == "创建一个助手"
        assert history_002[0]["content"] == "创建一个工作流"

    @staticmethod
    def test_history_manager_with_limit():
        manager = HistoryManager()
        
        for i in range(10):
            manager.add_message(f"消息 {i}", "user")
        
        recent_history = manager.get_latest_k_messages(5)
        
        assert len(recent_history) == 5
        assert recent_history[0]["content"] == "消息 5"

    @staticmethod
    def test_history_cache_max_size_enforcement():
        cache = HistoryCache(max_history_size=5)
        
        for i in range(10):
            msg = DialogueMessage(
                content=f"消息 {i}",
                role="user",
                timestamp=datetime.now(timezone.utc)
            )
            cache.add_message(msg)
        
        history = cache.get_messages(-1)
        
        assert len(history) == 5
        assert history[0]["content"] == "消息 5"

    @staticmethod
    def test_dialogue_message_creation():
        user_msg = DialogueMessage(
            content="用户消息",
            role="user",
            timestamp=datetime.now(timezone.utc)
        )
        assistant_msg = DialogueMessage(
            content="助手回复",
            role="assistant",
            timestamp=datetime.now(timezone.utc)
        )
        
        assert user_msg.role == "user"
        assert user_msg.content == "用户消息"
        assert assistant_msg.role == "assistant"
        assert assistant_msg.content == "助手回复"

    @staticmethod
    def test_dialogue_message_to_dict():
        msg = DialogueMessage(
            content="测试消息",
            role="user",
            timestamp=datetime.now(timezone.utc)
        )
        
        msg_dict = msg.to_dict()
        
        assert msg_dict["role"] == "user"
        assert msg_dict["content"] == "测试消息"
        assert "timestamp" not in msg_dict


class TestHistoryManagerPersistence:
    @staticmethod
    def test_session_clear_and_recreate():
        manager = HistoryManager()
        
        manager.add_message("消息1", "user")
        manager.add_message("回复1", "assistant")
        
        assert len(manager.get_history()) == 2
        
        manager.clear()
        
        assert len(manager.get_history()) == 0
        
        manager.add_message("新消息", "user")
        
        assert len(manager.get_history()) == 1

    @staticmethod
    def test_message_timestamp_ordering():
        import time
        
        manager = HistoryManager()
        
        manager.add_message("消息1", "user")
        time.sleep(0.01)
        manager.add_message("消息2", "user")
        
        history = manager.get_history()
        
        assert history[0]["content"] == "消息1"
        assert history[1]["content"] == "消息2"


class TestExecutorIntegration:
    @staticmethod
    def test_executor_with_real_history_manager():        
        history_manager_map = {}
        mock_llm = Mock()
        
        with patch('openjiuwen.dev_tools.agent_builder.executor.executor.create_core_model') as mock_create:
            mock_create.return_value = mock_llm
            
            from openjiuwen.dev_tools.agent_builder.executor.executor import AgentBuilderExecutor
            
            executor1 = AgentBuilderExecutor(
                query="创建助手",
                session_id="session_001",
                agent_type=AgentType.LLM_AGENT,
                history_manager_map=history_manager_map,
                model_info={
                    "model_provider": "openai",
                    "model_name": "gpt-4",
                    "api_key": "test",
                    "temperature": 0.7,
                    "top_p": 0.9,
                },
                enable_progress=False,
            )
            
            executor2 = AgentBuilderExecutor(
                query="继续对话",
                session_id="session_001",
                agent_type=AgentType.LLM_AGENT,
                history_manager_map=history_manager_map,
                model_info={
                    "model_provider": "openai",
                    "model_name": "gpt-4",
                    "api_key": "test",
                    "temperature": 0.7,
                    "top_p": 0.9,
                },
                enable_progress=False,
            )
            
            assert executor1.history_manager is executor2.history_manager
            assert "session_001" in history_manager_map

    @staticmethod
    def test_executor_history_persistence_across_executions():
        history_manager_map = {}
        mock_llm = Mock()
        
        with patch('openjiuwen.dev_tools.agent_builder.executor.executor.create_core_model') as mock_create:
            mock_create.return_value = mock_llm
            
            from openjiuwen.dev_tools.agent_builder.executor.executor import AgentBuilderExecutor
            
            executor = AgentBuilderExecutor(
                query="创建助手",
                session_id="session_001",
                agent_type=AgentType.LLM_AGENT,
                history_manager_map=history_manager_map,
                model_info={
                    "model_provider": "openai",
                    "model_name": "gpt-4",
                    "api_key": "test",
                    "temperature": 0.7,
                    "top_p": 0.9,
                },
                enable_progress=False,
            )
            
            executor.history_manager.add_message("测试消息", "user")
            
            assert len(executor.history_manager.get_history()) == 1
