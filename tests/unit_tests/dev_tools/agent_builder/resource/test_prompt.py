# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
import pytest

from openjiuwen.core.foundation.llm import SystemMessage
from openjiuwen.core.foundation.prompt import PromptTemplate
from openjiuwen.dev_tools.agent_builder.resource.prompt import (
    RETRIEVE_SYSTEM_PROMPT,
    RETRIEVE_SYSTEM_TEMPLATE,
)


class TestRetrieveSystemPrompt:
    @staticmethod
    def test_prompt_is_string():
        assert isinstance(RETRIEVE_SYSTEM_PROMPT, str)
        assert len(RETRIEVE_SYSTEM_PROMPT) > 0

    @staticmethod
    def test_prompt_contains_key_sections():
        assert "人设" in RETRIEVE_SYSTEM_PROMPT
        assert "任务描述" in RETRIEVE_SYSTEM_PROMPT
        assert "输入信息" in RETRIEVE_SYSTEM_PROMPT
        assert "选择规则" in RETRIEVE_SYSTEM_PROMPT
        assert "输出格式" in RETRIEVE_SYSTEM_PROMPT

    @staticmethod
    def test_prompt_contains_placeholders():
        assert "{{dialog_history}}" in RETRIEVE_SYSTEM_PROMPT
        assert "{{plugin_info_list}}" in RETRIEVE_SYSTEM_PROMPT

    @staticmethod
    def test_prompt_contains_json_format():
        assert "tool_id_list" in RETRIEVE_SYSTEM_PROMPT
        assert "```json" in RETRIEVE_SYSTEM_PROMPT


class TestRetrieveSystemTemplate:
    @staticmethod
    def test_template_is_prompt_template():
        assert isinstance(RETRIEVE_SYSTEM_TEMPLATE, PromptTemplate)

    @staticmethod
    def test_template_format():
        result = RETRIEVE_SYSTEM_TEMPLATE.format({
            "dialog_history": "User: Hello",
            "plugin_info_list": "[{'plugin_name': 'Test'}]"
        })
        
        messages = result.to_messages()
        assert len(messages) > 0
        assert isinstance(messages[0], SystemMessage)

    @staticmethod
    def test_template_contains_formatted_content():
        result = RETRIEVE_SYSTEM_TEMPLATE.format({
            "dialog_history": "User: Test query",
            "plugin_info_list": "[{'plugin_name': 'Calculator'}]"
        })
        
        messages = result.to_messages()
        content = messages[0].content
        
        assert "User: Test query" in content
        assert "Calculator" in content
