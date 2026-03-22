# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""
System tests for Workflow Designer Prompts module.

Tests workflow designer prompts integration.
"""
import pytest

from openjiuwen.dev_tools.agent_builder.builders.workflow.workflow_designer.basic_design_prompt import (
    BASIC_DESIGN_SYSTEM_PROMPT,
    BASIC_DESIGN_USER_PROMPT_TEMPLATE,
)
from openjiuwen.dev_tools.agent_builder.builders.workflow.workflow_designer.branch_design_prompt import (
    BRANCH_DESIGN_SYSTEM_PROMPT,
    BRANCH_DESIGN_USER_PROMPT_TEMPLATE,
)
from openjiuwen.dev_tools.agent_builder.builders.workflow.workflow_designer.reflection_evaluate_prompt import (
    REFLECTION_EVALUATE_SYSTEM_PROMPT,
    REFLECTION_EVALUATE_USER_PROMPT_TEMPLATE,
)


class TestBasicDesignPromptIntegration:
    """Test basic design prompt integration."""

    @staticmethod
    def test_system_prompt_content():
        """Test BASIC_DESIGN_SYSTEM_PROMPT content."""
        assert "角色定位" in BASIC_DESIGN_SYSTEM_PROMPT
        assert "核心任务" in BASIC_DESIGN_SYSTEM_PROMPT
        assert "输入需求分析" in BASIC_DESIGN_SYSTEM_PROMPT
        assert "模块设计" in BASIC_DESIGN_SYSTEM_PROMPT
        assert "API" in BASIC_DESIGN_SYSTEM_PROMPT
        assert "输出格式规范" in BASIC_DESIGN_SYSTEM_PROMPT

    @staticmethod
    def test_user_prompt_template_format():
        """Test BASIC_DESIGN_USER_PROMPT_TEMPLATE format."""
        messages = BASIC_DESIGN_USER_PROMPT_TEMPLATE.format({
            "user_query": "create workflow",
            "tool_list": "tool1, tool2"
        }).to_messages()
        
        assert len(messages) > 0
        assert "create workflow" in messages[0].content
        assert "tool1, tool2" in messages[0].content


class TestBranchDesignPromptIntegration:
    """Test branch design prompt integration."""

    @staticmethod
    def test_system_prompt_content():
        """Test BRANCH_DESIGN_SYSTEM_PROMPT content."""
        assert "角色定位" in BRANCH_DESIGN_SYSTEM_PROMPT
        assert "核心任务" in BRANCH_DESIGN_SYSTEM_PROMPT
        assert "分支设计" in BRANCH_DESIGN_SYSTEM_PROMPT
        assert "输出格式规范" in BRANCH_DESIGN_SYSTEM_PROMPT

    @staticmethod
    def test_user_prompt_template_format():
        """Test BRANCH_DESIGN_USER_PROMPT_TEMPLATE format."""
        messages = BRANCH_DESIGN_USER_PROMPT_TEMPLATE.format({
            "user_query": "create workflow",
            "basic_design": "basic design result"
        }).to_messages()
        
        assert len(messages) > 0
        assert "create workflow" in messages[0].content
        assert "basic design result" in messages[0].content


class TestReflectionEvaluatePromptIntegration:
    """Test reflection evaluate prompt integration."""

    @staticmethod
    def test_system_prompt_content():
        """Test REFLECTION_EVALUATE_SYSTEM_PROMPT content."""
        assert "角色定位" in REFLECTION_EVALUATE_SYSTEM_PROMPT
        assert "核心任务" in REFLECTION_EVALUATE_SYSTEM_PROMPT
        assert "评估" in REFLECTION_EVALUATE_SYSTEM_PROMPT
        assert "输出格式" in REFLECTION_EVALUATE_SYSTEM_PROMPT

    @staticmethod
    def test_user_prompt_template_format():
        """Test REFLECTION_EVALUATE_USER_PROMPT_TEMPLATE format."""
        messages = REFLECTION_EVALUATE_USER_PROMPT_TEMPLATE.format({
            "user_query": "create workflow",
            "basic_design": "basic design",
            "branch_design": "branch design"
        }).to_messages()
        
        assert len(messages) > 0
        assert "create workflow" in messages[0].content
        assert "basic design" in messages[0].content
        assert "branch design" in messages[0].content


class TestPromptTemplateConsistency:
    """Test prompt template consistency."""

    @staticmethod
    def test_all_system_prompts_exist():
        """Test all system prompts exist."""
        assert BASIC_DESIGN_SYSTEM_PROMPT is not None
        assert BRANCH_DESIGN_SYSTEM_PROMPT is not None
        assert REFLECTION_EVALUATE_SYSTEM_PROMPT is not None

    @staticmethod
    def test_all_user_templates_exist():
        """Test all user templates exist."""
        assert BASIC_DESIGN_USER_PROMPT_TEMPLATE is not None
        assert BRANCH_DESIGN_USER_PROMPT_TEMPLATE is not None
        assert REFLECTION_EVALUATE_USER_PROMPT_TEMPLATE is not None
