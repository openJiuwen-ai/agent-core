# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
import pytest

from openjiuwen.dev_tools.agent_builder.resource.processor import (
    TYPE_MAP,
    PluginProcessor,
    convert_type,
)


class TestTypeMap:
    @staticmethod
    def test_type_map_values():
        assert TYPE_MAP[1] == "string"
        assert TYPE_MAP[2] == "integer"
        assert TYPE_MAP[3] == "number"
        assert TYPE_MAP[4] == "boolean"
        assert TYPE_MAP[5] == "array"
        assert TYPE_MAP[6] == "object"


class TestConvertType:
    @staticmethod
    def test_convert_int_type():
        assert convert_type(1) == "string"
        assert convert_type(2) == "integer"
        assert convert_type(3) == "number"
        assert convert_type(4) == "boolean"
        assert convert_type(5) == "array"
        assert convert_type(6) == "object"

    @staticmethod
    def test_convert_string_type():
        assert convert_type("string") == "string"
        assert convert_type("integer") == "integer"
        assert convert_type("custom") == "custom"

    @staticmethod
    def test_convert_unknown_int_type():
        assert convert_type(999) == "string"

    @staticmethod
    def test_convert_none_type():
        assert convert_type(None) == "string"

    @staticmethod
    def test_convert_other_type():
        assert convert_type([1, 2, 3]) == "string"
        assert convert_type({"key": "value"}) == "string"


