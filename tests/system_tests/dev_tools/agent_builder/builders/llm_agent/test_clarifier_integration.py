# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""
System tests for LLM Agent Clarifier module.

Tests Clarifier integration with LLM and resources.
"""
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest

from openjiuwen.dev_tools.agent_builder.builders.llm_agent.clarifier import (
    RESOURCE_CONFIG,
    Clarifier,
)


class TestClarifierIntegration:
    """Test Clarifier integration."""

    @pytest.fixture
    def mock_llm(self):
        llm = Mock()
        llm.invoke = AsyncMock()
        return llm

    @pytest.fixture
    def clarifier(self, mock_llm):
        return Clarifier(mock_llm)

    @staticmethod
    def test_clarifier_initialization(clarifier, mock_llm):
        """Test Clarifier initialization."""
        assert clarifier.llm == mock_llm

    @staticmethod
    def test_resource_config_structure():
        """Test RESOURCE_CONFIG structure."""
        assert "plugin" in RESOURCE_CONFIG
        assert "knowledge" in RESOURCE_CONFIG
        assert "workflow" in RESOURCE_CONFIG
        
        for key, config in RESOURCE_CONFIG.items():
            assert "label" in config
            assert "id_key" in config
            assert "name_key" in config
            assert "desc_key" in config

    @staticmethod
    def test_parse_resource_output_empty(clarifier):
        """Test _parse_resource_output with empty output."""
        result, resource_dict = Clarifier.parse_resource_output("", {})
        
        assert isinstance(result, str)
        assert isinstance(resource_dict, dict)

    @staticmethod
    def test_parse_resource_output_with_plugins(clarifier):
        """Test _parse_resource_output with plugins."""
        resource_output = "插件: test_plugin"
        available_resources = {
            "plugin": [
                {"tool_id": "test_plugin", "tool_name": "Test Plugin"}
            ]
        }
        
        result, resource_dict = Clarifier.parse_resource_output(
            resource_output, available_resources
        )
        
        assert isinstance(result, str)
        assert isinstance(resource_dict, dict)


class TestClarifierResourceConfig:
    """Test Clarifier resource configuration."""

    @staticmethod
    def test_plugin_config():
        """Test plugin resource config."""
        config = RESOURCE_CONFIG["plugin"]
        assert config["label"] == "插件"
        assert config["id_key"] == "tool_id"
        assert config["name_key"] == "tool_name"
        assert config["desc_key"] == "tool_desc"

    @staticmethod
    def test_knowledge_config():
        """Test knowledge resource config."""
        config = RESOURCE_CONFIG["knowledge"]
        assert config["label"] == "知识库"
        assert config["id_key"] == "knowledge_id"
        assert config["name_key"] == "knowledge_name"
        assert config["desc_key"] == "knowledge_desc"

    @staticmethod
    def test_workflow_config():
        """Test workflow resource config."""
        config = RESOURCE_CONFIG["workflow"]
        assert config["label"] == "工作流"
        assert config["id_key"] == "workflow_id"
        assert config["name_key"] == "workflow_name"
        assert config["desc_key"] == "workflow_desc"
