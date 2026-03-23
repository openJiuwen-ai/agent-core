# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""
System tests for agent_builder main module.

Tests end-to-end integration of AgentBuilder.
"""
from unittest.mock import MagicMock, Mock, patch

import pytest

from openjiuwen.dev_tools.agent_builder.main import AgentBuilder
from openjiuwen.dev_tools.agent_builder.utils.enums import AgentType


class TestAgentBuilderEndToEnd:
    @pytest.fixture
    def valid_model_info(self):
        return {
            "model_provider": "openai",
            "model_name": "gpt-4",
            "api_key": "test_key",
            "temperature": 0.7,
            "top_p": 0.9,
        }

    @staticmethod
    def test_agent_builder_full_lifecycle(valid_model_info):
        with patch('openjiuwen.dev_tools.agent_builder.executor.executor.create_core_model') as mock_create_model:
            mock_llm = Mock()
            mock_create_model.return_value = mock_llm
            
            builder = AgentBuilder(model_info=valid_model_info)
            
            assert builder is not None
            assert builder.model_info == valid_model_info

    @staticmethod
    def test_agent_builder_multiple_sessions(valid_model_info):
        with patch('openjiuwen.dev_tools.agent_builder.executor.executor.create_core_model') as mock_create_model:
            mock_llm = Mock()
            mock_create_model.return_value = mock_llm
            
            builder = AgentBuilder(model_info=valid_model_info)
            
            with patch.object(builder, 'build_agent') as mock_build:
                mock_build.side_effect = [
                    {"session_id": "session_001", "status": "completed"},
                    {"session_id": "session_002", "status": "completed"}
                ]
                
                result1 = builder.build_llm_agent("创建助手1", "session_001")
                result2 = builder.build_llm_agent("创建助手2", "session_002")
                
                assert result1["session_id"] == "session_001"
                assert result2["session_id"] == "session_002"

    @staticmethod
    def test_agent_builder_session_history_management(valid_model_info):
        with patch('openjiuwen.dev_tools.agent_builder.executor.executor.create_core_model') as mock_create_model:
            mock_llm = Mock()
            mock_create_model.return_value = mock_llm
            
            builder = AgentBuilder(model_info=valid_model_info)
            
            mock_history_manager = Mock()
            mock_history_manager.get_history.return_value = [
                {"role": "user", "content": "消息1"},
                {"role": "assistant", "content": "回复1"}
            ]
            builder.history_manager_map["session_001"] = mock_history_manager
            
            history = builder.get_session_history("session_001")
            
            assert len(history) == 2

    @staticmethod
    def test_agent_builder_clear_session(valid_model_info):
        with patch('openjiuwen.dev_tools.agent_builder.executor.executor.create_core_model') as mock_create_model:
            mock_llm = Mock()
            mock_create_model.return_value = mock_llm
            
            builder = AgentBuilder(model_info=valid_model_info)
            
            mock_history_manager = Mock()
            builder.history_manager_map["session_001"] = mock_history_manager
            
            assert "session_001" in builder.history_manager_map
            
            builder.clear_session("session_001")
            
            mock_history_manager.clear.assert_called_once()


class TestAgentBuilderWorkflowIntegration:
    @pytest.fixture
    def valid_model_info(self):
        return {
            "model_provider": "openai",
            "model_name": "gpt-4",
            "api_key": "test_key",
            "temperature": 0.7,
            "top_p": 0.9,
        }

    @staticmethod
    def test_build_llm_agent_integration(valid_model_info):
        with patch('openjiuwen.dev_tools.agent_builder.executor.executor.create_core_model') as mock_create_model:
            mock_llm = Mock()
            mock_create_model.return_value = mock_llm
            
            builder = AgentBuilder(model_info=valid_model_info)
            
            with patch.object(builder, 'build_agent') as mock_build:
                mock_build.return_value = {
                    "session_id": "session_001",
                    "agent_type": "llm_agent",
                    "status": "completed"
                }
                
                result = builder.build_llm_agent("创建一个助手", "session_001")
                
                mock_build.assert_called_once_with("创建一个助手", "session_001", "llm_agent")
                assert result["session_id"] == "session_001"
                assert result["agent_type"] == "llm_agent"

    @staticmethod
    def test_build_workflow_integration(valid_model_info):
        with patch('openjiuwen.dev_tools.agent_builder.executor.executor.create_core_model') as mock_create_model:
            mock_llm = Mock()
            mock_create_model.return_value = mock_llm
            
            builder = AgentBuilder(model_info=valid_model_info)
            
            with patch.object(builder, 'build_agent') as mock_build:
                mock_build.return_value = {
                    "session_id": "session_001",
                    "agent_type": "workflow",
                    "status": "completed"
                }
                
                result = builder.build_workflow("创建一个工作流", "session_001")
                
                mock_build.assert_called_once_with("创建一个工作流", "session_001", "workflow")
                assert result["session_id"] == "session_001"
                assert result["agent_type"] == "workflow"


class TestAgentBuilderStatusIntegration:
    @pytest.fixture
    def valid_model_info(self):
        return {
            "model_provider": "openai",
            "model_name": "gpt-4",
            "api_key": "test_key",
            "temperature": 0.7,
            "top_p": 0.9,
        }

    @staticmethod
    def test_get_build_status_integration(valid_model_info):
        with patch('openjiuwen.dev_tools.agent_builder.executor.executor.create_core_model') as mock_create_model:
            mock_llm = Mock()
            mock_create_model.return_value = mock_llm
            
            builder = AgentBuilder(model_info=valid_model_info)
            
            mock_builder = Mock()
            mock_builder.get_build_status.return_value = {
                "state": "processing",
                "resource_count": {}
            }
            builder.agent_builder_map["session_001"] = mock_builder
            
            status = builder.get_build_status("session_001")
            
            assert status["state"] == "processing"

    @staticmethod
    def test_get_build_status_not_found(valid_model_info):
        with patch('openjiuwen.dev_tools.agent_builder.executor.executor.create_core_model') as mock_create_model:
            mock_llm = Mock()
            mock_create_model.return_value = mock_llm
            
            builder = AgentBuilder(model_info=valid_model_info)
            
            status = builder.get_build_status("non_existent_session")
            
            assert status["state"] == "not_found"

    @staticmethod
    def test_get_progress_integration(valid_model_info):
        with patch('openjiuwen.dev_tools.agent_builder.executor.executor.create_core_model') as mock_create_model, \
             patch('openjiuwen.dev_tools.agent_builder.main.progress_manager') as mock_progress_manager:
            
            mock_llm = Mock()
            mock_create_model.return_value = mock_llm
            
            mock_progress = Mock()
            mock_progress.to_dict.return_value = {
                "steps": [],
                "current_stage": "initializing"
            }
            mock_progress_manager.get_progress.return_value = mock_progress
            
            progress = AgentBuilder.get_progress("session_001")
            
            assert "steps" in progress


class TestAgentBuilderStateMapping:
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
