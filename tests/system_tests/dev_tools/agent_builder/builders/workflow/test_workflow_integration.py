# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""
System tests for Workflow builder module.

Tests integration between Workflow builder components.
"""
from unittest.mock import MagicMock, Mock, patch

import pytest

from openjiuwen.dev_tools.agent_builder.builders.workflow.builder import WorkflowBuilder
from openjiuwen.dev_tools.agent_builder.builders.workflow.cycle_checker import CycleChecker
from openjiuwen.dev_tools.agent_builder.builders.workflow.intention_detector import (
    IntentionDetector as WorkflowIntentionDetector,
)
from openjiuwen.dev_tools.agent_builder.executor.history_manager import HistoryManager
from openjiuwen.dev_tools.agent_builder.utils.enums import BuildState


class TestWorkflowBuilderIntegration:
    @pytest.fixture
    def mock_llm(self):
        return Mock()

    @pytest.fixture
    def history_manager(self):
        return HistoryManager()

    @staticmethod
    def test_workflow_builder_initialization(mock_llm, history_manager):
        with patch('openjiuwen.dev_tools.agent_builder.builders.base.ResourceRetriever') as mock_retriever:
            mock_retriever_instance = Mock()
            mock_retriever.return_value = mock_retriever_instance
            
            builder = WorkflowBuilder(mock_llm, history_manager)
            
            assert builder.state == BuildState.INITIAL
            assert builder.llm is mock_llm
            assert builder.history_manager is history_manager

    @staticmethod
    def test_workflow_builder_is_workflow(mock_llm, history_manager):
        with patch('openjiuwen.dev_tools.agent_builder.builders.base.ResourceRetriever') as mock_retriever:
            mock_retriever_instance = Mock()
            mock_retriever.return_value = mock_retriever_instance
            
            builder = WorkflowBuilder(mock_llm, history_manager)
            
            assert builder.is_workflow_builder() is True

    @staticmethod
    def test_workflow_builder_reset(mock_llm, history_manager):
        with patch('openjiuwen.dev_tools.agent_builder.builders.base.ResourceRetriever') as mock_retriever:
            mock_retriever_instance = Mock()
            mock_retriever.return_value = mock_retriever_instance
            
            builder = WorkflowBuilder(mock_llm, history_manager)
            
            builder.state = BuildState.PROCESSING
            builder.reset()
            
            assert builder.state == BuildState.INITIAL

    @staticmethod
    def test_workflow_builder_get_build_status(mock_llm, history_manager):
        with patch('openjiuwen.dev_tools.agent_builder.builders.base.ResourceRetriever') as mock_retriever:
            mock_retriever_instance = Mock()
            mock_retriever.return_value = mock_retriever_instance
            
            builder = WorkflowBuilder(mock_llm, history_manager)
            
            status = builder.get_build_status()
            
            assert "state" in status
            assert status["state"] == BuildState.INITIAL.value


class TestCycleCheckerIntegration:
    @staticmethod
    def test_cycle_checker_initialization():
        mock_llm = Mock()
        checker = CycleChecker(mock_llm)
        
        assert checker is not None

    @staticmethod
    def test_cycle_checker_has_required_methods():
        mock_llm = Mock()
        checker = CycleChecker(mock_llm)
        
        assert hasattr(checker, 'check_and_parse')
        assert hasattr(checker, 'check_mermaid_cycle')
        assert hasattr(checker, 'parse_cycle_result_json')

    @staticmethod
    def test_cycle_checker_parse_result_json():
        result = CycleChecker.parse_cycle_result_json('{"need_refined": false, "loop_desc": ""}')
        
        assert result == (False, "")

    @staticmethod
    def test_cycle_checker_parse_result_json_with_cycle():
        result = CycleChecker.parse_cycle_result_json('{"need_refined": true, "loop_desc": "检测到环"}')
        
        assert result == (True, "检测到环")


class TestWorkflowIntentionDetectorIntegration:
    @staticmethod
    def test_intention_detector_initialization():
        mock_llm = Mock()
        detector = WorkflowIntentionDetector(mock_llm)
        
        assert detector is not None

    @staticmethod
    def test_intention_detector_has_required_methods():
        mock_llm = Mock()
        detector = WorkflowIntentionDetector(mock_llm)
        
        assert hasattr(detector, 'detect_initial_instruction')
        assert hasattr(detector, 'detect_refine_intent')


class TestWorkflowBuilderComponents:
    @pytest.fixture
    def mock_llm(self):
        return Mock()

    @pytest.fixture
    def history_manager(self):
        return HistoryManager()

    @staticmethod
    def test_workflow_builder_has_required_components(mock_llm, history_manager):
        with patch('openjiuwen.dev_tools.agent_builder.builders.base.ResourceRetriever') as mock_retriever:
            mock_retriever_instance = Mock()
            mock_retriever.return_value = mock_retriever_instance
            
            builder = WorkflowBuilder(mock_llm, history_manager)
            
            assert hasattr(builder, '_intention_detector')
            assert hasattr(builder, '_workflow_designer')
            assert hasattr(builder, '_dl_generator')
            assert hasattr(builder, '_dl_reflector')
            assert hasattr(builder, '_dl_transformer')
            assert hasattr(builder, '_cycle_checker')

    @staticmethod
    def test_workflow_builder_internal_state(mock_llm, history_manager):
        with patch('openjiuwen.dev_tools.agent_builder.builders.base.ResourceRetriever') as mock_retriever:
            mock_retriever_instance = Mock()
            mock_retriever.return_value = mock_retriever_instance
            
            builder = WorkflowBuilder(mock_llm, history_manager)
            
            assert builder.workflow_name is None
            assert builder.workflow_name_en is None
            assert builder.workflow_desc is None
            assert builder.dl is None
            assert builder.mermaid_code is None


class TestDLTransformerIntegration:
    @staticmethod
    def test_dl_transformer_import():
        from openjiuwen.dev_tools.agent_builder.builders.workflow.dl_transformer import DLTransformer
        
        transformer = DLTransformer()
        
        assert transformer is not None

    @staticmethod
    def test_dl_transformer_has_required_methods():
        from openjiuwen.dev_tools.agent_builder.builders.workflow.dl_transformer import DLTransformer
        
        transformer = DLTransformer()
        
        assert hasattr(transformer, 'transform_to_dsl')
        assert hasattr(transformer, 'transform_to_mermaid')


class TestWorkflowDesignerIntegration:
    @staticmethod
    def test_workflow_designer_import():
        from openjiuwen.dev_tools.agent_builder.builders.workflow.workflow_designer import WorkflowDesigner
        
        mock_llm = Mock()
        designer = WorkflowDesigner(mock_llm)
        
        assert designer is not None

    @staticmethod
    def test_workflow_designer_has_required_methods():
        from openjiuwen.dev_tools.agent_builder.builders.workflow.workflow_designer import WorkflowDesigner
        
        mock_llm = Mock()
        designer = WorkflowDesigner(mock_llm)
        
        assert hasattr(designer, 'design')


class TestDLGeneratorIntegration:
    @staticmethod
    def test_dl_generator_import():
        from openjiuwen.dev_tools.agent_builder.builders.workflow.dl_generator import DLGenerator
        
        mock_llm = Mock()
        generator = DLGenerator(mock_llm)
        
        assert generator is not None

    @staticmethod
    def test_dl_generator_has_required_methods():
        from openjiuwen.dev_tools.agent_builder.builders.workflow.dl_generator import DLGenerator
        
        mock_llm = Mock()
        generator = DLGenerator(mock_llm)
        
        assert hasattr(generator, 'generate')
        assert hasattr(generator, 'refine')
