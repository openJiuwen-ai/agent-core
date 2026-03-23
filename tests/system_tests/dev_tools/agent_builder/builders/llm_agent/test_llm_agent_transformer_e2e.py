# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""
System tests for LLM Agent Transformer module.

Tests end-to-end DSL transformation integration.
"""
import json

import pytest

from openjiuwen.dev_tools.agent_builder.builders.llm_agent.template import LLM_AGENT_TEMPLATE
from openjiuwen.dev_tools.agent_builder.builders.llm_agent.transformer import Transformer


class TestTransformerIntegration:
    @pytest.fixture
    def transformer(self):
        return Transformer()

    @pytest.fixture
    def sample_plugin_dict(self):
        return {
            "plugin_001": {
                "plugin_id": "plugin_001",
                "plugin_name": "Weather Plugin",
                "plugin_desc": "Get weather information",
                "plugin_version": "1.0.0",
                "tools": {
                    "tool_001": {
                        "tool_id": "tool_001",
                        "tool_name": "Get Weather",
                        "tool_desc": "Get current weather",
                        "code": "def get_weather(city): return f'Weather in {city}'",
                        "language": "python",
                        "input_parameters": [
                            {"name": "city", "desc": "City name", "type": 1, "value": "", "is_required": True}
                        ],
                        "output_parameters": [
                            {"name": "result", "desc": "Weather result", "type": 1}
                        ]
                    },
                    "tool_002": {
                        "tool_id": "tool_002",
                        "tool_name": "Get Forecast",
                        "tool_desc": "Get weather forecast",
                        "code": "def get_forecast(city, days): return f'Forecast for {city}'",
                        "language": "python",
                        "input_parameters": [
                            {"name": "city", "desc": "City name", "type": 1},
                            {"name": "days", "desc": "Number of days", "type": 2}
                        ],
                        "output_parameters": [
                            {"name": "forecast", "desc": "Forecast result", "type": 1}
                        ]
                    }
                }
            },
            "plugin_002": {
                "plugin_id": "plugin_002",
                "plugin_name": "Calculator Plugin",
                "plugin_desc": "Perform calculations",
                "plugin_version": "2.0.0",
                "tools": {
                    "tool_003": {
                        "tool_id": "tool_003",
                        "tool_name": "Calculate",
                        "tool_desc": "Perform calculation",
                        "code": "def calculate(expr): return eval(expr)",
                        "language": "python",
                        "input_parameters": [
                            {"name": "expression", "desc": "Math expression", "type": 1}
                        ],
                        "output_parameters": [
                            {"name": "result", "desc": "Calculation result", "type": 2}
                        ]
                    }
                }
            }
        }

    @pytest.fixture
    def sample_tool_id_map(self):
        return {
            "tool_001": "plugin_001",
            "tool_002": "plugin_001",
            "tool_003": "plugin_002"
        }

    @pytest.fixture
    def sample_workflow_dict(self):
        return {
            "wf_001": {
                "workflow_id": "wf_001",
                "workflow_name": "Data Processing",
                "workflow_version": "1.0.0",
                "workflow_desc": "Process and transform data",
                "input_parameters": [
                    {"name": "input_data", "desc": "Input data", "type": 1}
                ],
                "output_parameters": [
                    {"name": "output_data", "desc": "Output data", "type": 1}
                ]
            },
            "wf_002": {
                "workflow_id": "wf_002",
                "workflow_name": "Report Generation",
                "workflow_version": "2.0.0",
                "workflow_desc": "Generate reports",
                "input_parameters": [],
                "output_parameters": []
            }
        }


class TestTransformerEndToEnd(TestTransformerIntegration):
    @staticmethod
    def test_transform_to_dsl_complete_flow(transformer,
         sample_plugin_dict, sample_tool_id_map, sample_workflow_dict):
        agent_info = {
            "name": "Smart Assistant",
            "description": "An intelligent assistant with weather and calculation capabilities",
            "opening_remarks": "Hello! How can I help you today?",
            "plugin": ["tool_001", "tool_003"],
            "workflow": ["wf_001"]
        }
        
        resource = {
            "plugin_dict": sample_plugin_dict,
            "tool_id_map": sample_tool_id_map,
            "workflow_dict": sample_workflow_dict
        }
        
        result = transformer.transform_to_dsl(agent_info, resource)
        
        dsl = json.loads(result)
        
        assert dsl["name"] == "Smart Assistant"
        assert dsl["description"] == "An intelligent assistant with weather and calculation capabilities"
        assert dsl["opening_remarks"] == "Hello! How can I help you today?"
        
        assert len(dsl["plugins"]) == 2
        plugin_ids = [p["plugin_id"] for p in dsl["plugins"]]
        assert "plugin_001" in plugin_ids
        assert "plugin_002" in plugin_ids
        
        assert len(dsl["workflows"]) == 1
        assert dsl["workflows"][0]["workflow_id"] == "wf_001"
        
        assert "dependencies" in dsl
        assert len(dsl["dependencies"]["plugins"]) == 2
        assert len(dsl["dependencies"]["workflows"]) == 1
        
        assert dsl["agent_id"] != ""
        assert dsl["agent_type"] == "react"
        assert dsl["create_time"] is not None
        assert dsl["update_time"] is not None

    @staticmethod
    def test_transform_to_dsl_with_multiple_tools_same_plugin(transformer,
             sample_plugin_dict, sample_tool_id_map, sample_workflow_dict):
        agent_info = {
            "name": "Weather Expert",
            "description": "Weather specialist",
            "prompt": "You are a weather expert.",
            "opening_remarks": "Ask me about weather!",
            "plugin": ["tool_001", "tool_002"],
            "workflow": []
        }
        
        resource = {
            "plugin_dict": sample_plugin_dict,
            "tool_id_map": sample_tool_id_map,
            "workflow_dict": sample_workflow_dict
        }
        
        result = transformer.transform_to_dsl(agent_info, resource)
        
        dsl = json.loads(result)
        
        assert len(dsl["plugins"]) == 2
        plugin_names = [p["plugin_name"] for p in dsl["plugins"]]
        assert "Weather Plugin" in plugin_names
        
        assert len(dsl["dependencies"]["plugins"]) == 1
        assert dsl["dependencies"]["plugins"][0]["plugin_id"] == "plugin_001"

    @staticmethod
    def test_transform_to_dsl_minimal_agent(transformer):
        agent_info = {
            "name": "Simple Agent",
            "description": "A simple agent",
            "prompt": "You are simple.",
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
        assert dsl["dependencies"]["plugins"] == []
        assert dsl["dependencies"]["workflows"] == []

    @staticmethod
    def test_transform_to_dsl_generates_unique_agent_ids(transformer):
        agent_info = {
            "name": "Test Agent",
            "description": "Test",
            "prompt": "Test prompt",
            "opening_remarks": "Hello",
            "plugin": [],
            "workflow": []
        }
        
        resource = {
            "plugin_dict": {},
            "tool_id_map": {},
            "workflow_dict": {}
        }
        
        result1 = transformer.transform_to_dsl(agent_info, resource)
        result2 = transformer.transform_to_dsl(agent_info, resource)
        
        dsl1 = json.loads(result1)
        dsl2 = json.loads(result2)
        
        assert dsl1["agent_id"] != dsl2["agent_id"]


class TestTransformerWithTemplateIntegration(TestTransformerIntegration):
    @staticmethod
    def test_dsl_inherits_template_structure(transformer):
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
        dsl = json.loads(result)
        
        for key in LLM_AGENT_TEMPLATE.keys():
            assert key in dsl, f"Key '{key}' from template missing in DSL"
        
        assert "constraints" in dsl
        assert "max_iterations" in dsl["constraints"]
        assert "memory" in dsl
        assert "max_tokens" in dsl["memory"]
        assert "model" in dsl
        assert "model_info" in dsl["model"]

    @staticmethod
    def test_dsl_template_values_preserved(transformer):
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
        dsl = json.loads(result)
        
        assert dsl["agent_type"] == "react"
        assert dsl["edit_mode"] == "manual"
        assert dsl["constraints"]["max_iterations"] == 5
        assert dsl["memory"]["max_tokens"] == 1000


class TestTransformerPluginDependenciesIntegration(TestTransformerIntegration):
    @staticmethod
    def test_build_plugin_dependencies_complete_metadata(transformer, sample_plugin_dict, sample_tool_id_map):
        current_ts = 1700000000000
        
        dependencies = Transformer.build_plugin_dependencies(
            ["tool_001", "tool_003"],
            sample_plugin_dict,
            sample_tool_id_map,
            current_ts
        )
        
        assert len(dependencies) == 2
        
        weather_dep = next(d for d in dependencies if d["plugin_id"] == "plugin_001")
        assert weather_dep["name"] == "Weather Plugin"
        assert weather_dep["desc"] == "Get weather information"
        assert weather_dep["plugin_version"] == "1.0.0"
        assert weather_dep["create_time"] == current_ts
        assert weather_dep["update_time"] == current_ts
        assert len(weather_dep["tool_list"]) == 1
        
        calc_dep = next(d for d in dependencies if d["plugin_id"] == "plugin_002")
        assert calc_dep["name"] == "Calculator Plugin"
        assert len(calc_dep["tool_list"]) == 1

    @staticmethod
    def test_build_plugin_dependencies_tool_conversion(transformer, sample_plugin_dict, sample_tool_id_map):
        current_ts = 1700000000000
        
        dependencies = Transformer.build_plugin_dependencies(
            ["tool_001"],
            sample_plugin_dict,
            sample_tool_id_map,
            current_ts
        )
        
        assert len(dependencies) == 1
        tool = dependencies[0]["tool_list"][0]
        
        assert tool["tool_id"] == "tool_001"
        assert tool["name"] == "Get Weather"
        assert tool["desc"] == "Get current weather"
        assert tool["language"] == "python"
        assert tool["plugin_id"] == "plugin_001"
        assert tool["available"] is True
        assert len(tool["input_parameters"]) == 1
        assert tool["input_parameters"][0]["name"] == "city"

    @staticmethod
    def test_build_plugin_dependencies_handles_missing_tool(transformer, sample_plugin_dict, sample_tool_id_map):
        current_ts = 1700000000000
        
        dependencies = Transformer.build_plugin_dependencies(
            ["non_existent_tool"],
            sample_plugin_dict,
            sample_tool_id_map,
            current_ts
        )
        
        assert len(dependencies) == 0


class TestTransformerWorkflowDependenciesIntegration(TestTransformerIntegration):
    @staticmethod
    def test_build_workflow_dependencies_complete_metadata(transformer, sample_workflow_dict):
        current_ts = 1700000000000
        
        dependencies = Transformer.build_workflow_dependencies(
            ["wf_001", "wf_002"],
            sample_workflow_dict,
            current_ts
        )
        
        assert len(dependencies) == 2
        
        wf1 = next(d for d in dependencies if d["workflow_id"] == "wf_001")
        assert wf1["name"] == "Data Processing"
        assert wf1["desc"] == "Process and transform data"
        assert wf1["workflow_version"] == "1.0.0"
        assert len(wf1["input_parameters"]) == 1
        assert len(wf1["output_parameters"]) == 1
        
        wf2 = next(d for d in dependencies if d["workflow_id"] == "wf_002")
        assert wf2["name"] == "Report Generation"

    @staticmethod
    def test_build_workflow_dependencies_handles_missing_workflow(transformer, sample_workflow_dict):
        current_ts = 1700000000000
        
        dependencies = Transformer.build_workflow_dependencies(
            ["non_existent_wf"],
            sample_workflow_dict,
            current_ts
        )
        
        assert len(dependencies) == 1
        assert dependencies[0]["workflow_id"] == "non_existent_wf"
        assert dependencies[0]["name"] == ""


class TestTransformerJSONOutputIntegration(TestTransformerIntegration):
    @staticmethod
    def test_output_is_valid_json(transformer, sample_plugin_dict, sample_tool_id_map, sample_workflow_dict):
        agent_info = {
            "name": "JSON Test Agent",
            "description": "Test JSON output",
            "prompt": "Test prompt with special chars: \n\t\"quotes\"",
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
        
        parsed = json.loads(result)
        assert isinstance(parsed, dict)

    @staticmethod
    def test_output_handles_unicode(transformer):
        agent_info = {
            "name": "中文助手",
            "description": "这是一个中文描述，包含特殊字符：😊🎉",
            "prompt": "你是一个智能助手。",
            "opening_remarks": "你好！欢迎使用！",
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
        
        assert dsl["name"] == "中文助手"
        assert "中文描述" in dsl["description"]
        assert "😊" in dsl["description"]
        assert dsl["opening_remarks"] == "你好！欢迎使用！"


class TestTransformerCollectMethodsIntegration(TestTransformerIntegration):
    @staticmethod
    def test_collect_plugin_integration(transformer, sample_plugin_dict, sample_tool_id_map):
        result = Transformer.collect_plugin(
            ["tool_001", "tool_002", "tool_003"],
            sample_plugin_dict,
            sample_tool_id_map
        )
        
        assert len(result) == 3
        
        tool_001_info = next(r for r in result if r["tool_id"] == "tool_001")
        assert tool_001_info["plugin_id"] == "plugin_001"
        assert tool_001_info["plugin_name"] == "Weather Plugin"
        assert tool_001_info["tool_name"] == "Get Weather"

    @staticmethod
    def test_collect_workflow_integration(transformer, sample_workflow_dict):
        result = Transformer.collect_workflow(
            ["wf_001", "wf_002"],
            sample_workflow_dict
        )
        
        assert len(result) == 2
        
        wf1 = next(r for r in result if r["workflow_id"] == "wf_001")
        assert wf1["workflow_name"] == "Data Processing"
        assert wf1["workflow_version"] == "1.0.0"
        assert wf1["description"] == "Process and transform data"
