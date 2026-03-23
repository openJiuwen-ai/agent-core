# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
from unittest.mock import MagicMock, Mock, patch

import pytest

from openjiuwen.dev_tools.agent_builder.main import AgentBuilder


class TestAgentBuilder:
    @pytest.fixture
    def valid_model_info(self):
        return {
            "model_provider": "openai",
            "model_name": "gpt-4",
            "api_key": "test_key",
            "temperature": 0.7,
            "top_p": 0.9,
        }

    @pytest.fixture
    def builder(self, valid_model_info):
        return AgentBuilder(model_info=valid_model_info)

    @staticmethod
    def test_builder_creation(valid_model_info):
        builder = AgentBuilder(model_info=valid_model_info)
        
        assert builder.model_info == valid_model_info
        assert builder.history_manager_map == {}
        assert builder.agent_builder_map == {}

    @staticmethod
    def test_builder_creation_with_empty_model_info():
        builder = AgentBuilder(model_info={})
        
        assert builder.model_info == {}
        assert builder.history_manager_map == {}
        assert builder.agent_builder_map == {}

    @staticmethod
    def test_builder_with_existing_maps(valid_model_info):
        from openjiuwen.dev_tools.agent_builder.executor.history_manager import HistoryManager
        
        history_map = {"session_001": HistoryManager()}
        builder_map = {}
        
        builder = AgentBuilder(
            model_info=valid_model_info,
            history_manager_map=history_map,
            agent_builder_map=builder_map,
        )
        
        assert builder.history_manager_map is history_map

    @staticmethod
    def test_build_llm_agent(builder):
        with patch('openjiuwen.dev_tools.agent_builder.executor.executor.create_core_model') as mock_create_model, \
             patch('openjiuwen.dev_tools.agent_builder.builders.factory.AgentBuilderFactory.create'
                ) as mock_create_builder:
            
            mock_create_model.return_value = Mock()
            mock_executor = MagicMock()
            mock_executor.execute.return_value = '{"name": "Test Agent"}'
            mock_executor.get_build_status.return_value = {"state": "completed"}
            mock_create_builder.return_value = mock_executor
            
            result = builder.build_llm_agent(
                query="创建一个助手",
                session_id="session_001"
            )
            
            assert result["session_id"] == "session_001"
            assert result["agent_type"] == "llm_agent"

    @staticmethod
    def test_build_workflow(builder):
        with patch('openjiuwen.dev_tools.agent_builder.executor.executor.create_core_model') as mock_create_model, \
             patch('openjiuwen.dev_tools.agent_builder.builders.factory.AgentBuilderFactory.create'
             ) as mock_create_builder:
            
            mock_create_model.return_value = Mock()
            mock_executor = MagicMock()
            mock_executor.execute.return_value = "graph TD; A-->B"
            mock_executor.get_build_status.return_value = {"state": "processing"}
            mock_create_builder.return_value = mock_executor
            
            result = builder.build_workflow(
                query="创建一个工作流",
                session_id="session_001"
            )
            
            assert result["session_id"] == "session_001"
            assert result["agent_type"] == "workflow"

    @staticmethod
    def test_build_agent_with_dsl_result(builder):
        with patch('openjiuwen.dev_tools.agent_builder.executor.executor.create_core_model') as mock_create_model, \
             patch('openjiuwen.dev_tools.agent_builder.builders.factory.AgentBuilderFactory.create'
             ) as mock_create_builder:
            
            mock_create_model.return_value = Mock()
            mock_executor = MagicMock()
            mock_executor.execute.return_value = '{"agent_id": "123", "name": "Test"}'
            mock_executor.get_build_status.return_value = {"state": "completed"}
            mock_create_builder.return_value = mock_executor
            
            result = builder.build_agent(
                query="test query",
                session_id="session_001",
                agent_type="llm_agent"
            )
            
            assert "dsl" in result
            assert result["dsl"]["agent_id"] == "123"

    @staticmethod
    def test_build_agent_with_string_result(builder):
        with patch('openjiuwen.dev_tools.agent_builder.executor.executor.create_core_model') as mock_create_model, \
             patch('openjiuwen.dev_tools.agent_builder.builders.factory.AgentBuilderFactory.create'
             ) as mock_create_builder:
            
            mock_create_model.return_value = Mock()
            mock_executor = MagicMock()
            mock_executor.execute.return_value = "需要更多信息"
            mock_executor.get_build_status.return_value = {"state": "initial"}
            mock_create_builder.return_value = mock_executor
            
            result = builder.build_agent(
                query="test query",
                session_id="session_001",
                agent_type="llm_agent"
            )
            
            assert "response" in result
            assert result["response"] == "需要更多信息"

    @staticmethod
    def test_build_agent_with_dict_result(builder):
        with patch('openjiuwen.dev_tools.agent_builder.executor.executor.create_core_model') as mock_create_model, \
             patch('openjiuwen.dev_tools.agent_builder.builders.factory.AgentBuilderFactory.create'
             ) as mock_create_builder:
            
            mock_create_model.return_value = Mock()
            mock_executor = MagicMock()
            mock_executor.execute.return_value = {"key": "value", "nested": {"a": 1}}
            mock_executor.get_build_status.return_value = {"state": "completed"}
            mock_create_builder.return_value = mock_executor
            
            result = builder.build_agent(
                query="test query",
                session_id="session_001",
                agent_type="llm_agent"
            )
            
            assert result["key"] == "value"
            assert result["nested"]["a"] == 1

    @staticmethod
    def test_get_session_history_empty(builder):
        result = builder.get_session_history("nonexistent_session")
        
        assert result == []

    @staticmethod
    def test_get_session_history_with_data(builder):
        from openjiuwen.dev_tools.agent_builder.executor.history_manager import HistoryManager
        
        manager = HistoryManager()
        manager.add_user_message("Hello")
        manager.add_assistant_message("Hi!")
        builder.history_manager_map["session_001"] = manager
        
        history = builder.get_session_history("session_001")
        
        assert len(history) == 2
        assert history[0]["role"] == "user"
        assert history[1]["role"] == "assistant"

    @staticmethod
    def test_get_session_history_with_limit(builder):
        from openjiuwen.dev_tools.agent_builder.executor.history_manager import HistoryManager
        
        manager = HistoryManager()
        manager.add_user_message("Message 1")
        manager.add_user_message("Message 2")
        manager.add_user_message("Message 3")
        builder.history_manager_map["session_001"] = manager
        
        history = builder.get_session_history("session_001", k=2)
        
        assert len(history) == 2

    @staticmethod
    def test_clear_session(builder):
        from openjiuwen.dev_tools.agent_builder.executor.history_manager import HistoryManager
        
        manager = HistoryManager()
        manager.add_user_message("Test")
        builder.history_manager_map["session_001"] = manager
        
        builder.clear_session("session_001")
        
        assert len(manager.get_history()) == 0

    @staticmethod
    def test_clear_nonexistent_session(builder):
        builder.clear_session("nonexistent_session")

    @staticmethod
    def test_get_build_status_nonexistent(builder):
        status = builder.get_build_status("nonexistent_session")
        
        assert status["session_id"] == "nonexistent_session"
        assert status["state"] == "not_found"

    @staticmethod
    def test_get_build_status_existing(builder):
        mock_builder = MagicMock()
        mock_builder.get_build_status.return_value = {
            "state": "processing",
            "resource_count": {"plugins": 2}
        }
        
        builder.agent_builder_map["session_001"] = mock_builder
        
        status = builder.get_build_status("session_001")
        
        assert status["state"] == "processing"
        assert status["resource_count"]["plugins"] == 2

    @staticmethod
    def test_get_progress_nonexistent():
        result = AgentBuilder.get_progress("nonexistent_session")
        
        assert result is None

    @staticmethod
    def test_map_state_to_status_llm_agent():
        assert AgentBuilder.map_state_to_status("initial", "llm_agent") == "clarifying"
        assert AgentBuilder.map_state_to_status("processing", "llm_agent") == "processing"
        assert AgentBuilder.map_state_to_status("completed", "llm_agent") == "completed"

    @staticmethod
    def test_map_state_to_status_workflow():
        assert AgentBuilder.map_state_to_status("initial", "workflow") == "requesting"
        assert AgentBuilder.map_state_to_status("processing", "workflow") == "processing"
        assert AgentBuilder.map_state_to_status("completed", "workflow") == "completed"

    @staticmethod
    def test_map_state_to_status_unknown():
        assert AgentBuilder.map_state_to_status("unknown", "llm_agent") == "unknown"
        assert AgentBuilder.map_state_to_status("invalid", "workflow") == "unknown"
