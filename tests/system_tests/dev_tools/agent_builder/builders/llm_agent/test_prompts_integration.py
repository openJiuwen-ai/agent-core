# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""
System tests for LLM Agent Prompts module.

Tests prompt templates integration.
"""
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


class TestPromptsIntegration:
    """Test prompts integration."""

    @staticmethod
    def test_factor_system_prompt_content():
        """Test FACTOR_SYSTEM_PROMPT content."""
        assert "角色" in FACTOR_SYSTEM_PROMPT
        assert "娱乐交互型" in FACTOR_SYSTEM_PROMPT
        assert "创意生成型" in FACTOR_SYSTEM_PROMPT
        assert "支持决策型" in FACTOR_SYSTEM_PROMPT
        assert "执行任务型" in FACTOR_SYSTEM_PROMPT
        assert "知识服务型" in FACTOR_SYSTEM_PROMPT
        assert "对话交互型" in FACTOR_SYSTEM_PROMPT

    @staticmethod
    def test_resource_system_prompt_content():
        """Test RESOURCE_SYSTEM_PROMPT content."""
        assert "插件" in RESOURCE_SYSTEM_PROMPT
        assert "知识库" in RESOURCE_SYSTEM_PROMPT

    @staticmethod
    def test_generate_system_prompt_content():
        """Test GENERATE_SYSTEM_PROMPT content."""
        assert len(GENERATE_SYSTEM_PROMPT) > 0

    @staticmethod
    def test_refine_intention_system_prompt_content():
        """Test REFINE_INTENTION_SYSTEM_PROMPT content."""
        assert len(REFINE_INTENTION_SYSTEM_PROMPT) > 0


class TestPromptTemplatesIntegration:
    """Test prompt templates integration."""

    @staticmethod
    def test_user_prompt_template_format():
        """Test USER_PROMPT_TEMPLATE format."""
        messages = USER_PROMPT_TEMPLATE.format({
            "user_messages": "test query"
        }).to_messages()
        
        assert len(messages) > 0
        assert "test query" in messages[0].content

    @staticmethod
    def test_resource_user_prompt_template_format():
        """Test RESOURCE_USER_PROMPT_TEMPLATE format."""
        messages = RESOURCE_USER_PROMPT_TEMPLATE.format({
            "agent_factor_info": "factor info",
            "resource": "resource info"
        }).to_messages()
        
        assert len(messages) > 0
        assert "factor info" in messages[0].content
        assert "resource info" in messages[0].content

    @staticmethod
    def test_generate_user_prompt_template_format():
        """Test GENERATE_USER_PROMPT_TEMPLATE format."""
        messages = GENERATE_USER_PROMPT_TEMPLATE.format({
            "user_message": "user message",
            "agent_config_info": "config info",
            "agent_resource_info": "resource info"
        }).to_messages()
        
        assert len(messages) > 0
        assert "user message" in messages[0].content

    @staticmethod
    def test_user_intention_prompt_template_format():
        """Test USER_INTENTION_PROMPT_TEMPLATE format."""
        messages = USER_INTENTION_PROMPT_TEMPLATE.format({
            "query": "test query",
            "agent_config_info": "config info"
        }).to_messages()
        
        assert len(messages) > 0
        assert "test query" in messages[0].content


class TestPromptTemplateConsistency:
    """Test prompt template consistency."""

    @staticmethod
    def test_all_templates_exist():
        """Test all templates exist."""
        assert USER_PROMPT_TEMPLATE is not None
        assert RESOURCE_USER_PROMPT_TEMPLATE is not None
        assert GENERATE_USER_PROMPT_TEMPLATE is not None
        assert USER_INTENTION_PROMPT_TEMPLATE is not None

    @staticmethod
    def test_all_system_prompts_exist():
        """Test all system prompts exist."""
        assert FACTOR_SYSTEM_PROMPT is not None
        assert RESOURCE_SYSTEM_PROMPT is not None
        assert GENERATE_SYSTEM_PROMPT is not None
        assert REFINE_INTENTION_SYSTEM_PROMPT is not None
