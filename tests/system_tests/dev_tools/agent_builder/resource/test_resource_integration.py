# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""
System tests for agent_builder resource module.

Tests integration between resource processing and prompt generation.
"""
from unittest.mock import Mock, patch

import pytest

from openjiuwen.dev_tools.agent_builder.resource.processor import PluginProcessor
from openjiuwen.dev_tools.agent_builder.resource.prompt import (
    RETRIEVE_SYSTEM_PROMPT,
    RETRIEVE_SYSTEM_TEMPLATE,
)


class TestPluginProcessorIntegration:
    @staticmethod
    def test_plugin_processor_full_workflow():
        plugins = [
            {
                "plugin_id": "plugin_001",
                "plugin_name": "天气查询",
                "plugin_desc": "查询指定城市的天气信息",
                "tools": [
                    {
                        "tool_id": "tool_001",
                        "tool_name": "get_weather",
                        "desc": "获取天气",
                        "input_parameters": [
                            {"name": "city", "type": 1, "description": "城市名称"}
                        ],
                        "output_parameters": [
                            {"name": "weather", "type": 1, "description": "天气描述"}
                        ]
                    }
                ]
            },
            {
                "plugin_id": "plugin_002",
                "plugin_name": "计算器",
                "plugin_desc": "执行数学计算",
                "tools": [
                    {
                        "tool_id": "tool_002",
                        "tool_name": "calculate",
                        "desc": "执行计算",
                        "input_parameters": [
                            {"name": "expression", "type": 1, "description": "数学表达式"}
                        ],
                        "output_parameters": [
                            {"name": "result", "type": 2, "description": "计算结果"}
                        ]
                    }
                ]
            }
        ]
        
        plugin_dict, tool_plugin_id_map = PluginProcessor.preprocess(plugins)
        
        assert len(plugin_dict) == 2
        assert "plugin_001" in plugin_dict
        assert "plugin_002" in plugin_dict
        assert "tool_001" in tool_plugin_id_map
        assert "tool_002" in tool_plugin_id_map
        
        prompt_text = PluginProcessor.format_for_prompt(plugin_dict)
        
        assert len(prompt_text) == 2
        assert any(p["plugin_name"] == "天气查询" for p in prompt_text)
        assert any(p["plugin_name"] == "计算器" for p in prompt_text)

    @staticmethod
    def test_plugin_processor_retrieved_info():
        plugins = [
            {
                "plugin_id": "plugin_001",
                "plugin_name": "天气查询",
                "plugin_desc": "查询天气",
                "tools": [
                    {
                        "tool_id": "tool_001",
                        "tool_name": "get_weather",
                        "desc": "获取天气",
                        "input_parameters": [{"name": "city", "type": 1}],
                        "output_parameters": [{"name": "weather", "type": 1}]
                    }
                ]
            }
        ]
        
        plugin_dict, tool_plugin_id_map = PluginProcessor.preprocess(plugins)
        
        tool_id_list = ["tool_001"]
        retrieved_plugin, retrieved_plugin_dict, retrieved_tool_id_map = (
            PluginProcessor.get_retrieved_info(
                tool_id_list,
                plugin_dict,
                tool_plugin_id_map,
                need_inputs_outputs=True
            )
        )
        
        assert len(retrieved_plugin) == 1
        assert retrieved_plugin[0]["tool_name"] == "get_weather"

    @staticmethod
    def test_plugin_processor_empty_and_none():
        result_empty = PluginProcessor.preprocess([])
        assert result_empty == ({}, {})
        
        result_none = PluginProcessor.preprocess(None)
        assert result_none == ({}, {})


class TestPromptIntegration:
    @staticmethod
    def test_retrieve_system_prompt_content():
        assert isinstance(RETRIEVE_SYSTEM_PROMPT, str)
        assert len(RETRIEVE_SYSTEM_PROMPT) > 0

    @staticmethod
    def test_retrieve_system_template_formatting():
        test_vars = {
            "dialog_history": "用户: 你好\n助手: 你好！",
            "plugin_info_list": "可用插件：天气查询、计算器",
        }
        
        formatted = RETRIEVE_SYSTEM_TEMPLATE.format(test_vars)
        
        assert formatted is not None

    @staticmethod
    def test_retrieve_system_template_to_messages():
        test_vars = {
            "dialog_history": "用户: 你好",
            "plugin_info_list": "可用插件：天气查询",
        }
        
        messages = RETRIEVE_SYSTEM_TEMPLATE.format(test_vars).to_messages()
        
        assert len(messages) > 0


class TestResourceRetrieverIntegration:
    @staticmethod
    def test_retriever_initialization():
        from openjiuwen.dev_tools.agent_builder.resource.retriever import ResourceRetriever
        
        mock_llm = Mock()
        
        with patch.object(ResourceRetriever, 'load_resources', return_value=[]):
            retriever = ResourceRetriever(mock_llm)
            
            assert retriever is not None
            assert retriever.llm is mock_llm

    @staticmethod
    def test_retriever_load_resources():
        from openjiuwen.dev_tools.agent_builder.resource.retriever import ResourceRetriever
        
        with patch('os.path.exists', return_value=False):
            result = ResourceRetriever.load_resources()
            
            assert result == []

    @staticmethod
    def test_retriever_retrieve_workflow():
        from openjiuwen.dev_tools.agent_builder.resource.retriever import ResourceRetriever
        
        mock_llm = Mock()
        
        with patch.object(ResourceRetriever, 'load_resources', return_value=[]):
            retriever = ResourceRetriever(mock_llm)
            
            dialog_history = [
                {"role": "user", "content": "创建一个天气查询助手"}
            ]
            
            with patch.object(retriever, '_llm_retrieve', return_value={"tool_id_list": []}):
                result = retriever.retrieve(dialog_history, for_workflow=False)
                
                assert isinstance(result, dict)
                assert "plugins" in result
                assert "plugin_dict" in result
                assert "tool_id_map" in result
