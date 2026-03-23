# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""
System tests for DL Transformer Converters module.

Tests DL transformer converters integration.
"""
import pytest

from openjiuwen.dev_tools.agent_builder.builders.workflow.dl_transformer.converters import (
    BranchConverter,
    CodeConverter,
    EndConverter,
    IntentDetectionConverter,
    LLMConverter,
    OutputConverter,
    PluginConverter,
    QuestionerConverter,
    StartConverter,
)
from openjiuwen.dev_tools.agent_builder.builders.workflow.dl_transformer.dl_transformer import DLTransformer
from openjiuwen.dev_tools.agent_builder.builders.workflow.dl_transformer.models import NodeType


class TestDLTransformerRegistryIntegration:
    """Test DLTransformer registry integration."""

    @staticmethod
    def test_registry_contains_all_types():
        """Test registry contains all node types."""
        expected_types = {
            'Start', 'End', 'LLM', 'IntentDetection',
            'Questioner', 'Code', 'Plugin', 'Output', 'Branch'
        }
        
        assert set(DLTransformer.get_dsl_converter_registry().keys()) == expected_types

    @staticmethod
    def test_registry_values_are_classes():
        """Test registry values are classes."""
        for converter_class in DLTransformer.get_dsl_converter_registry().values():
            assert isinstance(converter_class, type)


class TestStartConverterIntegration:
    """Test StartConverter integration."""

    @staticmethod
    def test_start_converter_creation():
        """Test StartConverter creation."""
        node_data = {
            "id": "node_start",
            "type": "Start",
            "description": "开始节点",
            "parameters": {
                "outputs": [{"name": "query", "description": "用户输入"}]
            },
            "next": "node_end"
        }
        
        converter = StartConverter(node_data, {})
        
        assert converter.node_data == node_data

    @staticmethod
    def test_start_converter_convert():
        """Test StartConverter convert."""
        node_data = {
            "id": "node_start",
            "type": "Start",
            "description": "开始节点",
            "parameters": {
                "outputs": [{"name": "query", "description": "用户输入"}]
            },
            "next": "node_end"
        }
        
        converter = StartConverter(node_data, {})
        converter.convert()
        
        assert converter.node.id == "node_start"
        assert converter.node.type == NodeType.Start.dsl_type


class TestEndConverterIntegration:
    """Test EndConverter integration."""

    @staticmethod
    def test_end_converter_creation():
        """Test EndConverter creation."""
        node_data = {
            "id": "node_end",
            "type": "End",
            "description": "结束节点",
            "parameters": {
                "inputs": [],
                "configs": {"template": "{{result}}"}
            }
        }
        
        converter = EndConverter(node_data, {})
        
        assert converter.node_data == node_data

    @staticmethod
    def test_end_converter_convert():
        """Test EndConverter convert."""
        node_data = {
            "id": "node_end",
            "type": "End",
            "description": "结束节点",
            "parameters": {
                "inputs": [],
                "configs": {"template": "{{result}}"}
            }
        }
        
        converter = EndConverter(node_data, {})
        converter.convert()
        
        assert converter.node.id == "node_end"
        assert converter.node.type == NodeType.End.dsl_type


class TestLLMConverterIntegration:
    """Test LLMConverter integration."""

    @staticmethod
    def test_llm_converter_creation():
        """Test LLMConverter creation."""
        node_data = {
            "id": "node_llm",
            "type": "LLM",
            "description": "大模型节点",
            "parameters": {
                "inputs": [{"name": "query", "value": "${node_start.query}"}],
                "outputs": [{"name": "output", "description": "输出"}],
                "configs": {
                    "system_prompt": "You are helpful",
                    "user_prompt": "{{query}}"
                }
            },
            "next": "node_end"
        }
        
        converter = LLMConverter(node_data, {})
        
        assert converter.node_data == node_data


class TestBranchConverterIntegration:
    """Test BranchConverter integration."""

    @staticmethod
    def test_branch_converter_creation():
        """Test BranchConverter creation."""
        node_data = {
            "id": "node_branch",
            "type": "Branch",
            "description": "分支节点",
            "parameters": {
                "conditions": [
                    {"branch": "branch_1", "description": "条件1", "next": "node_1"},
                    {"branch": "branch_2", "description": "条件2", "next": "node_2"}
                ]
            }
        }
        
        converter = BranchConverter(node_data, {})
        
        assert converter.node_data == node_data


class TestPluginConverterIntegration:
    """Test PluginConverter integration."""

    @staticmethod
    def test_plugin_converter_creation():
        """Test PluginConverter creation."""
        node_data = {
            "id": "node_plugin",
            "type": "Plugin",
            "description": "插件节点",
            "parameters": {
                "plugin_id": "plugin_1",
                "tool_id": "tool_1",
                "inputs": [],
                "outputs": []
            },
            "next": "node_end"
        }
        
        converter = PluginConverter(node_data, {})
        
        assert converter.node_data == node_data


class TestCodeConverterIntegration:
    """Test CodeConverter integration."""

    @staticmethod
    def test_code_converter_creation():
        """Test CodeConverter creation."""
        node_data = {
            "id": "node_code",
            "type": "Code",
            "description": "代码节点",
            "parameters": {
                "language": "python",
                "code": "print('hello')",
                "inputs": [],
                "outputs": []
            },
            "next": "node_end"
        }
        
        converter = CodeConverter(node_data, {})
        
        assert converter.node_data == node_data


class TestQuestionerConverterIntegration:
    """Test QuestionerConverter integration."""

    @staticmethod
    def test_questioner_converter_creation():
        """Test QuestionerConverter creation."""
        node_data = {
            "id": "node_questioner",
            "type": "Questioner",
            "description": "提问节点",
            "parameters": {
                "question": "请问有什么可以帮助您？",
                "outputs": [{"name": "answer", "description": "用户回答"}]
            },
            "next": "node_end"
        }
        
        converter = QuestionerConverter(node_data, {})
        
        assert converter.node_data == node_data


class TestIntentDetectionConverterIntegration:
    """Test IntentDetectionConverter integration."""

    @staticmethod
    def test_intent_detection_converter_creation():
        """Test IntentDetectionConverter creation."""
        node_data = {
            "id": "node_intent",
            "type": "IntentDetection",
            "description": "意图识别节点",
            "parameters": {
                "conditions": [
                    {"branch": "branch_1", "description": "查询", "next": "node_1"},
                    {"branch": "branch_2", "description": "闲聊", "next": "node_2"}
                ]
            }
        }
        
        converter = IntentDetectionConverter(node_data, {})
        
        assert converter.node_data == node_data


class TestOutputConverterIntegration:
    """Test OutputConverter integration."""

    @staticmethod
    def test_output_converter_creation():
        """Test OutputConverter creation."""
        node_data = {
            "id": "node_output",
            "type": "Output",
            "description": "输出节点",
            "parameters": {
                "inputs": [],
                "outputs": []
            },
            "next": "node_end"
        }
        
        converter = OutputConverter(node_data, {})
        
        assert converter.node_data == node_data
