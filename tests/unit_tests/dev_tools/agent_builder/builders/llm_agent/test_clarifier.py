# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
import pytest

from openjiuwen.dev_tools.agent_builder.builders.llm_agent.clarifier import (
    RESOURCE_CONFIG,
    Clarifier,
)


class TestResourceConfig:
    @staticmethod
    def test_resource_config_has_plugin():
        assert "plugin" in RESOURCE_CONFIG
        assert RESOURCE_CONFIG["plugin"]["label"] == "插件"
        assert RESOURCE_CONFIG["plugin"]["id_key"] == "tool_id"
        assert RESOURCE_CONFIG["plugin"]["name_key"] == "tool_name"
        assert RESOURCE_CONFIG["plugin"]["desc_key"] == "tool_desc"

    @staticmethod
    def test_resource_config_has_knowledge():
        assert "knowledge" in RESOURCE_CONFIG
        assert RESOURCE_CONFIG["knowledge"]["label"] == "知识库"

    @staticmethod
    def test_resource_config_has_workflow():
        assert "workflow" in RESOURCE_CONFIG
        assert RESOURCE_CONFIG["workflow"]["label"] == "工作流"


class TestClarifierParseResourceOutput:
    @staticmethod
    def test_parse_resource_output_no_section():
        resource_output = "Some text without resource planning section"
        available_resources = {"plugins": []}
        
        display, id_dict = Clarifier.parse_resource_output(
            resource_output,
            available_resources
        )
        
        assert display == ""
        assert id_dict == {}

    @staticmethod
    def test_parse_resource_output_with_plugins():
        resource_output = """
        ## Agent资源规划
        
        【选择的插件】
        [{"tool_id": "tool_001", "tool_name": "Test Tool", "tool_desc": "A test tool"}]
        """
        available_resources = {
            "plugins": [
                {"tool_id": "tool_001", "tool_name": "Test Tool", "tool_desc": "A test tool"}
            ]
        }
        
        display, id_dict = Clarifier.parse_resource_output(
            resource_output,
            available_resources
        )
        
        assert "插件" in display
        assert "tool_001" in id_dict.get("plugin", [])

    @staticmethod
    def test_parse_resource_output_with_invalid_tool_id():
        resource_output = """
        ## Agent资源规划
        
        【选择的插件】
        [{"tool_id": "invalid_tool", "tool_name": "Invalid", "tool_desc": "Invalid tool"}]
        """
        available_resources = {
            "plugins": [
                {"tool_id": "tool_001", "tool_name": "Valid Tool", "tool_desc": "A valid tool"}
            ]
        }
        
        display, id_dict = Clarifier.parse_resource_output(
            resource_output,
            available_resources
        )
        
        assert "plugin" not in id_dict or "invalid_tool" not in id_dict.get("plugin", [])

    @staticmethod
    def test_parse_resource_output_with_knowledge():
        resource_output = """
        ## Agent资源规划
        
        【选择的知识库】
        [{"knowledge_id": "kb_001", "knowledge_name": "Test KB", "knowledge_desc": "A test KB"}]
        """
        available_resources = {
            "knowledge": [
                {"knowledge_id": "kb_001", "knowledge_name": "Test KB", "knowledge_desc": "A test KB"}
            ]
        }
        
        display, id_dict = Clarifier.parse_resource_output(
            resource_output,
            available_resources
        )
        
        assert "知识库" in display or "kb_001" in id_dict.get("knowledge", [])

    @staticmethod
    def test_parse_resource_output_with_workflow():
        resource_output = """
        ## Agent资源规划
        
        【选择的工作流】
        [{"workflow_id": "wf_001", "workflow_name": "Test WF", "workflow_desc": "A test WF"}]
        """
        available_resources = {
            "workflow": [
                {"workflow_id": "wf_001", "workflow_name": "Test WF", "workflow_desc": "A test WF"}
            ]
        }
        
        display, id_dict = Clarifier.parse_resource_output(
            resource_output,
            available_resources
        )
        
        assert "工作流" in display or "wf_001" in id_dict.get("workflow", [])

    @staticmethod
    def test_parse_resource_output_empty_list():
        resource_output = """
        ## Agent资源规划
        
        【选择的插件】
        []
        """
        available_resources = {"plugins": []}
        
        display, id_dict = Clarifier.parse_resource_output(
            resource_output,
            available_resources
        )
        
        assert "plugin" not in id_dict or id_dict["plugin"] == []
