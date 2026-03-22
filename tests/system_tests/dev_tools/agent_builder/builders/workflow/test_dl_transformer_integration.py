# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""
System tests for DL Transformer converters module.

Tests DL to DSL transformation converters.
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
from openjiuwen.dev_tools.agent_builder.builders.workflow.dl_transformer.models import (
    Edge,
    Node,
    NodeType,
    Position,
)


class TestDLTransformerIntegration:
    @staticmethod
    def test_transformer_initialization():
        transformer = DLTransformer()
        
        assert transformer is not None
        assert len(transformer.get_dsl_converter_registry()) > 0

    @staticmethod
    def test_transformer_registry_contains_all_types():
        expected_types = {
            'Start', 'End', 'LLM', 'IntentDetection',
            'Questioner', 'Code', 'Plugin', 'Output', 'Branch'
        }
        
        assert set(DLTransformer.get_dsl_converter_registry().keys()) == expected_types

    @staticmethod
    def test_transform_to_mermaid_simple():
        dl_content = '''
        [
            {"id": "start", "type": "Start", "description": "开始", "parameters": {"outputs": [{"name": "query", "description": "用户输入"}]}},
            {"id": "end", "type": "End", "description": "结束", "parameters": {"inputs": [], "configs": {"template": ""}}}
        ]
        '''
        
        mermaid = DLTransformer.transform_to_mermaid(dl_content)
        
        assert mermaid is not None
        assert isinstance(mermaid, str)
        assert "graph TD" in mermaid or "flowchart" in mermaid.lower()

    @staticmethod
    def test_transform_to_mermaid_with_branch():
        dl_content = '''
        [
            {"id": "start", "type": "Start", "description": "开始", "parameters": {"outputs": []}, "next": "branch1"},
            {"id": "branch1", "type": "Branch", "description": "分支", "parameters": {"inputs": [], "conditions": [{"branch": "b1", "expression": "default", "next": "end"}]}},
            {"id": "end", "type": "End", "description": "结束", "parameters": {"inputs": [], "configs": {"template": ""}}}
        ]
        '''
        
        mermaid = DLTransformer.transform_to_mermaid(dl_content)
        
        assert mermaid is not None
        assert isinstance(mermaid, str)

    @staticmethod
    def test_transform_to_dsl_simple():
        dl_content = '''
        [
            {"id": "start", "type": "Start", "description": "开始", "parameters": {"outputs": [{"name": "query", "description": "用户输入"}]}},
            {"id": "end", "type": "End", "description": "结束", "parameters": {"inputs": [], "configs": {"template": ""}}}
        ]
        '''
        
        transformer = DLTransformer()
        dsl = transformer.transform_to_dsl(dl_content)
        
        assert dsl is not None
        assert isinstance(dsl, str)

    @staticmethod
    def test_transform_to_dsl_with_resource():
        dl_content = '''
        [
            {"id": "start", "type": "Start", "description": "开始", "parameters": {"outputs": []}},
            {"id": "end", "type": "End", "description": "结束", "parameters": {"inputs": [], "configs": {"template": ""}}}
        ]
        '''
        
        resource = {
            "plugins": [{"tool_id": "tool1"}],
            "plugin_dict": {},
            "tool_id_map": {}
        }
        
        transformer = DLTransformer()
        dsl = transformer.transform_to_dsl(dl_content, resource)
        
        assert dsl is not None

    @staticmethod
    def test_transform_invalid_json_raises_error():
        invalid_content = "not a valid json"
        
        with pytest.raises(Exception):
            DLTransformer.transform_to_mermaid(invalid_content)

    @staticmethod
    def test_transform_non_array_raises_error():
        non_array_content = '{"key": "value"}'
        
        with pytest.raises(ValueError):
            DLTransformer.transform_to_mermaid(non_array_content)


