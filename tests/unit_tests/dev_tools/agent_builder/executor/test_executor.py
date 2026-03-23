# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
from unittest.mock import MagicMock, Mock, patch

import pytest

from openjiuwen.core.common.exception.errors import ValidationError
from openjiuwen.dev_tools.agent_builder.executor.history_manager import HistoryManager


class TestCreateCoreModel:
    @staticmethod
    def test_create_model_with_valid_info():
        with patch('openjiuwen.dev_tools.agent_builder.executor.executor.Model') as mock_model_class:
            mock_model = Mock()
            mock_model_class.return_value = mock_model
            
            from openjiuwen.dev_tools.agent_builder.executor.executor import create_core_model
            
            model_info = {
                "model_provider": "openai",
                "model_name": "gpt-4",
                "api_key": "test_key",
                "api_base": "https://api.openai.com",
                "temperature": 0.7,
                "top_p": 0.9,
            }
            
            model = create_core_model(model_info)
            
            assert model is not None
            mock_model_class.assert_called_once()

    @staticmethod
    def test_create_model_missing_provider():
        from openjiuwen.dev_tools.agent_builder.executor.executor import create_core_model
        
        model_info = {
            "model_name": "gpt-4",
            "api_key": "test_key",
        }
        
        with pytest.raises(ValidationError):
            create_core_model(model_info)

    @staticmethod
    def test_create_model_missing_model_name():
        from openjiuwen.dev_tools.agent_builder.executor.executor import create_core_model
        
        model_info = {
            "model_provider": "openai",
            "api_key": "test_key",
        }
        
        with pytest.raises(ValidationError):
            create_core_model(model_info)

    @staticmethod
    def test_create_model_missing_api_key():
        from openjiuwen.dev_tools.agent_builder.executor.executor import create_core_model
        
        model_info = {
            "model_provider": "openai",
            "model_name": "gpt-4",
        }
        
        with pytest.raises(ValidationError):
            create_core_model(model_info)

    @staticmethod
    def test_create_model_empty_info():
        from openjiuwen.dev_tools.agent_builder.executor.executor import create_core_model
        
        with pytest.raises(ValidationError):
            create_core_model({})

    @staticmethod
    def test_create_model_none_info():
        from openjiuwen.dev_tools.agent_builder.executor.executor import create_core_model
        
        with pytest.raises(ValidationError):
            create_core_model(None)

    @staticmethod
    def test_create_model_provider_mapping():
        with patch('openjiuwen.dev_tools.agent_builder.executor.executor.Model') as mock_model_class:
            mock_model = Mock()
            mock_model_class.return_value = mock_model
            
            from openjiuwen.dev_tools.agent_builder.executor.executor import create_core_model
            
            model_info = {
                "model_provider": "openai",
                "model_name": "gpt-4",
                "api_key": "test_key",
                "temperature": 0.7,
                "top_p": 0.9,
            }
            
            model = create_core_model(model_info)
            assert model is not None

    @staticmethod
    def test_create_model_with_optional_params():
        with patch('openjiuwen.dev_tools.agent_builder.executor.executor.Model') as mock_model_class:
            mock_model = Mock()
            mock_model_class.return_value = mock_model
            
            from openjiuwen.dev_tools.agent_builder.executor.executor import create_core_model
            
            model_info = {
                "model_provider": "openai",
                "model_name": "gpt-4",
                "api_key": "test_key",
                "temperature": 0.7,
                "max_tokens": 1000,
                "top_p": 0.9,
            }
            
            model = create_core_model(model_info)
            assert model is not None


