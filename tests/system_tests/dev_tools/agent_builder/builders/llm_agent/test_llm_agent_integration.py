# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""
System tests for LLM Agent builder module.

Tests integration between LLM Agent builder components.
"""
from unittest.mock import MagicMock, Mock, patch

import pytest

from openjiuwen.dev_tools.agent_builder.builders.llm_agent.builder import LlmAgentBuilder
from openjiuwen.dev_tools.agent_builder.builders.llm_agent.clarifier import Clarifier
from openjiuwen.dev_tools.agent_builder.builders.llm_agent.generator import Generator
from openjiuwen.dev_tools.agent_builder.builders.llm_agent.intention_detector import IntentionDetector
from openjiuwen.dev_tools.agent_builder.builders.llm_agent.transformer import Transformer
from openjiuwen.dev_tools.agent_builder.executor.history_manager import HistoryManager
from openjiuwen.dev_tools.agent_builder.utils.enums import BuildState


class TestLlmAgentBuilderIntegration:
    @pytest.fixture
    def mock_llm(self):
        return Mock()

    @pytest.fixture
    def history_manager(self):
        return HistoryManager()

    @staticmethod
    def test_llm_agent_builder_initialization(mock_llm, history_manager):
        with patch('openjiuwen.dev_tools.agent_builder.builders.base.ResourceRetriever') as mock_retriever:
            mock_retriever_instance = Mock()
            mock_retriever.return_value = mock_retriever_instance
            
            builder = LlmAgentBuilder(mock_llm, history_manager)
            
            assert builder.state == BuildState.INITIAL
            assert builder.llm is mock_llm
            assert builder.history_manager is history_manager

    @staticmethod
    def test_llm_agent_builder_components_initialization(mock_llm, history_manager):
        with patch('openjiuwen.dev_tools.agent_builder.builders.base.ResourceRetriever') as mock_retriever:
            mock_retriever_instance = Mock()
            mock_retriever.return_value = mock_retriever_instance
            
            builder = LlmAgentBuilder(mock_llm, history_manager)
            
            assert hasattr(builder, '_clarifier')
            assert hasattr(builder, '_generator')
            assert hasattr(builder, '_intention_detector')
            assert hasattr(builder, '_transformer')

    @staticmethod
    def test_llm_agent_builder_is_not_workflow(mock_llm, history_manager):
        with patch('openjiuwen.dev_tools.agent_builder.builders.base.ResourceRetriever') as mock_retriever:
            mock_retriever_instance = Mock()
            mock_retriever.return_value = mock_retriever_instance
            
            builder = LlmAgentBuilder(mock_llm, history_manager)
            
            assert builder.is_workflow_builder() is False

    @staticmethod
    def test_llm_agent_builder_reset(mock_llm, history_manager):
        with patch('openjiuwen.dev_tools.agent_builder.builders.base.ResourceRetriever') as mock_retriever:
            mock_retriever_instance = Mock()
            mock_retriever.return_value = mock_retriever_instance
            
            builder = LlmAgentBuilder(mock_llm, history_manager)
            
            builder.state = BuildState.PROCESSING
            builder.reset()
            
            assert builder.state == BuildState.INITIAL


class TestClarifierIntegration:
    @staticmethod
    def test_clarifier_initialization():
        mock_llm = Mock()
        clarifier = Clarifier(mock_llm)
        
        assert clarifier is not None

    @staticmethod
    def test_clarifier_has_required_methods():
        mock_llm = Mock()
        clarifier = Clarifier(mock_llm)
        
        assert hasattr(clarifier, 'clarify')


class TestGeneratorIntegration:
    @staticmethod
    def test_generator_initialization():
        mock_llm = Mock()
        generator = Generator(mock_llm)
        
        assert generator is not None

    @staticmethod
    def test_generator_has_required_methods():
        mock_llm = Mock()
        generator = Generator(mock_llm)
        
        assert hasattr(generator, 'generate')


class TestIntentionDetectorIntegration:
    @staticmethod
    def test_intention_detector_initialization():
        mock_llm = Mock()
        detector = IntentionDetector(mock_llm)
        
        assert detector is not None

    @staticmethod
    def test_intention_detector_has_required_methods():
        mock_llm = Mock()
        detector = IntentionDetector(mock_llm)
        
        assert hasattr(detector, 'detect_refine_intent')


class TestTransformerIntegration:
    @staticmethod
    def test_transformer_initialization():
        transformer = Transformer()
        
        assert transformer is not None

    @staticmethod
    def test_transformer_transform_method():
        transformer = Transformer()
        
        assert hasattr(transformer, 'transform_to_dsl')


class TestLlmAgentBuilderWorkflow:
    @pytest.fixture
    def mock_llm(self):
        llm = Mock()
        llm.invoke = Mock(return_value=MagicMock(content='{"name": "Test Agent"}'))
        return llm

    @pytest.fixture
    def history_manager(self):
        return HistoryManager()

    @staticmethod
    def test_builder_get_build_status(mock_llm, history_manager):
        with patch('openjiuwen.dev_tools.agent_builder.builders.base.ResourceRetriever') as mock_retriever:
            mock_retriever_instance = Mock()
            mock_retriever.return_value = mock_retriever_instance
            
            builder = LlmAgentBuilder(mock_llm, history_manager)
            
            status = builder.get_build_status()
            
            assert "state" in status
            assert status["state"] == BuildState.INITIAL.value

    @staticmethod
    def test_builder_withprogress_reporter(mock_llm, history_manager):
        from openjiuwen.dev_tools.agent_builder.utils.progress import ProgressReporter
        
        with patch('openjiuwen.dev_tools.agent_builder.builders.base.ResourceRetriever') as mock_retriever:
            mock_retriever_instance = Mock()
            mock_retriever.return_value = mock_retriever_instance
            
            progress_reporter = ProgressReporter(session_id="test_session", agent_type="llm_agent")
            builder = LlmAgentBuilder(mock_llm, history_manager)
            builder.progress_reporter = progress_reporter
            
            assert builder.progress_reporter is progress_reporter
