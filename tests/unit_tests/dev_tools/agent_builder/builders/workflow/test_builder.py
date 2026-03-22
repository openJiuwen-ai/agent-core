# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
from unittest.mock import MagicMock, Mock, patch

import pytest

from openjiuwen.dev_tools.agent_builder.builders.workflow.builder import WorkflowBuilder
from openjiuwen.dev_tools.agent_builder.utils.enums import BuildState


class TestWorkflowBuilderInit:
    """Test WorkflowBuilder initialization."""

    @staticmethod
    def test_init_success(mock_model):
        """Test successful initialization."""
        history_manager = Mock()
        builder = WorkflowBuilder(mock_model, history_manager)
        
        assert builder.llm == mock_model
        assert builder.history_manager == history_manager
        assert builder.state == BuildState.INITIAL
        assert builder.workflow_name is None
        assert builder.workflow_name_en is None
        assert builder.workflow_desc is None
        assert builder.dl is None
        assert builder.mermaid_code is None

    @staticmethod
    def test_init_progress_reporter_default_none(mock_model):
        """Test progress reporter defaults to None."""
        history_manager = Mock()
        builder = WorkflowBuilder(mock_model, history_manager)
        
        assert builder.progress_reporter is None


class TestWorkflowBuilderResource:
    """Test WorkflowBuilder resource property."""

    @staticmethod
    def test_resource_property(mock_model):
        """Test resource property."""
        history_manager = Mock()
        builder = WorkflowBuilder(mock_model, history_manager)
        
        assert builder.resource == {}


class TestWorkflowBuilderState:
    """Test WorkflowBuilder state property."""

    @staticmethod
    def test_state_property(mock_model):
        """Test state property."""
        history_manager = Mock()
        builder = WorkflowBuilder(mock_model, history_manager)
        
        assert builder.state == BuildState.INITIAL
        
        builder.state = BuildState.PROCESSING
        assert builder.state == BuildState.PROCESSING
