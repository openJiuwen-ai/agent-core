# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
import os
from unittest.mock import AsyncMock, Mock, patch

import pytest

from openjiuwen.dev_tools.agent_builder.resource.retriever import ResourceRetriever


class TestResourceRetrieverInit:
    """Test ResourceRetriever initialization."""

    @staticmethod
    @patch('openjiuwen.dev_tools.agent_builder.resource.retriever.PluginProcessor')
    @patch.object(ResourceRetriever, 'load_resources')
    def test_init_success(mock_load_resources, mock_processor, mock_model):
        """Test successful initialization."""
        mock_load_resources.return_value = []
        mock_processor.preprocess.return_value = ({}, {})
        
        retriever = ResourceRetriever(mock_model)
        
        assert retriever.llm == mock_model
        assert retriever.plugin_dict == {}
        assert retriever.tool_plugin_id_map == {}

    @staticmethod
    @patch('openjiuwen.dev_tools.agent_builder.resource.retriever.PluginProcessor')
    @patch.object(ResourceRetriever, 'load_resources')
    def test_init_with_plugins(mock_load_resources, mock_processor, mock_model):
        """Test initialization with plugins."""
        mock_load_resources.return_value = [
            {"plugin_id": "plugin_1", "tools": [{"tool_id": "tool_1"}]}
        ]
        mock_processor.preprocess.return_value = (
            {"plugin_1": {"name": "Plugin 1"}},
            {"tool_1": "plugin_1"}
        )
        
        retriever = ResourceRetriever(mock_model)
        
        assert retriever.plugin_dict == {"plugin_1": {"name": "Plugin 1"}}
        assert retriever.tool_plugin_id_map == {"tool_1": "plugin_1"}


class TestResourceRetrieverLoadResources:
    """Test ResourceRetriever.load_resources method."""

    @staticmethod
    @patch('openjiuwen.dev_tools.agent_builder.resource.retriever.load_json_file')
    @patch('os.path.exists')
    def test_load_resources_default_path(mock_exists, mock_load_json):
        """Test load_resources with default path."""
        mock_exists.return_value = True
        mock_load_json.return_value = {"plugins": [{"plugin_id": "plugin_1"}]}
        
        result = ResourceRetriever.load_resources()
        
        assert len(result) == 1
        assert result[0]["plugin_id"] == "plugin_1"

    @staticmethod
    @patch('os.path.exists')
    def test_load_resources_file_not_found(mock_exists):
        """Test load_resources when file not found."""
        mock_exists.return_value = False
        
        result = ResourceRetriever.load_resources()
        
        assert result == []

    @staticmethod
    @patch('openjiuwen.dev_tools.agent_builder.resource.retriever.load_json_file')
    @patch('os.path.exists')
    def test_load_resources_custom_path(mock_exists, mock_load_json):
        """Test load_resources with custom path."""
        mock_exists.return_value = True
        mock_load_json.return_value = {"plugins": []}
        
        result = ResourceRetriever.load_resources("/custom/path/plugins.json")
        
        mock_load_json.assert_called_once_with("/custom/path/plugins.json")

    @staticmethod
    @patch('openjiuwen.dev_tools.agent_builder.resource.retriever.load_json_file')
    @patch('os.path.exists')
    def test_load_resources_empty_plugins(mock_exists, mock_load_json):
        """Test load_resources with empty plugins."""
        mock_exists.return_value = True
        mock_load_json.return_value = {}
        
        result = ResourceRetriever.load_resources()
        
        assert result == []
