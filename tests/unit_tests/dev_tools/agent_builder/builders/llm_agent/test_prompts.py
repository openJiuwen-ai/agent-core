# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
from typing import Final

import pytest

from openjiuwen.dev_tools.agent_builder.builders.llm_agent.prompts import (
    FACTOR_SYSTEM_PROMPT,
    GENERATE_SYSTEM_PROMPT,
    GENERATE_USER_PROMPT_TEMPLATE,
    REFINE_INTENTION_SYSTEM_PROMPT,
    RESOURCE_SYSTEM_PROMPT,
    RESOURCE_USER_PROMPT_TEMPLATE,
    USER_INTENTION_PROMPT_TEMPLATE,
    USER_PROMPT_TEMPLATE,
)


class TestPromptsConstants:
    """Test prompts constants."""

    @staticmethod
    def test_factor_system_prompt_is_string():
        """Test FACTOR_SYSTEM_PROMPT is string."""
        assert isinstance(FACTOR_SYSTEM_PROMPT, str)
        assert len(FACTOR_SYSTEM_PROMPT) > 0

    @staticmethod
    def test_factor_system_prompt_contains_role():
        """Test FACTOR_SYSTEM_PROMPT contains role definition."""
        assert "角色" in FACTOR_SYSTEM_PROMPT or "Role" in FACTOR_SYSTEM_PROMPT.lower()

    @staticmethod
    def test_factor_system_prompt_contains_agent_types():
        """Test FACTOR_SYSTEM_PROMPT contains agent types."""
        agent_types = ["娱乐交互型", "创意生成型", "支持决策型", "执行任务型", "知识服务型", "对话交互型"]
        for agent_type in agent_types:
            assert agent_type in FACTOR_SYSTEM_PROMPT

    @staticmethod
    def test_resource_system_prompt_is_string():
        """Test RESOURCE_SYSTEM_PROMPT is string."""
        assert isinstance(RESOURCE_SYSTEM_PROMPT, str)
        assert len(RESOURCE_SYSTEM_PROMPT) > 0

    @staticmethod
    def test_resource_system_prompt_contains_resource_types():
        """Test RESOURCE_SYSTEM_PROMPT mentions resource types."""
        assert "插件" in RESOURCE_SYSTEM_PROMPT or "plugin" in RESOURCE_SYSTEM_PROMPT.lower()
        assert "知识库" in RESOURCE_SYSTEM_PROMPT or "knowledge" in RESOURCE_SYSTEM_PROMPT.lower()

    @staticmethod
    def test_generate_system_prompt_is_string():
        """Test GENERATE_SYSTEM_PROMPT is string."""
        assert isinstance(GENERATE_SYSTEM_PROMPT, str)
        assert len(GENERATE_SYSTEM_PROMPT) > 0

    @staticmethod
    def test_refine_intention_system_prompt_is_string():
        """Test REFINE_INTENTION_SYSTEM_PROMPT is string."""
        assert isinstance(REFINE_INTENTION_SYSTEM_PROMPT, str)
        assert len(REFINE_INTENTION_SYSTEM_PROMPT) > 0


class TestPromptTemplates:
    """Test prompt templates."""

    @staticmethod
    def test_user_prompt_template_has_content():
        """Test USER_PROMPT_TEMPLATE has content."""
        assert USER_PROMPT_TEMPLATE is not None
        assert hasattr(USER_PROMPT_TEMPLATE, 'content')

    @staticmethod
    def test_resource_user_prompt_template_has_content():
        """Test RESOURCE_USER_PROMPT_TEMPLATE has content."""
        assert RESOURCE_USER_PROMPT_TEMPLATE is not None
        assert hasattr(RESOURCE_USER_PROMPT_TEMPLATE, 'content')

    @staticmethod
    def test_generate_user_prompt_template_has_content():
        """Test GENERATE_USER_PROMPT_TEMPLATE has content."""
        assert GENERATE_USER_PROMPT_TEMPLATE is not None
        assert hasattr(GENERATE_USER_PROMPT_TEMPLATE, 'content')

    @staticmethod
    def test_user_intention_prompt_template_has_content():
        """Test USER_INTENTION_PROMPT_TEMPLATE has content."""
        assert USER_INTENTION_PROMPT_TEMPLATE is not None
        assert hasattr(USER_INTENTION_PROMPT_TEMPLATE, 'content')

    @staticmethod
    def test_user_prompt_template_format():
        """Test USER_PROMPT_TEMPLATE can be formatted."""
        messages = USER_PROMPT_TEMPLATE.format({
            "user_messages": "test query"
        }).to_messages()
        assert len(messages) > 0

    @staticmethod
    def test_resource_user_prompt_template_format():
        """Test RESOURCE_USER_PROMPT_TEMPLATE can be formatted."""
        messages = RESOURCE_USER_PROMPT_TEMPLATE.format({
            "agent_factor_info": "test factor",
            "resource": "test resource"
        }).to_messages()
        assert len(messages) > 0

    @staticmethod
    def test_generate_user_prompt_template_format():
        """Test GENERATE_USER_PROMPT_TEMPLATE can be formatted."""
        messages = GENERATE_USER_PROMPT_TEMPLATE.format({
            "user_message": "test message",
            "agent_config_info": "test config",
            "agent_resource_info": "test resource"
        }).to_messages()
        assert len(messages) > 0

    @staticmethod
    def test_user_intention_prompt_template_format():
        """Test USER_INTENTION_PROMPT_TEMPLATE can be formatted."""
        messages = USER_INTENTION_PROMPT_TEMPLATE.format({
            "dialog_history": "test history",
            "agent_config": "test config"
        }).to_messages()
        assert len(messages) > 0
