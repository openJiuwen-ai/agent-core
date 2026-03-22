# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""
System tests for Workflow prompts module.

Tests prompt templates and their integration.
"""
import pytest

from openjiuwen.dev_tools.agent_builder.builders.workflow.prompts import (
    CHECK_CYCLE_SYSTEM_PROMPT,
    CHECK_CYCLE_USER_PROMPT_TEMPLATE,
    EMPTY_RESOURCE_CONTENT,
    INITIAL_INTENTION_SYSTEM_PROMPT,
    INITIAL_INTENTION_USER_TEMPLATE,
    REFINE_INTENTION_SYSTEM_PROMPT,
    REFINE_INTENTION_USER_TEMPLATE,
)


class TestWorkflowPromptsIntegration:
    @staticmethod
    def test_initial_intention_system_prompt_content():
        assert isinstance(INITIAL_INTENTION_SYSTEM_PROMPT, str)
        assert len(INITIAL_INTENTION_SYSTEM_PROMPT) > 0
        assert "角色" in INITIAL_INTENTION_SYSTEM_PROMPT
        assert "判断规则" in INITIAL_INTENTION_SYSTEM_PROMPT
        assert "provide_process" in INITIAL_INTENTION_SYSTEM_PROMPT

    @staticmethod
    def test_initial_intention_user_template():
        assert INITIAL_INTENTION_USER_TEMPLATE is not None
        
        test_vars = {
            "dialog_history": "用户: 创建一个工作流\n助手: 请描述流程"
        }
        
        formatted = INITIAL_INTENTION_USER_TEMPLATE.format(test_vars)
        
        assert formatted is not None
        messages = formatted.to_messages()
        assert len(messages) > 0

    @staticmethod
    def test_refine_intention_system_prompt_content():
        assert isinstance(REFINE_INTENTION_SYSTEM_PROMPT, str)
        assert len(REFINE_INTENTION_SYSTEM_PROMPT) > 0
        assert "角色" in REFINE_INTENTION_SYSTEM_PROMPT
        assert "need_refined" in REFINE_INTENTION_SYSTEM_PROMPT

    @staticmethod
    def test_refine_intention_user_template():
        assert REFINE_INTENTION_USER_TEMPLATE is not None
        
        test_vars = {
            "dialog_history": "用户: 修改一下\n助手: 已修改",
            "mermaid_code": "A --> B"
        }
        
        formatted = REFINE_INTENTION_USER_TEMPLATE.format(test_vars)
        
        assert formatted is not None
        messages = formatted.to_messages()
        assert len(messages) > 0

    @staticmethod
    def test_empty_resource_content():
        assert isinstance(EMPTY_RESOURCE_CONTENT, str)
        assert len(EMPTY_RESOURCE_CONTENT) > 0
        assert "无可用工具" in EMPTY_RESOURCE_CONTENT

    @staticmethod
    def test_check_cycle_system_prompt_content():
        assert isinstance(CHECK_CYCLE_SYSTEM_PROMPT, str)
        assert len(CHECK_CYCLE_SYSTEM_PROMPT) > 0
        assert "角色设定" in CHECK_CYCLE_SYSTEM_PROMPT
        assert "need_refined" in CHECK_CYCLE_SYSTEM_PROMPT
        assert "loop_desc" in CHECK_CYCLE_SYSTEM_PROMPT

    @staticmethod
    def test_check_cycle_user_prompt_template():
        assert CHECK_CYCLE_USER_PROMPT_TEMPLATE is not None
        
        test_vars = {
            "mermaid_code": "A[开始] --> B[处理] --> C[结束]"
        }
        
        formatted = CHECK_CYCLE_USER_PROMPT_TEMPLATE.format(test_vars)
        
        assert formatted is not None
        messages = formatted.to_messages()
        assert len(messages) > 0


class TestPromptTemplateFormatting:
    @staticmethod
    def test_initial_intention_template_with_long_history():
        long_history = "\n".join([
            f"用户: 消息{i}\n助手: 回复{i}"
            for i in range(10)
        ])
        
        formatted = INITIAL_INTENTION_USER_TEMPLATE.format({
            "dialog_history": long_history
        })
        
        assert formatted is not None
        messages = formatted.to_messages()
        assert len(messages) > 0

    @staticmethod
    def test_refine_intention_template_with_complex_mermaid():
        complex_mermaid = """
        graph TD
            A[开始] --> B{判断}
            B -->|是| C[处理1]
            B -->|否| D[处理2]
            C --> E[结束]
            D --> E
        """
        
        formatted = REFINE_INTENTION_USER_TEMPLATE.format({
            "dialog_history": "用户: 创建工作流",
            "mermaid_code": complex_mermaid
        })
        
        assert formatted is not None
        messages = formatted.to_messages()
        assert len(messages) > 0

    @staticmethod
    def test_check_cycle_template_with_cycle():
        cycle_mermaid = "A[开始] --> B{判断} --不通过--> A"
        
        formatted = CHECK_CYCLE_USER_PROMPT_TEMPLATE.format({
            "mermaid_code": cycle_mermaid
        })
        
        assert formatted is not None
        messages = formatted.to_messages()
        assert len(messages) > 0


class TestPromptJsonFormat:
    @staticmethod
    def test_initial_intention_contains_json_format():
        assert '"provide_process": true' in INITIAL_INTENTION_SYSTEM_PROMPT
        assert '"provide_process": false' in INITIAL_INTENTION_SYSTEM_PROMPT

    @staticmethod
    def test_refine_intention_contains_json_format():
        assert '"need_refined": true' in REFINE_INTENTION_SYSTEM_PROMPT
        assert '"need_refined": false' in REFINE_INTENTION_SYSTEM_PROMPT

    @staticmethod
    def test_check_cycle_contains_json_format():
        assert '"need_refined": true' in CHECK_CYCLE_SYSTEM_PROMPT
        assert '"need_refined": false' in CHECK_CYCLE_SYSTEM_PROMPT
        assert '"loop_desc"' in CHECK_CYCLE_SYSTEM_PROMPT
