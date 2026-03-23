# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
from unittest.mock import AsyncMock, Mock, patch

import pytest

from openjiuwen.dev_tools.agent_builder.builders.workflow.dl_transformer.dl_transformer import DLTransformer


class TestDLTransformerCollectPlugin:
    """Test DLTransformer.collect_plugin method."""

    @staticmethod
    def test_collect_plugin_success():
        """Test successful plugin collection."""
        tool_id_list = ["tool_1", "tool_2"]
        plugin_dict = {
            "plugin_1": {
                "plugin_name": "Test Plugin",
                "plugin_version": "1.0",
                "tools": {
                    "tool_1": {
                        "tool_name": "Tool 1",
                        "ori_inputs": [],
                        "ori_outputs": []
                    }
                }
            }
        }
        tool_id_map = {"tool_1": "plugin_1"}
        
        result = DLTransformer.collect_plugin(tool_id_list, plugin_dict, tool_id_map)
        
        assert len(result) == 1
        assert result[0]["tool_id"] == "tool_1"
        assert result[0]["plugin_id"] == "plugin_1"

    @staticmethod
    def test_collect_plugin_missing_tool():
        """Test plugin collection with missing tool."""
        tool_id_list = ["tool_missing"]
        plugin_dict = {}
        tool_id_map = {}
        
        result = DLTransformer.collect_plugin(tool_id_list, plugin_dict, tool_id_map)
        
        assert len(result) == 0

    @staticmethod
    def test_collect_plugin_empty_list():
        """Test plugin collection with empty list."""
        result = DLTransformer.collect_plugin([], {}, {})
        
        assert len(result) == 0

    @staticmethod
    def test_collect_plugin_with_code():
        """Test plugin collection with code."""
        tool_id_list = ["tool_1"]
        plugin_dict = {
            "plugin_1": {
                "plugin_name": "Code Plugin",
                "plugin_version": "1.0",
                "tools": {
                    "tool_1": {
                        "tool_name": "Code Tool",
                        "ori_inputs": [],
                        "ori_outputs": [],
                        "language": "python",
                        "code": "print('hello')"
                    }
                }
            }
        }
        tool_id_map = {"tool_1": "plugin_1"}
        
        result = DLTransformer.collect_plugin(tool_id_list, plugin_dict, tool_id_map)
        
        assert result[0]["language"] == "python"
        assert result[0]["code"] == "print('hello')"


class TestDLTransformerDslConverterRegistry:
    """Test DLTransformer.get_dsl_converter_registry()."""

    @staticmethod
    def test_registry_contains_all_types():
        """Test registry contains all node types."""
        expected_types = ["Start", "End", "LLM", "IntentDetection", "Questioner", "Code", "Plugin", "Output", "Branch"]
        
        for node_type in expected_types:
            assert node_type in DLTransformer.get_dsl_converter_registry()

    @staticmethod
    def test_registry_values_are_classes():
        """Test registry values are classes."""
        for converter_class in DLTransformer.get_dsl_converter_registry().values():
            assert isinstance(converter_class, type)


class TestDLTransformerInit:
    """Test DLTransformer initialization."""

    @staticmethod
    def test_init_success():
        """Test successful initialization."""
        transformer = DLTransformer()
        
        assert transformer is not None
