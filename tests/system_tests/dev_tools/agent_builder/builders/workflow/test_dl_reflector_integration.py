# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""
System tests for DL Reflector module.

Tests DL format validation and error detection.
"""
import pytest

from openjiuwen.dev_tools.agent_builder.builders.workflow.dl_reflector import (
    Reflector,
    extract_placeholder_content,
)


class TestExtractPlaceholderContent:
    @staticmethod
    def test_extract_single_placeholder():
        text = "Hello ${name}!"
        has_placeholder, matches = extract_placeholder_content(text)
        
        assert has_placeholder is True
        assert matches == ["name"]

    @staticmethod
    def test_extract_multiple_placeholders():
        text = "${node1.output} and ${node2.output}"
        has_placeholder, matches = extract_placeholder_content(text)
        
        assert has_placeholder is True
        assert matches == ["node1.output", "node2.output"]

    @staticmethod
    def test_no_placeholder():
        text = "No placeholder here"
        has_placeholder, matches = extract_placeholder_content(text)
        
        assert has_placeholder is False
        assert matches == []

    @staticmethod
    def test_empty_string():
        has_placeholder, matches = extract_placeholder_content("")
        
        assert has_placeholder is False
        assert matches == []


class TestReflectorIntegration:
    @pytest.fixture
    def reflector(self):
        return Reflector()

    @staticmethod
    def test_reflector_initialization(reflector):
        assert reflector is not None
        assert len(reflector.errors) == 0
        assert len(reflector.node_ids) == 0

    @staticmethod
    def test_reflector_available_node_types(reflector):
        expected_types = {
            'Start', 'End', 'Output', 'LLM', 'Questioner',
            'Plugin', 'Code', 'Branch', 'IntentDetection'
        }
        
        assert reflector.available_node_types == expected_types

    @staticmethod
    def test_reflector_available_variable_types(reflector):
        expected_types = {
            'String', 'Integer', 'Number', 'Boolean', 'Object',
            'Array<String>', 'Array<Integer>', 'Array<Number>',
            'Array<Boolean>', 'Array<Object>'
        }
        
        assert reflector.available_variable_types == expected_types

    @staticmethod
    def test_reflector_available_condition_operators(reflector):
        expected_operators = {
            "eq", "not_eq", "contain", "not_contain",
            "longer_than", "longer_than_or_eq",
            "short_than", "short_than_or_eq",
            "is_empty", "is_not_empty"
        }
        
        assert reflector.available_condition_operators == expected_operators

    @staticmethod
    def test_reflector_reset(reflector):
        reflector.errors = ["error1", "error2"]
        reflector.node_ids = ["node1", "node2"]
        
        reflector.reset()
        
        assert len(reflector.errors) == 0
        assert len(reflector.node_ids) == 0


class TestReflectorValidation:
    @pytest.fixture
    def reflector(self):
        return Reflector()

    @staticmethod
    def test_validate_invalid_json(reflector):
        invalid_json = "not a json"
        
        reflector.check_format(invalid_json)
        
        assert len(reflector.errors) > 0
        assert any("JSON" in err for err in reflector.errors)

    @staticmethod
    def test_validate_empty_json(reflector):
        reflector.check_format("[]")
        
        assert len(reflector.errors) == 0

    @staticmethod
    def test_validate_missing_required_fields(reflector):
        dl_content = '[{"id": "node1"}]'
        
        reflector.check_format(dl_content)
        
        assert len(reflector.errors) > 0

    @staticmethod
    def test_validate_invalid_node_type(reflector):
        dl_content = '''
        [{
            "id": "node1",
            "type": "InvalidType",
            "description": "test",
            "parameters": {}
        }]
        '''
        
        reflector.check_format(dl_content)
        
        assert len(reflector.errors) > 0
        assert any("类型错误" in err for err in reflector.errors)

    @staticmethod
    def test_validate_duplicate_node_id(reflector):
        dl_content = '''
        [
            {"id": "node1", "type": "Start", "description": "test", "parameters": {"outputs": []}},
            {"id": "node1", "type": "End", "description": "test", "parameters": {"inputs": []}}
        ]
        '''
        
        reflector.check_format(dl_content)
        
        assert len(reflector.errors) > 0
        assert any("已存在" in err for err in reflector.errors)


class TestReflectorStartNode:
    @pytest.fixture
    def reflector(self):
        return Reflector()

    @staticmethod
    def test_valid_start_node(reflector):
        dl_content = '''
        [{
            "id": "start",
            "type": "Start",
            "description": "开始节点",
            "parameters": {
                "outputs": [
                    {"name": "query", "description": "用户输入"}
                ]
            },
            "next": "end"
        },
        {
            "id": "end",
            "type": "End",
            "description": "结束节点",
            "parameters": {
                "inputs": [],
                "configs": {"template": ""}
            }
        }]
        '''
        
        reflector.check_format(dl_content)
        
        assert len(reflector.errors) == 0

    @staticmethod
    def test_start_node_missing_query_output(reflector):
        dl_content = '''
        [{
            "id": "start",
            "type": "Start",
            "description": "开始节点",
            "parameters": {
                "outputs": [
                    {"name": "other", "description": "其他输出"}
                ]
            },
            "next": "end"
        }]
        '''
        
        reflector.check_format(dl_content)
        
        assert len(reflector.errors) > 0
        assert any("query" in err for err in reflector.errors)

    @staticmethod
    def test_start_node_missing_next(reflector):
        dl_content = '''
        [{
            "id": "start",
            "type": "Start",
            "description": "开始节点",
            "parameters": {
                "outputs": [
                    {"name": "query", "description": "用户输入"}
                ]
            }
        }]
        '''
        
        reflector.check_format(dl_content)
        
        assert len(reflector.errors) > 0
        assert any("next" in err for err in reflector.errors)


class TestReflectorEndNode:
    @pytest.fixture
    def reflector(self):
        return Reflector()

    @staticmethod
    def test_valid_end_node(reflector):
        dl_content = '''
        [{
            "id": "end",
            "type": "End",
            "description": "结束节点",
            "parameters": {
                "inputs": [
                    {"name": "result", "value": "test"}
                ],
                "configs": {
                    "template": "结果: test"
                }
            }
        }]
        '''
        
        reflector.check_format(dl_content)
        
        assert len(reflector.errors) == 0

    @staticmethod
    def test_end_node_missing_configs(reflector):
        dl_content = '''
        [{
            "id": "end",
            "type": "End",
            "description": "结束节点",
            "parameters": {
                "inputs": [
                    {"name": "result", "value": "test"}
                ]
            }
        }]
        '''
        
        reflector.check_format(dl_content)
        
        assert len(reflector.errors) > 0
        assert any("configs" in err for err in reflector.errors)


class TestReflectorLLMNode:
    @pytest.fixture
    def reflector(self):
        return Reflector()

    @staticmethod
    def test_valid_llm_node(reflector):
        dl_content = '''
        [{
            "id": "llm1",
            "type": "LLM",
            "description": "LLM节点",
            "parameters": {
                "inputs": [
                    {"name": "prompt", "value": "test"}
                ],
                "outputs": [
                    {"name": "response", "description": "响应"}
                ],
                "configs": {
                    "system_prompt": "You are helpful",
                    "user_prompt": "Hello"
                }
            },
            "next": "end"
        },
        {
            "id": "end",
            "type": "End",
            "description": "结束节点",
            "parameters": {
                "inputs": [],
                "configs": {"template": ""}
            }
        }]
        '''
        
        reflector.check_format(dl_content)
        
        assert len(reflector.errors) == 0

    @staticmethod
    def test_llm_node_missing_required_configs(reflector):
        dl_content = '''
        [{
            "id": "llm1",
            "type": "LLM",
            "description": "LLM节点",
            "parameters": {
                "inputs": [],
                "outputs": [],
                "configs": {
                    "system_prompt": "You are helpful"
                }
            },
            "next": "end"
        }]
        '''
        
        reflector.check_format(dl_content)
        
        assert len(reflector.errors) > 0
        assert any("user_prompt" in err for err in reflector.errors)


class TestReflectorBranchNode:
    @pytest.fixture
    def reflector(self):
        return Reflector()

    @staticmethod
    def test_valid_branch_node(reflector):
        dl_content = '''
        [{
            "id": "start",
            "type": "Start",
            "description": "开始",
            "parameters": {
                "outputs": [
                    {"name": "query", "description": "用户输入"}
                ]
            },
            "next": "branch1"
        },
        {
            "id": "branch1",
            "type": "Branch",
            "description": "分支节点",
            "parameters": {
                "inputs": [
                    {"name": "input", "value": "${start.query}"}
                ],
                "conditions": [
                    {
                        "branch": "branch1",
                        "description": "条件1",
                        "expression": "${start.query} eq 'yes'",
                        "next": "end1"
                    },
                    {
                        "branch": "default",
                        "description": "默认",
                        "expression": "default",
                        "next": "end2"
                    }
                ]
            }
        },
        {
            "id": "end1",
            "type": "End",
            "description": "结束1",
            "parameters": {
                "inputs": [],
                "configs": {"template": ""}
            }
        },
        {
            "id": "end2",
            "type": "End",
            "description": "结束2",
            "parameters": {
                "inputs": [],
                "configs": {"template": ""}
            }
        }]
        '''
        
        reflector.check_format(dl_content)
        
        assert len(reflector.errors) == 0

    @staticmethod
    def test_branch_node_missing_default_branch(reflector):
        dl_content = '''
        [{
            "id": "branch1",
            "type": "Branch",
            "description": "分支节点",
            "parameters": {
                "inputs": [],
                "conditions": [
                    {
                        "branch": "branch1",
                        "description": "条件1",
                        "expression": "'test' eq 'yes'",
                        "next": "end1"
                    }
                ]
            }
        }]
        '''
        
        reflector.check_format(dl_content)
        
        assert len(reflector.errors) > 0
        assert any("default" in err for err in reflector.errors)


class TestReflectorIntentDetectionNode:
    @pytest.fixture
    def reflector(self):
        return Reflector()

    @staticmethod
    def test_valid_intent_detection_node(reflector):
        dl_content = '''
        [{
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
                        "next": "end1"
                    },
                    {
                        "branch": "default",
                        "description": "默认",
                        "expression": "default",
                        "next": "end2"
                    }
                ]
            }
        },
        {
            "id": "end1",
            "type": "End",
            "description": "结束1",
            "parameters": {
                "inputs": [],
                "configs": {"template": ""}
            }
        },
        {
            "id": "end2",
            "type": "End",
            "description": "结束2",
            "parameters": {
                "inputs": [],
                "configs": {"template": ""}
            }
        }]
        '''
        
        reflector.check_format(dl_content)
        
        assert len(reflector.errors) == 0

    @staticmethod
    def test_intent_detection_missing_conditions(reflector):
        dl_content = '''
        [{
            "id": "intent1",
            "type": "IntentDetection",
            "description": "意图检测节点",
            "parameters": {
                "inputs": [],
                "configs": {
                    "prompt": "检测意图"
                }
            }
        }]
        '''
        
        reflector.check_format(dl_content)
        
        assert len(reflector.errors) > 0
        assert any("conditions" in err for err in reflector.errors)


class TestReflectorNonExistentNodeReference:
    @pytest.fixture
    def reflector(self):
        return Reflector()

    @staticmethod
    def test_reference_to_non_existent_node(reflector):
        dl_content = '''
        [{
            "id": "start",
            "type": "Start",
            "description": "开始",
            "parameters": {
                "outputs": [
                    {"name": "query", "description": "用户输入"}
                ]
            },
            "next": "non_existent_node"
        }]
        '''
        
        reflector.check_format(dl_content)
        
        assert len(reflector.errors) > 0
        assert any("不存在" in err for err in reflector.errors)
