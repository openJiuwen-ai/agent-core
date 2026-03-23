# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
import json

import pytest

from openjiuwen.dev_tools.agent_builder.builders.llm_agent.template import LLM_AGENT_TEMPLATE
from openjiuwen.dev_tools.agent_builder.builders.llm_agent.transformer import Transformer


class TestTransformer:
    @pytest.fixture
    def transformer(self):
        return Transformer()

    @pytest.fixture
    def sample_plugin_dict(self):
        return {
            "plugin_001": {
                "plugin_id": "plugin_001",
                "plugin_name": "Test Plugin",
                "plugin_desc": "A test plugin",
                "plugin_version": "1.0.0",
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
                            {"name": "result", "desc": "Result", "type": 1}
                        ]
                    }
                }
            }
        }

    @pytest.fixture
    def sample_tool_id_map(self):
        return {"tool_001": "plugin_001"}

    @pytest.fixture
    def sample_workflow_dict(self):
        return {
            "wf_001": {
                "workflow_id": "wf_001",
                "workflow_name": "Test Workflow",
                "workflow_version": "1.0.0",
                "workflow_desc": "A test workflow",
                "input_parameters": [],
                "output_parameters": []
            }
        }

    @staticmethod
    def test_collect_plugin(transformer, sample_plugin_dict, sample_tool_id_map):
        result = Transformer.collect_plugin(
            ["tool_001"],
            sample_plugin_dict,
            sample_tool_id_map
        )
        
        assert len(result) == 1
        assert result[0]["plugin_id"] == "plugin_001"
        assert result[0]["tool_id"] == "tool_001"
        assert result[0]["tool_name"] == "Test Tool"

    @staticmethod
    def test_collect_plugin_empty_list(transformer, sample_plugin_dict, sample_tool_id_map):
        result = Transformer.collect_plugin(
            [],
            sample_plugin_dict,
            sample_tool_id_map
        )
        assert result == []

    @staticmethod
    def test_collect_plugin_invalid_tool_id(transformer, sample_plugin_dict, sample_tool_id_map):
        result = Transformer.collect_plugin(
            ["invalid_tool"],
            sample_plugin_dict,
            sample_tool_id_map
        )
        assert result == []

    @staticmethod
    def test_collect_workflow(transformer, sample_workflow_dict):
        result = Transformer.collect_workflow(
            ["wf_001"],
            sample_workflow_dict
        )
        
        assert len(result) == 1
        assert result[0]["workflow_id"] == "wf_001"
        assert result[0]["workflow_name"] == "Test Workflow"

    @staticmethod
    def test_collect_workflow_empty_list(transformer, sample_workflow_dict):
        result = Transformer.collect_workflow([], sample_workflow_dict)
        assert result == []

    @staticmethod
    def test_collect_workflow_invalid_id_returns_entry(transformer, sample_workflow_dict):
        result = Transformer.collect_workflow(["invalid_id"], sample_workflow_dict)
        assert len(result) == 1
        assert result[0]["workflow_id"] == "invalid_id"

    @staticmethod
    def test_convert_input_parameters(transformer):
        params = [
            {"name": "p1", "desc": "Param 1", "type": 1, "value": "v1"},
            {"name": "p2", "description": "Param 2", "type": 2}
        ]
        
        result = Transformer.convert_input_parameters(params)
        
        assert len(result) == 2
        assert result[0]["name"] == "p1"
        assert result[0]["desc"] == "Param 1"
        assert result[0]["type"] == 1
        assert result[1]["desc"] == "Param 2"

    @staticmethod
    def test_convert_output_parameters(transformer):
        params = [
            {"name": "result", "desc": "Output result", "type": 1}
        ]
        
        result = Transformer.convert_output_parameters(params)
        
        assert len(result) == 1
        assert result[0]["name"] == "result"
        assert result[0]["is_runtime"] == False

    @staticmethod
    def test_build_plugin_dependencies(transformer, sample_plugin_dict, sample_tool_id_map):
        current_ts = 1700000000000
        
        result = Transformer.build_plugin_dependencies(
            ["tool_001"],
            sample_plugin_dict,
            sample_tool_id_map,
            current_ts
        )
        
        assert len(result) == 1
        assert result[0]["plugin_id"] == "plugin_001"
        assert result[0]["plugin_version"] == "1.0.0"
        assert result[0]["name"] == "Test Plugin"
        assert len(result[0]["tool_list"]) == 1
        assert result[0]["create_time"] == current_ts

    @staticmethod
    def test_build_workflow_dependencies(transformer, sample_workflow_dict):
        current_ts = 1700000000000
        
        result = Transformer.build_workflow_dependencies(
            ["wf_001"],
            sample_workflow_dict,
            current_ts
        )
        
        assert len(result) == 1
        assert result[0]["workflow_id"] == "wf_001"
        assert result[0]["workflow_version"] == "1.0.0"
        assert result[0]["name"] == "Test Workflow"
        assert result[0]["create_time"] == current_ts

    @staticmethod
    def test_transform_to_dsl(transformer, sample_plugin_dict, sample_tool_id_map, sample_workflow_dict):
        agent_info = {
            "name": "Test Agent",
            "description": "A test agent",
            "prompt": "You are a test agent.",
            "opening_remarks": "Hello!",
            "plugin": ["tool_001"],
            "workflow": ["wf_001"]
        }
        
        resource = {
            "plugin_dict": sample_plugin_dict,
            "tool_id_map": sample_tool_id_map,
            "workflow_dict": sample_workflow_dict
        }
        
        result = transformer.transform_to_dsl(agent_info, resource)
        
        dsl = json.loads(result)
        
        assert dsl["name"] == "Test Agent"
        assert dsl["description"] == "A test agent"
        assert dsl["configs"]["system_prompt"] == "You are a test agent."
        assert dsl["opening_remarks"] == "Hello!"
        assert len(dsl["plugins"]) == 1
        assert len(dsl["workflows"]) == 1
        assert "dependencies" in dsl
        assert dsl["agent_id"] != ""
        assert dsl["create_time"] is not None
        assert dsl["update_time"] is not None

    @staticmethod
    def test_transform_to_dsl_without_plugins(transformer):
        agent_info = {
            "name": "Simple Agent",
            "description": "A simple agent",
            "prompt": "You are a simple agent.",
            "opening_remarks": "Hi!",
            "plugin": [],
            "workflow": []
        }
        
        resource = {
            "plugin_dict": {},
            "tool_id_map": {},
            "workflow_dict": {}
        }
        
        result = transformer.transform_to_dsl(agent_info, resource)
        
        dsl = json.loads(result)
        
        assert dsl["name"] == "Simple Agent"
        assert dsl["plugins"] == []
        assert dsl["workflows"] == []

    @staticmethod
    def test_transform_to_dsl_generates_valid_json(transformer):
        agent_info = {
            "name": "Test",
            "description": "Test",
            "prompt": "Test",
            "opening_remarks": "Test",
            "plugin": [],
            "workflow": []
        }
        
        resource = {
            "plugin_dict": {},
            "tool_id_map": {},
            "workflow_dict": {}
        }
        
        result = transformer.transform_to_dsl(agent_info, resource)
        
        parsed = json.loads(result)
        assert isinstance(parsed, dict)
        assert "agent_id" in parsed
        assert "name" in parsed
