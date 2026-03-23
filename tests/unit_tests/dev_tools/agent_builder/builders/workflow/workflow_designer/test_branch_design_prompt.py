# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
import pytest

from openjiuwen.dev_tools.agent_builder.builders.workflow.workflow_designer.branch_design_prompt import (
    BRANCH_DESIGN_SYSTEM_PROMPT,
    BRANCH_DESIGN_USER_PROMPT_TEMPLATE,
)


class TestBranchDesignSystemPrompt:
    """Test BRANCH_DESIGN_SYSTEM_PROMPT constant."""

    @staticmethod
    def test_is_string():
        """Test is string."""
        assert isinstance(BRANCH_DESIGN_SYSTEM_PROMPT, str)
        assert len(BRANCH_DESIGN_SYSTEM_PROMPT) > 0

    @staticmethod
    def test_contains_role():
        """Test contains role definition."""
        assert "角色定位" in BRANCH_DESIGN_SYSTEM_PROMPT

    @staticmethod
    def test_contains_core_task():
        """Test contains core task."""
        assert "核心任务" in BRANCH_DESIGN_SYSTEM_PROMPT

    @staticmethod
    def test_contains_branch_design():
        """Test contains branch design."""
        assert "分支设计" in BRANCH_DESIGN_SYSTEM_PROMPT

    @staticmethod
    def test_contains_decision_principles():
        """Test contains decision principles."""
        assert "分流决策原则" in BRANCH_DESIGN_SYSTEM_PROMPT or "决策原则" in BRANCH_DESIGN_SYSTEM_PROMPT

    @staticmethod
    def test_contains_output_format():
        """Test contains output format."""
        assert "输出格式规范" in BRANCH_DESIGN_SYSTEM_PROMPT

    @staticmethod
    def test_contains_must_branch():
        """Test contains must branch conditions."""
        assert "必须设计分支" in BRANCH_DESIGN_SYSTEM_PROMPT

    @staticmethod
    def test_contains_forbidden_branch():
        """Test contains forbidden branch conditions."""
        assert "禁止设计分支" in BRANCH_DESIGN_SYSTEM_PROMPT


class TestBranchDesignUserPromptTemplate:
    """Test BRANCH_DESIGN_USER_PROMPT_TEMPLATE."""

    @staticmethod
    def test_template_exists():
        """Test template exists."""
        assert BRANCH_DESIGN_USER_PROMPT_TEMPLATE is not None

    @staticmethod
    def test_template_has_content():
        """Test template has content."""
        assert hasattr(BRANCH_DESIGN_USER_PROMPT_TEMPLATE, 'content')

    @staticmethod
    def test_template_format():
        """Test template can be formatted."""
        messages = BRANCH_DESIGN_USER_PROMPT_TEMPLATE.format({
            "user_query": "create workflow",
            "basic_design": "basic design result"
        }).to_messages()
        
        assert len(messages) > 0

    @staticmethod
    def test_template_contains_user_query():
        """Test template contains user_query placeholder."""
        messages = BRANCH_DESIGN_USER_PROMPT_TEMPLATE.format({
            "user_query": "test query",
            "basic_design": ""
        }).to_messages()
        
        assert "test query" in messages[0].content

    @staticmethod
    def test_template_contains_basic_design():
        """Test template contains basic_design placeholder."""
        messages = BRANCH_DESIGN_USER_PROMPT_TEMPLATE.format({
            "user_query": "",
            "basic_design": "test design"
        }).to_messages()
        
        assert "test design" in messages[0].content
