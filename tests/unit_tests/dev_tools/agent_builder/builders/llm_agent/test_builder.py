# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
from unittest.mock import MagicMock, Mock, patch

import pytest

from openjiuwen.dev_tools.agent_builder.builders.llm_agent.builder import LlmAgentBuilder
from openjiuwen.dev_tools.agent_builder.utils.enums import BuildState


class TestLlmAgentBuilderInit:
    """Test LlmAgentBuilder initialization."""

    @staticmethod
    def test_init_success(mock_model):
        """Test successful initialization."""
        history_manager = Mock()
        builder = LlmAgentBuilder(mock_model, history_manager)
        
        assert builder.llm == mock_model
        assert builder.history_manager == history_manager
        assert builder.state == BuildState.INITIAL
        assert builder.agent_config_info is None
        assert builder.factor_output_info is None
        assert builder.display_resource_info is None

    @staticmethod
    def test_init_progress_reporter_default_none(mock_model):
        """Test progress reporter defaults to None."""
        history_manager = Mock()
        builder = LlmAgentBuilder(mock_model, history_manager)
        
        assert builder.progress_reporter is None


class TestLlmAgentBuilderResourceUniqueKey:
    """Test LlmAgentBuilder resource unique key."""

    @staticmethod
    def test_resource_unique_key(mock_model):
        """Test RESOURCE_UNIQUE_KEY constant."""
        history_manager = Mock()
        builder = LlmAgentBuilder(mock_model, history_manager)
        
        assert hasattr(builder, 'RESOURCE_UNIQUE_KEY')
        assert builder.RESOURCE_UNIQUE_KEY == {"plugins": "tool_id"}


class TestLlmAgentBuilderResource:
    """Test LlmAgentBuilder resource property."""

    @staticmethod
    def test_resource_property(mock_model):
        """Test resource property."""
        history_manager = Mock()
        builder = LlmAgentBuilder(mock_model, history_manager)
        
        assert builder.resource == {}


class TestLlmAgentBuilderState:
    """Test LlmAgentBuilder state property."""

    @staticmethod
    def test_state_property(mock_model):
        """Test state property."""
        history_manager = Mock()
        builder = LlmAgentBuilder(mock_model, history_manager)
        
        assert builder.state == BuildState.INITIAL
        
        builder.state = BuildState.PROCESSING
        assert builder.state == BuildState.PROCESSING
