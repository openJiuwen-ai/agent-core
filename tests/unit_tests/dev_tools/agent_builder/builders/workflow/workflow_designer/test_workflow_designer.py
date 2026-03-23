# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
from unittest.mock import AsyncMock, Mock, patch

import pytest

from openjiuwen.dev_tools.agent_builder.builders.workflow.workflow_designer.workflow_designer import WorkflowDesigner


class TestWorkflowDesignerInit:
    """Test WorkflowDesigner initialization."""

    @staticmethod
    def test_init_success(mock_model):
        """Test successful initialization."""
        designer = WorkflowDesigner(mock_model)
        
        assert designer.llm == mock_model

    @staticmethod
    def test_init_with_none_llm():
        """Test initialization with None LLM."""
        designer = WorkflowDesigner(None)
        
        assert designer.llm is None


class TestWorkflowDesignerParseReflectionResult:
    """Test WorkflowDesigner _parse_reflection_result method."""

    @staticmethod
    def test_parse_with_chinese_separator():
        """Test parse with Chinese separator."""
        result = WorkflowDesigner.parse_reflection_result(
            "## 问题评估\n无问题\n## New Workflow Design\nFinal design"
        )
        
        assert "Final design" in result

    @staticmethod
    def test_parse_with_english_separator():
        """Test parse with English separator."""
        result = WorkflowDesigner.parse_reflection_result(
            "Evaluation\n New Workflow Design\nFinal design"
        )
        
        assert "Final design" in result

    @staticmethod
    def test_parse_without_separator():
        """Test parse without separator."""
        result = WorkflowDesigner.parse_reflection_result("Just design content")
        
        assert result == "Just design content"