class TestPluginProcessor:
    @staticmethod
    def test_preprocess_empty_list():
        plugin_dict, tool_id_map = PluginProcessor.preprocess([])
        assert plugin_dict == {}
        assert tool_id_map == {}

    @staticmethod
    def test_preprocess_none():
        plugin_dict, tool_id_map = PluginProcessor.preprocess(None)
        assert plugin_dict == {}
        assert tool_id_map == {}

    @staticmethod
    def test_preprocess_single_plugin():
        raw_plugins = [
            {
                "plugin_id": "plugin_001",
                "plugin_name": "Test Plugin",
                "plugin_desc": "A test plugin",
                "plugin_version": "1.0.0",
                "tools": [
                    {
                        "tool_id": "tool_001",
                        "tool_name": "Test Tool",
                        "desc": "A test tool",
                        "code": "print('hello')",
                        "language": "python",
                        "input_parameters": [
                            {"name": "param1", "desc": "First param", "type": 1}
                        ],
                        "output_parameters": [
                            {"name": "result", "desc": "Result", "type": 1}
                        ]
                    }
                ]
            }
        ]
        
        plugin_dict, tool_id_map = PluginProcessor.preprocess(raw_plugins)
        
        assert "plugin_001" in plugin_dict
        assert "tool_001" in tool_id_map
        assert tool_id_map["tool_001"] == "plugin_001"
        
        plugin = plugin_dict["plugin_001"]
        assert plugin["plugin_name"] == "Test Plugin"
        assert plugin["plugin_desc"] == "A test plugin"
        assert "tool_001" in plugin["tools"]
        
        tool = plugin["tools"]["tool_001"]
        assert tool["tool_name"] == "Test Tool"
        assert tool["tool_desc"] == "A test tool"
        assert tool["code"] == "print('hello')"
        assert tool["language"] == "python"

    @staticmethod
    def test_preprocess_multiple_plugins():
        raw_plugins = [
            {
                "plugin_id": "plugin_001",
                "plugin_name": "Plugin 1",
                "plugin_desc": "First plugin",
                "tools": [
                    {"tool_id": "tool_001", "tool_name": "Tool 1", "desc": "Tool 1"}
                ]
            },
            {
                "plugin_id": "plugin_002",
                "plugin_name": "Plugin 2",
                "plugin_desc": "Second plugin",
                "tools": [
                    {"tool_id": "tool_002", "tool_name": "Tool 2", "desc": "Tool 2"}
                ]
            }
        ]
        
        plugin_dict, tool_id_map = PluginProcessor.preprocess(raw_plugins)
        
        assert len(plugin_dict) == 2
        assert len(tool_id_map) == 2
        assert tool_id_map["tool_001"] == "plugin_001"
        assert tool_id_map["tool_002"] == "plugin_002"

    @staticmethod
    def test_preprocess_plugin_without_id():
        raw_plugins = [
            {
                "plugin_name": "No ID Plugin",
                "tools": []
            }
        ]
        
        plugin_dict, tool_id_map = PluginProcessor.preprocess(raw_plugins)
        assert len(plugin_dict) == 0
        assert len(tool_id_map) == 0

    @staticmethod
    def test_preprocess_tool_without_id():
        raw_plugins = [
            {
                "plugin_id": "plugin_001",
                "plugin_name": "Test Plugin",
                "tools": [
                    {"tool_name": "No ID Tool"}
                ]
            }
        ]
        
        plugin_dict, tool_id_map = PluginProcessor.preprocess(raw_plugins)
        assert "plugin_001" in plugin_dict
        assert len(tool_id_map) == 0

    @staticmethod
    def test_format_for_prompt():
        plugin_dict = {
            "plugin_001": {
                "plugin_id": "plugin_001",
                "plugin_name": "Test Plugin",
                "plugin_desc": "A test plugin",
                "tools": {
                    "tool_001": {
                        "tool_id": "tool_001",
                        "tool_name": "Test Tool",
                        "tool_desc": "A test tool",
                        "code": "print('hello')",
                        "language": "python",
                        "input_parameters": [
                            {"name": "param1", "desc": "First param", "type": 1}
                        ],
                        "output_parameters": [
                            {"name": "result", "desc": "Result", "type": 2}
                        ]
                    }
                }
            }
        }
        
        result = PluginProcessor.format_for_prompt(plugin_dict)
        
        assert len(result) == 1
        assert result[0]["plugin_id"] == "plugin_001"
        assert result[0]["plugin_name"] == "Test Plugin"
        assert len(result[0]["tools"]) == 1
        
        tool = result[0]["tools"][0]
        assert tool["tool_id"] == "tool_001"
        assert tool["tool_name"] == "Test Tool"
        assert tool["input_parameters"][0]["type"] == "string"
        assert tool["output_parameters"][0]["type"] == "integer"

    @staticmethod
    def test_format_for_prompt_empty():
        result = PluginProcessor.format_for_prompt({})
        assert result == []

    @staticmethod
    def test_get_retrieved_info():
        plugin_dict = {
            "plugin_001": {
                "plugin_id": "plugin_001",
                "plugin_name": "Test Plugin",
                "plugin_desc": "A test plugin",
                "tools": {
                    "tool_001": {
                        "tool_id": "tool_001",
                        "tool_name": "Test Tool",
                        "tool_desc": "A test tool",
                        "inputs_for_dl_gen": [{"name": "p1", "desc": "param", "type": 1}],
                        "outputs_for_dl_gen": [{"name": "r1", "desc": "result", "type": 1}]
                    },
                    "tool_002": {
                        "tool_id": "tool_002",
                        "tool_name": "Tool 2",
                        "tool_desc": "Another tool",
                        "inputs_for_dl_gen": [],
                        "outputs_for_dl_gen": []
                    }
                }
            }
        }
        tool_id_map = {
            "tool_001": "plugin_001",
            "tool_002": "plugin_001"
        }
        
        tool_list, retrieved_dict, retrieved_map = PluginProcessor.get_retrieved_info(
            ["tool_001"],
            plugin_dict,
            tool_id_map,
            need_inputs_outputs=True
        )
        
        assert len(tool_list) == 1
        assert tool_list[0]["tool_id"] == "tool_001"
        assert tool_list[0]["tool_name"] == "Test Tool"
        assert "inputs" in tool_list[0]
        assert "outputs" in tool_list[0]

    @staticmethod
    def test_get_retrieved_info_without_inputs_outputs():
        plugin_dict = {
            "plugin_001": {
                "plugin_id": "plugin_001",
                "plugin_name": "Test Plugin",
                "plugin_desc": "A test plugin",
                "tools": {
                    "tool_001": {
                        "tool_id": "tool_001",
                        "tool_name": "Test Tool",
                        "tool_desc": "A test tool",
                        "inputs_for_dl_gen": [],
                        "outputs_for_dl_gen": []
                    }
                }
            }
        }
        tool_id_map = {"tool_001": "plugin_001"}
        
        tool_list, _, _ = PluginProcessor.get_retrieved_info(
            ["tool_001"],
            plugin_dict,
            tool_id_map,
            need_inputs_outputs=False
        )
        
        assert "inputs" not in tool_list[0]
        assert "outputs" not in tool_list[0]

    @staticmethod
    def test_get_retrieved_info_invalid_tool_id():
        plugin_dict = {
            "plugin_001": {
                "plugin_id": "plugin_001",
                "plugin_name": "Test Plugin",
                "plugin_desc": "A test plugin",
                "tools": {}
            }
        }
        tool_id_map = {}
        
        tool_list, retrieved_dict, retrieved_map = PluginProcessor.get_retrieved_info(
            ["invalid_tool_id"],
            plugin_dict,
            tool_id_map
        )
        
        assert len(tool_list) == 0
        assert len(retrieved_dict) == 0
        assert len(retrieved_map) == 0