class TestAgentBuilderExecutor:
    @pytest.fixture
    def mock_model(self):
        return Mock()

    @pytest.fixture
    def history_manager_map(self):
        return {}

    @staticmethod
    def test_executor_creation(mock_model, history_manager_map):
        with patch('openjiuwen.dev_tools.agent_builder.executor.executor.create_core_model') as mock_create:
            mock_create.return_value = mock_model
            
            from openjiuwen.dev_tools.agent_builder.executor.executor import AgentBuilderExecutor
            
            executor = AgentBuilderExecutor(
                query="test query",
                session_id="session_001",
                agent_type="llm_agent",
                history_manager_map=history_manager_map,
                model_info={"model_provider": "openai", "model_name": "gpt-4", "api_key": "test"},
                enable_progress=False,
            )
            
            assert executor.query == "test query"
            assert executor.session_id == "session_001"
            assert executor.agent_type == "llm_agent"
            assert executor.progress_reporter is None

    @staticmethod
    def test_executor_creates_history_manager(mock_model, history_manager_map):
        with patch('openjiuwen.dev_tools.agent_builder.executor.executor.create_core_model') as mock_create:
            mock_create.return_value = mock_model
            
            from openjiuwen.dev_tools.agent_builder.executor.executor import AgentBuilderExecutor
            
            executor = AgentBuilderExecutor(
                query="test query",
                session_id="session_001",
                agent_type="llm_agent",
                history_manager_map=history_manager_map,
                model_info={"model_provider": "openai", "model_name": "gpt-4", "api_key": "test"},
                enable_progress=False,
            )
            
            assert "session_001" in history_manager_map
            assert executor.history_manager is not None

    @staticmethod
    def test_executor_reuses_history_manager(mock_model):
        with patch('openjiuwen.dev_tools.agent_builder.executor.executor.create_core_model') as mock_create:
            mock_create.return_value = mock_model
            history_manager_map = {}
            
            from openjiuwen.dev_tools.agent_builder.executor.executor import AgentBuilderExecutor
            
            executor1 = AgentBuilderExecutor(
                query="query 1",
                session_id="session_001",
                agent_type="llm_agent",
                history_manager_map=history_manager_map,
                model_info={"model_provider": "openai", "model_name": "gpt-4", "api_key": "test"},
                enable_progress=False,
            )
            
            executor2 = AgentBuilderExecutor(
                query="query 2",
                session_id="session_001",
                agent_type="llm_agent",
                history_manager_map=history_manager_map,
                model_info={"model_provider": "openai", "model_name": "gpt-4", "api_key": "test"},
                enable_progress=False,
            )
            
            assert executor1.history_manager is executor2.history_manager

    @staticmethod
    def test_executor_with_progress_enabled(mock_model, history_manager_map):
        with patch('openjiuwen.dev_tools.agent_builder.executor.executor.create_core_model') as mock_create:
            mock_create.return_value = mock_model
            
            from openjiuwen.dev_tools.agent_builder.executor.executor import AgentBuilderExecutor
            
            executor = AgentBuilderExecutor(
                query="test query",
                session_id="session_001",
                agent_type="llm_agent",
                history_manager_map=history_manager_map,
                model_info={"model_provider": "openai", "model_name": "gpt-4", "api_key": "test"},
                enable_progress=True,
            )
            
            assert executor.progress_reporter is not None

    @staticmethod
    def test_executor_invalid_agent_type(mock_model, history_manager_map):
        with patch('openjiuwen.dev_tools.agent_builder.executor.executor.create_core_model') as mock_create:
            mock_create.return_value = mock_model
            
            from openjiuwen.dev_tools.agent_builder.executor.executor import AgentBuilderExecutor
            
            with pytest.raises(ValidationError):
                AgentBuilderExecutor(
                    query="test query",
                    session_id="session_001",
                    agent_type="invalid_type",
                    history_manager_map=history_manager_map,
                    model_info={"model_provider": "openai", "model_name": "gpt-4", "api_key": "test"},
                    enable_progress=False,
                )

    @staticmethod
    def test_get_build_status(mock_model, history_manager_map):
        with patch('openjiuwen.dev_tools.agent_builder.executor.executor.create_core_model') as mock_create:
            mock_create.return_value = mock_model
            
            from openjiuwen.dev_tools.agent_builder.executor.executor import AgentBuilderExecutor
            
            executor = AgentBuilderExecutor(
                query="test query",
                session_id="session_001",
                agent_type="llm_agent",
                history_manager_map=history_manager_map,
                model_info={"model_provider": "openai", "model_name": "gpt-4", "api_key": "test"},
                enable_progress=False,
            )
            
            status = executor.get_build_status()
            
            assert status["session_id"] == "session_001"
            assert status["agent_type"] == "llm_agent"
            assert "state" in status

    @staticmethod
    def test_get_history_manager_static(history_manager_map):
        from openjiuwen.dev_tools.agent_builder.executor.executor import AgentBuilderExecutor
        
        manager = AgentBuilderExecutor.get_history_manager(
            "session_001",
            history_manager_map
        )
        
        assert isinstance(manager, HistoryManager)
        assert "session_001" in history_manager_map

    @staticmethod
    def test_get_history_manager_reuse():
        from openjiuwen.dev_tools.agent_builder.executor.executor import AgentBuilderExecutor
        
        history_manager_map = {}
        
        manager1 = AgentBuilderExecutor.get_history_manager(
            "session_001",
            history_manager_map
        )
        manager2 = AgentBuilderExecutor.get_history_manager(
            "session_001",
            history_manager_map
        )
        
        assert manager1 is manager2


class TestAgentBuilderExecutorExecute:
    @pytest.fixture
    def mock_model(self):
        return Mock()

    @pytest.fixture
    def history_manager_map(self):
        return {}

    @staticmethod
    def test_execute_adds_user_message(mock_model, history_manager_map):
        with patch('openjiuwen.dev_tools.agent_builder.executor.executor.create_core_model') as mock_create_model, \
             patch('openjiuwen.dev_tools.agent_builder.builders.factory.AgentBuilderFactory.create'
             ) as mock_create_builder:
            
            mock_create_model.return_value = mock_model
            mock_builder = MagicMock()
            mock_builder.execute.return_value = "test result"
            mock_builder.get_build_status.return_value = {"state": "processing"}
            mock_create_builder.return_value = mock_builder
            
            from openjiuwen.dev_tools.agent_builder.executor.executor import AgentBuilderExecutor
            
            executor = AgentBuilderExecutor(
                query="test query",
                session_id="session_001",
                agent_type="llm_agent",
                history_manager_map=history_manager_map,
                model_info={"model_provider": "openai", "model_name": "gpt-4", "api_key": "test"},
                enable_progress=False,
            )
            
            executor.execute()
            
            history = executor.history_manager.get_history()
            assert len(history) == 1
            assert history[0]["role"] == "user"
            assert history[0]["content"] == "test query"

    @staticmethod
    def test_execute_returns_result(mock_model, history_manager_map):
        with patch('openjiuwen.dev_tools.agent_builder.executor.executor.create_core_model') as mock_create_model, \
             patch('openjiuwen.dev_tools.agent_builder.builders.factory.AgentBuilderFactory.create'
             ) as mock_create_builder:
            
            mock_create_model.return_value = mock_model
            mock_builder = MagicMock()
            mock_builder.execute.return_value = "test result"
            mock_builder.get_build_status.return_value = {"state": "processing"}
            mock_create_builder.return_value = mock_builder
            
            from openjiuwen.dev_tools.agent_builder.executor.executor import AgentBuilderExecutor
            
            executor = AgentBuilderExecutor(
                query="test query",
                session_id="session_001",
                agent_type="llm_agent",
                history_manager_map=history_manager_map,
                model_info={"model_provider": "openai", "model_name": "gpt-4", "api_key": "test"},
                enable_progress=False,
            )
            
            result = executor.execute()
            
            assert result == "test result"