class TestCollectPlugin:
    @staticmethod
    def test_collect_plugin_empty():
        result = DLTransformer.collect_plugin([], {}, {})
        
        assert result == []

    @staticmethod
    def test_collect_plugin_single():
        tool_id_list = ["tool1"]
        plugin_dict = {
            "plugin1": {
                "plugin_name": "测试插件",
                "plugin_version": "1.0.0",
                "tools": {
                    "tool1": {
                        "tool_name": "测试工具",
                        "ori_inputs": [],
                        "ori_outputs": []
                    }
                }
            }
        }
        tool_id_map = {"tool1": "plugin1"}
        
        result = DLTransformer.collect_plugin(tool_id_list, plugin_dict, tool_id_map)
        
        assert len(result) == 1
        assert result[0]["tool_id"] == "tool1"
        assert result[0]["plugin_name"] == "测试插件"

    @staticmethod
    def test_collect_plugin_missing_tool_id():
        tool_id_list = ["non_existent"]
        plugin_dict = {}
        tool_id_map = {}
        
        result = DLTransformer.collect_plugin(tool_id_list, plugin_dict, tool_id_map)
        
        assert len(result) == 0


class TestStartConverter:
    @staticmethod
    def test_start_converter_initialization():
        node_data = {
            "id": "start",
            "type": "Start",
            "description": "开始节点",
            "parameters": {"outputs": []}
        }
        
        converter = StartConverter(node_data, {}, position=Position(0, 0))
        
        assert converter is not None
        assert converter.node.id == "start"

    @staticmethod
    def test_start_converter_convert():
        node_data = {
            "id": "start",
            "type": "Start",
            "description": "开始节点",
            "parameters": {
                "outputs": [
                    {"name": "query", "description": "用户输入"}
                ]
            },
            "next": "next_node"
        }
        
        converter = StartConverter(node_data, {}, position=Position(0, 0))
        converter.convert()
        
        assert converter.node is not None
        assert len(converter.edges) == 1
        assert converter.edges[0].target_node_id == "next_node"


class TestEndConverter:
    @staticmethod
    def test_end_converter_initialization():
        node_data = {
            "id": "end",
            "type": "End",
            "description": "结束节点",
            "parameters": {"inputs": [], "configs": {"template": ""}}
        }
        
        converter = EndConverter(node_data, {}, position=Position(0, 0))
        
        assert converter is not None
        assert converter.node.id == "end"

    @staticmethod
    def test_end_converter_convert():
        node_data = {
            "id": "end",
            "type": "End",
            "description": "结束节点",
            "parameters": {
                "inputs": [
                    {"name": "result", "value": "test"}
                ],
                "configs": {
                    "template": "结果: ${result}"
                }
            }
        }
        
        converter = EndConverter(node_data, {}, position=Position(0, 0))
        converter.convert()
        
        assert converter.node is not None
        assert len(converter.edges) == 0


class TestLLMConverter:
    @staticmethod
    def test_llm_converter_initialization():
        node_data = {
            "id": "llm1",
            "type": "LLM",
            "description": "LLM节点",
            "parameters": {
                "inputs": [],
                "outputs": [],
                "configs": {"system_prompt": "", "user_prompt": ""}
            }
        }
        
        converter = LLMConverter(node_data, {}, position=Position(0, 0))
        
        assert converter is not None
        assert converter.node.id == "llm1"

    @staticmethod
    def test_llm_converter_convert():
        node_data = {
            "id": "llm1",
            "type": "LLM",
            "description": "LLM节点",
            "parameters": {
                "inputs": [
                    {"name": "prompt", "value": "Hello"}
                ],
                "outputs": [
                    {"name": "response", "description": "响应"}
                ],
                "configs": {
                    "system_prompt": "You are helpful",
                    "user_prompt": "${prompt}"
                }
            },
            "next": "end"
        }
        
        converter = LLMConverter(node_data, {}, position=Position(0, 0))
        converter.convert()
        
        assert converter.node is not None
        assert len(converter.edges) == 1


