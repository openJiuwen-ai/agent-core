# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
import pytest

from openjiuwen.dev_tools.agent_builder.builders.workflow.workflow_designer.reflection_evaluate_prompt import (
    REFLECTION_EVALUATE_SYSTEM_PROMPT,
    REFLECTION_EVALUATE_USER_PROMPT_TEMPLATE,
)


class TestReflectionEvaluateSystemPrompt:
    """Test REFLECTION_EVALUATE_SYSTEM_PROMPT constant."""

    @staticmethod
    def test_is_string():
        """Test is string."""
        assert isinstance(REFLECTION_EVALUATE_SYSTEM_PROMPT, str)
        assert len(REFLECTION_EVALUATE_SYSTEM_PROMPT) > 0

    @staticmethod
    def test_contains_role():
        """Test contains role definition."""
        assert "角色定位" in REFLECTION_EVALUATE_SYSTEM_PROMPT

    @staticmethod
    def test_contains_core_task():
        """Test contains core task."""
        assert "核心任务" in REFLECTION_EVALUATE_SYSTEM_PROMPT

    @staticmethod
    def test_contains_evaluation_rules():
        """Test contains evaluation rules."""
        assert "评估" in REFLECTION_EVALUATE_SYSTEM_PROMPT

    @staticmethod
    def test_contains_input_evaluation():
        """Test contains input evaluation."""
        assert "输入评估" in REFLECTION_EVALUATE_SYSTEM_PROMPT

    @staticmethod
    def test_contains_module_evaluation():
        """Test contains module evaluation."""
        assert "模块" in REFLECTION_EVALUATE_SYSTEM_PROMPT

    @staticmethod
    def test_contains_branch_evaluation():
        """Test contains branch evaluation."""
        assert "分支" in REFLECTION_EVALUATE_SYSTEM_PROMPT

    @staticmethod
    def test_contains_output_format():
        """Test contains output format."""
        assert "输出格式" in REFLECTION_EVALUATE_SYSTEM_PROMPT


class TestReflectionEvaluateUserPromptTemplate:
    """Test REFLECTION_EVALUATE_USER_PROMPT_TEMPLATE."""

    @staticmethod
    def test_template_exists():
        """Test template exists."""
        assert REFLECTION_EVALUATE_USER_PROMPT_TEMPLATE is not None

    @staticmethod
    def test_template_has_content():
        """Test template has content."""
        assert hasattr(REFLECTION_EVALUATE_USER_PROMPT_TEMPLATE, 'content')

    @staticmethod
    def test_template_format():
        """Test template can be formatted."""
        messages = REFLECTION_EVALUATE_USER_PROMPT_TEMPLATE.format({
            "user_query": "create workflow",
            "basic_design": "basic design result",
            "branch_design": "branch design result"
        }).to_messages()
        
        assert len(messages) > 0

    @staticmethod
    def test_template_contains_user_query():
        """Test template contains user_query placeholder."""
        messages = REFLECTION_EVALUATE_USER_PROMPT_TEMPLATE.format({
            "user_query": "test query",
            "basic_design": "",
            "branch_design": ""
        }).to_messages()
        
        assert "test query" in messages[0].content

    @staticmethod
    def test_template_contains_basic_design():
        """Test template contains basic_design placeholder."""
        messages = REFLECTION_EVALUATE_USER_PROMPT_TEMPLATE.format({
            "user_query": "",
            "basic_design": "test basic",
            "branch_design": ""
        }).to_messages()
        
        assert "test basic" in messages[0].content

    @staticmethod
    def test_template_contains_branch_design():
        """Test template contains branch_design placeholder."""
        messages = REFLECTION_EVALUATE_USER_PROMPT_TEMPLATE.format({
            "user_query": "",
            "basic_design": "",
            "branch_design": "test branch"
        }).to_messages()
        
        assert "test branch" in messages[0].content
