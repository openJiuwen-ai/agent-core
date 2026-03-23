# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
import re

import pytest

from openjiuwen.dev_tools.agent_builder.builders.llm_agent.generator import Generator


class TestGenerator:
    @staticmethod
    def test_extract_elements_definition():
        assert hasattr(Generator, "EXTRACT_ELEMENTS")
        elements = Generator.EXTRACT_ELEMENTS
        assert "name" in elements
        assert "description" in elements
        assert "prompt" in elements
        assert "opening_remarks" in elements
        assert "question" in elements

    @staticmethod
    def test_parse_info_with_valid_content():
        content = """
        <角色名称>测试助手</角色名称>
        <角色描述>这是一个测试助手</角色描述>
        <提示词>你是一个测试助手</提示词>
        <智能体开场白>你好！</智能体开场白>
        <预置问题>什么是测试？</预置问题>
        """
        
        result = Generator.parse_info(content)
        
        assert result["name"] == "测试助手"
        assert result["description"] == "这是一个测试助手"
        assert result["prompt"] == "你是一个测试助手"
        assert result["opening_remarks"] == "你好！"
        assert result["question"] == "什么是测试？"

    @staticmethod
    def test_parse_info_with_quoted_content():
        content = '<角色名称>"测试助手"</角色名称>'
        result = Generator.parse_info(content)
        assert result["name"] == "测试助手"

    @staticmethod
    def test_parse_info_with_missing_element():
        content = """
        <角色名称>测试助手</角色名称>
        <角色描述>这是一个测试助手</角色描述>
        """
        
        result = Generator.parse_info(content)
        
        assert result["name"] == "测试助手"
        assert result["description"] == "这是一个测试助手"
        assert result["prompt"] == ""
        assert result["opening_remarks"] == ""

    @staticmethod
    def test_parse_info_with_plugin_list():
        content = """
        <角色名称>测试助手</角色名称>
        <选择的插件列表>["plugin_001", "plugin_002"]</选择的插件列表>
        """
        
        result = Generator.parse_info(content)
        
        assert result["plugin"] == '["plugin_001", "plugin_002"]'

    @staticmethod
    def test_parse_info_with_knowledge_list():
        content = """
        <选择的知识库列表>["kb_001"]</选择的知识库列表>
        """
        
        result = Generator.parse_info(content)
        
        assert result["knowledge"] == '["kb_001"]'

    @staticmethod
    def test_parse_info_with_workflow_list():
        content = """
        <选择的工作流列表>["wf_001"]</选择的工作流列表>
        """
        
        result = Generator.parse_info(content)
        
        assert result["workflow"] == '["wf_001"]'

    @staticmethod
    def test_parse_info_empty_content():
        result = Generator.parse_info("")
        
        assert result["name"] == ""
        assert result["description"] == ""
        assert result["prompt"] == ""
        assert result["plugin"] == ""
        assert result["knowledge"] == ""
        assert result["workflow"] == ""

    @staticmethod
    def test_parse_info_multiline_content():
        content = """
        <提示词>你是一个测试助手。
        你可以帮助用户进行测试。
        请保持友好。</提示词>
        """
        
        result = Generator.parse_info(content)
        
        assert "你是一个测试助手" in result["prompt"]
        assert "你可以帮助用户进行测试" in result["prompt"]