class TestBranchConverter:
    @staticmethod
    def test_branch_converter_initialization():
        node_data = {
            "id": "branch1",
            "type": "Branch",
            "description": "分支节点",
            "parameters": {
                "inputs": [],
                "conditions": []
            }
        }
        
        converter = BranchConverter(node_data, {}, position=Position(0, 0))
        
        assert converter is not None
        assert converter.node.id == "branch1"

    @staticmethod
    def test_branch_converter_convert():
        node_data = {
            "id": "branch1",
            "type": "Branch",
            "description": "分支节点",
            "parameters": {
                "inputs": [
                    {"name": "input", "value": "test"}
                ],
                "conditions": [
                    {
                        "branch": "branch1",
                        "description": "条件1",
                        "expression": "'test' eq 'yes'",
                        "next": "node1"
                    },
                    {
                        "branch": "default",
                        "description": "默认",
                        "expression": "default",
                        "next": "node2"
                    }
                ]
            }
        }
        
        converter = BranchConverter(node_data, {}, position=Position(0, 0))
        converter.convert()
        
        assert converter.node is not None
        assert len(converter.edges) == 2


class TestIntentDetectionConverter:
    @staticmethod
    def test_intent_detection_converter_initialization():
        node_data = {
            "id": "intent1",
            "type": "IntentDetection",
            "description": "意图检测节点",
            "parameters": {
                "inputs": [],
                "configs": {"prompt": ""},
                "conditions": []
            }
        }
        
        converter = IntentDetectionConverter(node_data, {}, position=Position(0, 0))
        
        assert converter is not None
        assert converter.node.id == "intent1"

    @staticmethod
    def test_intent_detection_converter_convert():
        node_data = {
            "id": "intent1",
            "type": "IntentDetection",
            "description": "意图检测节点",
            "parameters": {
                "inputs": [
                    {"name": "input", "value": "test"}
                ],
                "configs": {
                    "prompt": "检测意图"
                },
                "conditions": [
                    {
                        "branch": "intent1",
                        "description": "意图1",
                        "expression": "${intent1.rawOutput} contain 'intent1'",
                        "next": "node1"
                    },
                    {
                        "branch": "default",
                        "description": "默认",
                        "expression": "default",
                        "next": "node2"
                    }
                ]
            }
        }
        
        converter = IntentDetectionConverter(node_data, {}, position=Position(0, 0))
        converter.convert()
        
        assert converter.node is not None
        assert len(converter.edges) == 2


class TestPluginConverter:
    @staticmethod
    def test_plugin_converter_initialization():
        node_data = {
            "id": "plugin1",
            "type": "Plugin",
            "description": "插件节点",
            "parameters": {
                "inputs": [],
                "outputs": [],
                "configs": {"tool_id": "tool1"}
            }
        }
        
        converter = PluginConverter(node_data, {}, resource={}, position=Position(0, 0))
        
        assert converter is not None
        assert converter.node.id == "plugin1"


class TestCodeConverter:
    @staticmethod
    def test_code_converter_initialization():
        node_data = {
            "id": "code1",
            "type": "Code",
            "description": "代码节点",
            "parameters": {
                "inputs": [],
                "outputs": [],
                "configs": {"code": ""}
            }
        }
        
        converter = CodeConverter(node_data, {}, position=Position(0, 0))
        
        assert converter is not None
        assert converter.node.id == "code1"


class TestQuestionerConverter:
    @staticmethod
    def test_questioner_converter_initialization():
        node_data = {
            "id": "q1",
            "type": "Questioner",
            "description": "提问节点",
            "parameters": {
                "inputs": [],
                "outputs": [],
                "configs": {"prompt": ""}
            }
        }
        
        converter = QuestionerConverter(node_data, {}, position=Position(0, 0))
        
        assert converter is not None
        assert converter.node.id == "q1"


class TestOutputConverter:
    @staticmethod
    def test_output_converter_initialization():
        node_data = {
            "id": "output1",
            "type": "Output",
            "description": "输出节点",
            "parameters": {
                "inputs": [],
                "configs": {"template": ""}
            }
        }
        
        converter = OutputConverter(node_data, {}, position=Position(0, 0))
        
        assert converter is not None
        assert converter.node.id == "output1"
