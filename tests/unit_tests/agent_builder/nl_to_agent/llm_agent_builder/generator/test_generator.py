#!/usr/bin/env python
# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
import unittest
from unittest.mock import Mock, patch

from openjiuwen.agent_builder.nl_to_agent.llm_agent_builder.generator.generator import Generator
from openjiuwen.core.foundation.llm import SystemMessage, HumanMessage


class TestGenerator(unittest.TestCase):

    def setUp(self):
        self.mock_llm = Mock()
        self.generator = Generator(self.mock_llm)

    def test_parse_info_with_complete_content(self):
        content = """
        <角色名称>旅行助手</角色名称>
        <角色描述>专业的旅行规划助手</角色描述>
        <智能体开场白>你好，我是旅行助手！</智能体开场白>
        <预置问题>["推荐旅行目的地", "行程规划建议"]</预置问题>
        <提示词>你是一个旅行助手，帮助用户规划行程。</提示词>
        """

        result = Generator._parse_info(content)

        expected = {
            "name": "旅行助手",
            "description": "专业的旅行规划助手",
            "opening_remarks": "你好，我是旅行助手！",
            "question": "[\"推荐旅行目的地\", \"行程规划建议\"]",
            "prompt": "你是一个旅行助手，帮助用户规划行程。"
        }

        self.assertEqual(result, expected)

    def test_parse_info_with_multiline_content(self):
        content = """
        <角色名称>旅行助手</角色名称>
        <提示词>
        你是一个旅行助手，帮助用户规划行程。
        请使用友好的语气。
        </提示词>
        <角色描述>专业的旅行规划助手</角色描述>
        """

        result = Generator._parse_info(content)

        self.assertEqual(result["name"], "旅行助手")
        self.assertEqual(result["description"], "专业的旅行规划助手")
        self.assertEqual(result["prompt"], "你是一个旅行助手，帮助用户规划行程。\n        请使用友好的语气。")

    def test_parse_info_element_found(self):
        content = """
        <角色名称>旅行助手</角色名称>
        <角色描述>这是一个旅行助手</角色描述>
        """

        result = Generator._parse_info(content)
        self.assertEqual(result["name"], "旅行助手")
        self.assertEqual(result["description"], "这是一个旅行助手")

    def test_parse_info_element_not_found(self):
        content = """<角色名称>旅行助手</角色名称>"""

        result = Generator._parse_info(content)
        self.assertEqual(result["name"], "旅行助手")
        self.assertEqual(result["description"], "")

    def test_parse_info_empty_content(self):
        result = Generator._parse_info("")

        for key in Generator._EXTRACT_ELEMENTS.keys():
            self.assertEqual(result[key], "")

    def test_parse_info_special_characters(self):
        content = """
        <角色名称>助手&<>"特殊字符</角色名称>
        <角色描述>描述包含"引号"和'单引号'</角色描述>
        """

        result = Generator._parse_info(content)

        self.assertEqual(result["name"], '助手&<>"特殊字符')
        self.assertEqual(result["description"], '描述包含"引号"和\'单引号\'')

    def test_parse_info_with_whitespace_handling(self):
        content = """
        <角色名称>  助手  </角色名称>
        <角色描述>  
        描述内容  
        </角色描述>
        """

        result = Generator._parse_info(content)

        self.assertEqual(result["name"], "助手")
        self.assertEqual(result["description"], "描述内容")

    def test_parse_info_with_nested_tags(self):
        content = """
        <角色名称>助手</角色名称>
        <提示词>
        这是一个包含<tag>嵌套标签</tag>的提示词
        第二行内容
        </提示词>
        """

        result = Generator._parse_info(content)

        expected_prompt = "这是一个包含<tag>嵌套标签</tag>的提示词\n        第二行内容"
        self.assertEqual(result["prompt"], expected_prompt)

    @patch('openjiuwen.agent_builder.nl_to_agent.llm_agent_builder.generator.prompt.GENERATE_SYSTEM_PROMPT', "System Prompt")
    @patch('openjiuwen.agent_builder.nl_to_agent.llm_agent_builder.generator.prompt.GENERATE_USER_PROMPT_TEMPLATE',
           "User Template: {{user_message}} {{agent_config_info}} {{agent_resource_info}}")
    def test_generate_method(self):
        mock_llm_response = """
        <角色名称>测试助手</角色名称>
        <角色描述>测试描述</角色描述>
        <智能体开场白>你好！</智能体开场白>
        <预置问题>["问题1", "问题2"]</预置问题>
        <提示词>测试提示词</提示词>
        """

        self.mock_llm.chat.return_value = mock_llm_response

        message = "用户消息"
        agent_config_info = "Agent配置信息"
        agent_resource_info = "Agent资源信息"
        resource_id_dict = {
            "plugin": ["tool_001", "tool_002"],
            "knowledge": ["knowledge_001"],
            "workflow": ["workflow_001"]
        }

        result = self.generator.generate(
            message, agent_config_info, agent_resource_info, resource_id_dict
        )

        self.mock_llm.chat.assert_called_once()
        call_args = self.mock_llm.chat.call_args[0]

        messages = call_args[0]

        self.assertIsInstance(messages[0], SystemMessage)
        self.assertIsInstance(messages[1], HumanMessage)

        human_message_content = messages[1].content
        self.assertIn("用户消息", human_message_content)
        self.assertIn("Agent配置信息", human_message_content)
        self.assertIn("Agent资源信息", human_message_content)

        self.assertEqual(result["name"], "测试助手")
        self.assertEqual(result["description"], "测试描述")
        self.assertEqual(result["prompt"], "测试提示词")

        self.assertEqual(result["plugin"], ["tool_001", "tool_002"])
        self.assertEqual(result["knowledge"], ["knowledge_001"])
        self.assertEqual(result["workflow"], ["workflow_001"])

    def test_generate_with_empty_response(self):
        self.mock_llm.chat.return_value = ""

        message = "用户消息"
        agent_config_info = "Agent配置信息"
        agent_resource_info = "Agent资源信息"
        resource_id_dict = {
            "plugin": ["tool_001"],
            "knowledge": [],
            "workflow": []
        }

        result = self.generator.generate(
            message, agent_config_info, agent_resource_info, resource_id_dict
        )

        self.mock_llm.chat.assert_called_once()

        for key in Generator._EXTRACT_ELEMENTS.keys():
            self.assertEqual(result[key], "")

        self.assertEqual(result["plugin"], ["tool_001"])
        self.assertEqual(result["knowledge"], [])
        self.assertEqual(result["workflow"], [])

    def test_generate_with_partial_response(self):
        mock_llm_response = """
        <角色名称>部分助手</角色名称>
        <提示词>部分提示词</提示词>
        """

        self.mock_llm.chat.return_value = mock_llm_response

        message = "用户消息"
        agent_config_info = "Agent配置信息"
        agent_resource_info = "Agent资源信息"
        resource_id_dict = {}

        result = self.generator.generate(
            message, agent_config_info, agent_resource_info, resource_id_dict
        )

        self.assertEqual(result["name"], "部分助手")
        self.assertEqual(result["prompt"], "部分提示词")
        self.assertEqual(result["description"], "")
        self.assertEqual(result["opening_remarks"], "")
        self.assertEqual(result["question"], "")

    def test_parse_info_with_malformed_tags(self):
        content = """
        <角色名称>助手</角色名称>
        <角色描述>描述内容</角色描述>
        <提示词>提示词内容</提示词
        """

        result = Generator._parse_info(content)

        self.assertEqual(result["name"], "助手")
        self.assertEqual(result["description"], "描述内容")
        self.assertEqual(result["prompt"], "")

    def test_generate_with_special_characters_in_input(self):
        mock_llm_response = """
        <角色名称>测试助手</角色名称>
        <角色描述>测试描述</角色描述>
        <提示词>测试提示词</提示词>
        """

        self.mock_llm.chat.return_value = mock_llm_response

        message = "用户消息包含特殊字符：&<>\"'"
        agent_config_info = "Agent配置信息包含特殊字符：&<>\"'"
        agent_resource_info = "Agent资源信息包含特殊字符：&<>\"'"
        resource_id_dict = {}

        result = self.generator.generate(
            message, agent_config_info, agent_resource_info, resource_id_dict
        )

        self.mock_llm.chat.assert_called_once()

        self.assertEqual(result["name"], "测试助手")
        self.assertEqual(result["description"], "测试描述")
        self.assertEqual(result["prompt"], "测试提示词")


if __name__ == '__main__':
    unittest.main()
