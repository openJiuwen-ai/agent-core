# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""
System tests for agent_builder builders module.

Tests integration between builder components and factory.
"""
from unittest.mock import MagicMock, Mock, patch

import pytest

from openjiuwen.dev_tools.agent_builder.builders.base import BaseAgentBuilder
from openjiuwen.dev_tools.agent_builder.builders.factory import AgentBuilderFactory
from openjiuwen.dev_tools.agent_builder.executor.history_manager import HistoryManager
from openjiuwen.dev_tools.agent_builder.utils.enums import AgentType, BuildState


class TestBuilderFactoryIntegration:
    @pytest.fixture(autouse=True)
    def setup(self):
        AgentBuilderFactory.clear_registry()
        yield
        AgentBuilderFactory.clear_registry()

    @staticmethod
    def test_factory_creates_llm_agent_builder_integration():
        mock_llm = Mock()
        history_manager = HistoryManager()
        
        with patch('openjiuwen.dev_tools.agent_builder.builders.base.ResourceRetriever') as mock_retriever:
            mock_retriever_instance = Mock()
            mock_retriever.return_value = mock_retriever_instance
            
            builder = AgentBuilderFactory.create(
                agent_type=AgentType.LLM_AGENT,
                llm=mock_llm,
                history_manager=history_manager,
            )
            
            assert builder is not None
            assert isinstance(builder, BaseAgentBuilder)
            assert builder.state == BuildState.INITIAL

    @staticmethod
    def test_factory_creates_workflow_builder_integration():
        mock_llm = Mock()
        history_manager = HistoryManager()
        
        with patch('openjiuwen.dev_tools.agent_builder.builders.base.ResourceRetriever') as mock_retriever:
            mock_retriever_instance = Mock()
            mock_retriever.return_value = mock_retriever_instance
            
            builder = AgentBuilderFactory.create(
                agent_type=AgentType.WORKFLOW,
                llm=mock_llm,
                history_manager=history_manager,
            )
            
            assert builder is not None
            assert isinstance(builder, BaseAgentBuilder)

    @staticmethod
    def test_builder_get_build_status_integration():
        mock_llm = Mock()
        history_manager = HistoryManager()
        
        with patch('openjiuwen.dev_tools.agent_builder.builders.base.ResourceRetriever') as mock_retriever:
            mock_retriever_instance = Mock()
            mock_retriever.return_value = mock_retriever_instance
            
            builder = AgentBuilderFactory.create(
                agent_type=AgentType.LLM_AGENT,
                llm=mock_llm,
                history_manager=history_manager,
            )
            
            status = builder.get_build_status()
            
            assert "state" in status
            assert status["state"] == BuildState.INITIAL.value

    @staticmethod
    def test_builder_reset_integration():
        mock_llm = Mock()
        history_manager = HistoryManager()
        
        with patch('openjiuwen.dev_tools.agent_builder.builders.base.ResourceRetriever') as mock_retriever:
            mock_retriever_instance = Mock()
            mock_retriever.return_value = mock_retriever_instance
            
            builder = AgentBuilderFactory.create(
                agent_type=AgentType.LLM_AGENT,
                llm=mock_llm,
                history_manager=history_manager,
            )
            
            builder.state = BuildState.PROCESSING
            builder.reset()
            
            assert builder.state == BuildState.INITIAL
            assert builder.resource == {}


class TestBaseBuilderIntegration:
    @staticmethod
    def test_builder_with_history_manager():
        mock_llm = Mock()
        history_manager = HistoryManager()
        
        with patch('openjiuwen.dev_tools.agent_builder.builders.base.ResourceRetriever') as mock_retriever:
            mock_retriever_instance = Mock()
            mock_retriever.return_value = mock_retriever_instance
            
            builder = AgentBuilderFactory.create(
                agent_type=AgentType.LLM_AGENT,
                llm=mock_llm,
                history_manager=history_manager,
            )
            
            assert builder.history_manager is history_manager

    @staticmethod
    def test_builder_state_transitions():
        mock_llm = Mock()
        history_manager = HistoryManager()
        
        with patch('openjiuwen.dev_tools.agent_builder.builders.base.ResourceRetriever') as mock_retriever:
            mock_retriever_instance = Mock()
            mock_retriever.return_value = mock_retriever_instance
            
            builder = AgentBuilderFactory.create(
                agent_type=AgentType.LLM_AGENT,
                llm=mock_llm,
                history_manager=history_manager,
            )
            
            assert builder.state == BuildState.INITIAL
            
            builder.state = BuildState.PROCESSING
            assert builder.state == BuildState.PROCESSING
            
            builder.state = BuildState.COMPLETED
            assert builder.state == BuildState.COMPLETED


class TestBuilderResourceIntegration:
    @staticmethod
    def test_builder_resource_management():
        mock_llm = Mock()
        history_manager = HistoryManager()
        
        with patch('openjiuwen.dev_tools.agent_builder.builders.base.ResourceRetriever') as mock_retriever:
            mock_retriever_instance = Mock()
            mock_retriever.return_value = mock_retriever_instance
            
            builder = AgentBuilderFactory.create(
                agent_type=AgentType.LLM_AGENT,
                llm=mock_llm,
                history_manager=history_manager,
            )
            
            assert isinstance(builder.resource, dict)
            assert len(builder.resource) == 0
