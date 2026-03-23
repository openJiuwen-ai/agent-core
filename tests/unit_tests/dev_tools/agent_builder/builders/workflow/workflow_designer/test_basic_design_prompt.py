# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
import pytest

from openjiuwen.dev_tools.agent_builder.builders.workflow.workflow_designer.basic_design_prompt import (
    BASIC_DESIGN_SYSTEM_PROMPT,
    BASIC_DESIGN_USER_PROMPT_TEMPLATE,
)


class TestBasicDesignSystemPrompt:
    """Test BASIC_DESIGN_SYSTEM_PROMPT constant."""

    @staticmethod
    def test_is_string():
        """Test is string."""
        assert isinstance(BASIC_DESIGN_SYSTEM_PROMPT, str)
        assert len(BASIC_DESIGN_SYSTEM_PROMPT) > 0

    @staticmethod
    def test_contains_role():
        """Test contains role definition."""
        assert "角色定位" in BASIC_DESIGN_SYSTEM_PROMPT

    @staticmethod
    def test_contains_core_task():
        """Test contains core task."""
        assert "核心任务" in BASIC_DESIGN_SYSTEM_PROMPT

    @staticmethod
    def test_contains_input_analysis():
        """Test contains input analysis."""
        assert "输入需求分析" in BASIC_DESIGN_SYSTEM_PROMPT

    @staticmethod
    def test_contains_module_design():
        """Test contains module design."""
        assert "模块设计" in BASIC_DESIGN_SYSTEM_PROMPT

    @staticmethod
    def test_contains_api_usage():
        """Test contains API usage."""
        assert "API" in BASIC_DESIGN_SYSTEM_PROMPT

    @staticmethod
    def test_contains_output_format():
        """Test contains output format."""
        assert "输出格式规范" in BASIC_DESIGN_SYSTEM_PROMPT


class TestBasicDesignUserPromptTemplate:
    """Test BASIC_DESIGN_USER_PROMPT_TEMPLATE."""

    @staticmethod
    def test_template_exists():
        """Test template exists."""
        assert BASIC_DESIGN_USER_PROMPT_TEMPLATE is not None

    @staticmethod
    def test_template_has_content():
        """Test template has content."""
        assert hasattr(BASIC_DESIGN_USER_PROMPT_TEMPLATE, 'content')

    @staticmethod
    def test_template_format():
        """Test template can be formatted."""
        messages = BASIC_DESIGN_USER_PROMPT_TEMPLATE.format({
            "user_query": "create workflow",
            "tool_list": "tool1, tool2"
        }).to_messages()
        
        assert len(messages) > 0

    @staticmethod
    def test_template_contains_user_query():
        """Test template contains user_query placeholder."""
        messages = BASIC_DESIGN_USER_PROMPT_TEMPLATE.format({
            "user_query": "test query",
            "tool_list": ""
        }).to_messages()
        
        assert "test query" in messages[0].content

    @staticmethod
    def test_template_contains_tool_list():
        """Test template contains tool_list placeholder."""
        messages = BASIC_DESIGN_USER_PROMPT_TEMPLATE.format({
            "user_query": "",
            "tool_list": "test tool"
        }).to_messages()
        
        assert "test tool" in messages[0].content
