# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""
System tests for Workflow Designer module.

Tests WorkflowDesigner integration with LLM.
"""
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest

from openjiuwen.dev_tools.agent_builder.builders.workflow.workflow_designer.workflow_designer import WorkflowDesigner


class TestWorkflowDesignerIntegration:
    """Test WorkflowDesigner integration."""

    @pytest.fixture
    def mock_llm(self):
        llm = Mock()
        llm.invoke = AsyncMock()
        return llm

    @pytest.fixture
    def workflow_designer(self, mock_llm):
        return WorkflowDesigner(mock_llm)

    @staticmethod
    def test_workflow_designer_initialization(workflow_designer, mock_llm):
        """Test WorkflowDesigner initialization."""
        assert workflow_designer.llm == mock_llm

    @staticmethod
    def test_basic_design_integration(workflow_designer, mock_llm):
        """Test basic_design integration."""
        mock_llm.invoke.return_value = Mock(content="Basic design result")
        
        result = workflow_designer.basic_design("create workflow", "tool list")
        
        assert result == "Basic design result"
        mock_llm.invoke.assert_called()

    @staticmethod
    def test_branch_design_integration(workflow_designer, mock_llm):
        """Test branch_design integration."""
        mock_llm.invoke.return_value = Mock(content="Branch design result")
        
        result = workflow_designer.branch_design("create workflow", "basic design")
        
        assert result == "Branch design result"
        mock_llm.invoke.assert_called()

    @staticmethod
    def test_reflection_evaluation_integration(workflow_designer, mock_llm):
        """Test reflection_evaluation integration."""
        mock_llm.invoke.return_value = Mock(content="## New Workflow Design\nFinal design")
        
        result = workflow_designer.reflection_evaluation(
            "create workflow", "basic design", "branch design"
        )
        
        assert "Final design" in result
        mock_llm.invoke.assert_called()


class TestWorkflowDesignerParseReflectionResult:
    """Test WorkflowDesigner _parse_reflection_result method."""

    @staticmethod
    def test_parse_with_new_workflow_design_marker():
        """Test parse with New Workflow Design marker."""
        content = "## 问题评估\n无问题\n## New Workflow Design\nFinal design content"
        
        result = WorkflowDesigner.parse_reflection_result(content)
        
        assert "Final design content" in result

    @staticmethod
    def test_parse_without_marker():
        """Test parse without marker."""
        content = "Just some design content"
        
        result = WorkflowDesigner.parse_reflection_result(content)
        
        assert result == "Just some design content"

    @staticmethod
    def test_parse_with_english_marker():
        """Test parse with English marker."""
        content = "Evaluation\n New Workflow Design\nFinal design"
        
        result = WorkflowDesigner.parse_reflection_result(content)
        
        assert "Final design" in result


class TestWorkflowDesignerBasicDesign:
    """Test WorkflowDesigner basic_design method."""

    @pytest.fixture
    def mock_llm(self):
        llm = Mock()
        llm.invoke = AsyncMock()
        return llm

    @pytest.fixture
    def workflow_designer(self, mock_llm):
        return WorkflowDesigner(mock_llm)

    @staticmethod
    def test_basic_design_with_empty_tools(workflow_designer, mock_llm):
        """Test basic_design with empty tools."""
        mock_llm.invoke.return_value = Mock(content="Design without tools")
        
        result = workflow_designer.basic_design("create workflow", "")
        
        assert result == "Design without tools"

    @staticmethod
    def test_basic_design_with_tools(workflow_designer, mock_llm):
        """Test basic_design with tools."""
        mock_llm.invoke.return_value = Mock(content="Design with tools")
        
        result = workflow_designer.basic_design("create workflow", "tool1, tool2")
        
        assert result == "Design with tools"


class TestWorkflowDesignerBranchDesign:
    """Test WorkflowDesigner branch_design method."""

    @pytest.fixture
    def mock_llm(self):
        llm = Mock()
        llm.invoke = AsyncMock()
        return llm

    @pytest.fixture
    def workflow_designer(self, mock_llm):
        return WorkflowDesigner(mock_llm)

    @staticmethod
    def test_branch_design_basic(workflow_designer, mock_llm):
        """Test branch_design basic."""
        mock_llm.invoke.return_value = Mock(content="Branch design")
        
        result = workflow_designer.branch_design("create workflow", "basic design")
        
        assert result == "Branch design"
