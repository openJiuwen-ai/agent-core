#!/usr/bin/env python
# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
import unittest
from unittest.mock import Mock

from openjiuwen.agent_builder.nl_to_agent.llm_agent_builder.clarifier.clarifier import Clarifier


class TestClarifier(unittest.TestCase):

    def setUp(self):
        self.mock_llm = Mock()
        self.clarifier = Clarifier(self.mock_llm)

    def test_parse_resource_output_with_valid_content(self):
        resource_output = """
        ## Agent资源规划
        【选择的插件】
        [{"tool_id": "tool1", "tool_name": "工具1", "tool_desc": "工具1描述"}, {"tool_id": "tool2", "tool_name": "工具2", "tool_desc": "工具2描述"}]

        【选择的知识库】
        [{"knowledge_id": "kb1", "knowledge_name": "知识库1", "knowledge_desc": "知识库1描述"}]

        【选择的工作流】
        [{"workflow_id": "wf1", "workflow_name": "工作流1", "workflow_desc": "工作流1描述"}]
        """

        display_content, id_dict = Clarifier._parse_resource_output(resource_output)

        self.assertIn("【选择的插件】", display_content)
        self.assertIn("1. 工具1：工具1描述", display_content)
        self.assertIn("2. 工具2：工具2描述", display_content)
        self.assertIn("【选择的知识库】", display_content)
        self.assertIn("1. 知识库1：知识库1描述", display_content)
        self.assertIn("【选择的工作流】", display_content)
        self.assertIn("1. 工作流1：工作流1描述", display_content)

        self.assertEqual(id_dict["plugin"], ["tool1", "tool2"])
        self.assertEqual(id_dict["knowledge"], ["kb1"])
        self.assertEqual(id_dict["workflow"], ["wf1"])

    def test_parse_resource_output_with_partial_content(self):
        resource_output = """
        ## Agent资源规划
        【选择的插件】
        [{"tool_id": "tool1", "tool_name": "工具1", "tool_desc": "工具1描述"}]
        """

        display_content, id_dict = Clarifier._parse_resource_output(resource_output)

        self.assertIn("【选择的插件】", display_content)
        self.assertIn("1. 工具1：工具1描述", display_content)
        self.assertNotIn("【选择的知识库】", display_content)
        self.assertNotIn("【选择的工作流】", display_content)

        self.assertEqual(id_dict["plugin"], ["tool1"])
        self.assertNotIn("knowledge", id_dict)
        self.assertNotIn("workflow", id_dict)

    def test_parse_resource_output_with_empty_resource(self):
        resource_output = """
        ## Agent资源规划
        【选择的插件】
        []
        """

        display_content, id_dict = Clarifier._parse_resource_output(resource_output)

        self.assertEqual(display_content, "")
        self.assertEqual(id_dict, {})

    def test_parse_resource_output_with_missing_description(self):
        resource_output = """
        ## Agent资源规划
        【选择的插件】
        [{"tool_id": "tool1", "tool_name": "工具1"}]
        """

        display_content, id_dict = Clarifier._parse_resource_output(resource_output)

        self.assertEqual(display_content, "")
        self.assertEqual(id_dict, {})

    def test_parse_resource_output_with_missing_section(self):
        resource_output = """
        ## Agent资源规划
        【选择的知识库】
        [{"knowledge_id": "kb1", "knowledge_name": "知识库1", "knowledge_desc": "知识库1描述"}]
        """

        display_content, id_dict = Clarifier._parse_resource_output(resource_output)

        self.assertNotIn("【选择的插件】", display_content)
        self.assertIn("【选择的知识库】", display_content)
        self.assertNotIn("【选择的工作流】", display_content)

        self.assertNotIn("plugin", id_dict)
        self.assertEqual(id_dict["knowledge"], ["kb1"])
        self.assertNotIn("workflow", id_dict)

    def test_parse_resource_output_without_resource_planning(self):
        resource_output = "没有资源规划的内容"

        display_content, id_dict = Clarifier._parse_resource_output(resource_output)

        self.assertEqual(display_content, "")
        self.assertEqual(id_dict, {})

    def test_parse_resource_output_with_invalid_json(self):
        resource_output = """
        ## Agent资源规划
        【选择的插件】
        [invalid json format]
        """

        display_content, id_dict = Clarifier._parse_resource_output(resource_output)

        self.assertEqual(display_content, "")
        self.assertEqual(id_dict, {})

    def test_parse_resource_output_with_malformed_section(self):
        resource_output = """
        ## Agent资源规划
        【选择的插件】
        [{"tool_id": "tool1", "tool_name": "工具1", "tool_desc": "工具1描述"}
        【选择的知识库】
        [{"knowledge_id": "kb1", "knowledge_name": "知识库1", "knowledge_desc": "知识库1描述"}]
        """

        display_content, id_dict = Clarifier._parse_resource_output(resource_output)

        self.assertIn("【选择的知识库】", display_content)
        self.assertIn("1. 知识库1：知识库1描述", display_content)
        self.assertNotIn("【选择的插件】", display_content)

        self.assertEqual(id_dict["knowledge"], ["kb1"])
        self.assertNotIn("plugin", id_dict)

    def test_clarify_basic_functionality(self):
        messages = "用户消息内容"
        resource = {
            "tools": [{"id": "tool1", "name": "工具1"}],
            "knowledge": [{"id": "kb1", "name": "知识库1"}]
        }

        self.mock_llm.chat.side_effect = [
            "要素输出内容",
            """
            ## Agent资源规划
            【选择的插件】
            [{"tool_id": "tool1", "tool_name": "工具1", "tool_desc": "工具1描述"}]

            【选择的知识库】
            [{"knowledge_id": "kb1", "knowledge_name": "知识库1", "knowledge_desc": "知识库1描述"}]
            """
        ]

        factor_output, display_resource, resource_id_dict = self.clarifier.clarify(messages, resource)

        self.assertEqual(factor_output, "要素输出内容")

        self.assertIn("【选择的插件】", display_resource)
        self.assertIn("1. 工具1：工具1描述", display_resource)
        self.assertIn("【选择的知识库】", display_resource)
        self.assertIn("1. 知识库1：知识库1描述", display_resource)

        self.assertEqual(resource_id_dict["plugin"], ["tool1"])
        self.assertEqual(resource_id_dict["knowledge"], ["kb1"])

        self.assertEqual(self.mock_llm.chat.call_count, 2)

        first_call_args = self.mock_llm.chat.call_args_list[0]
        self.assertEqual(len(first_call_args[0][0]), 2)

        second_call_args = self.mock_llm.chat.call_args_list[1]
        self.assertEqual(len(second_call_args[0][0]), 2)

    def test_clarify_with_empty_resource_output(self):
        messages = "用户消息内容"
        resource = {"tools": []}

        self.mock_llm.chat.side_effect = [
            "要素输出内容",
            "没有资源规划的内容"
        ]

        factor_output, display_resource, resource_id_dict = self.clarifier.clarify(messages, resource)

        self.assertEqual(factor_output, "要素输出内容")
        self.assertEqual(display_resource, "")
        self.assertEqual(resource_id_dict, {})

    def test_clarify_with_invalid_resource_output(self):
        messages = "用户消息内容"
        resource = {"tools": []}

        self.mock_llm.chat.side_effect = [
            "要素输出内容",
            "无效的资源输出格式"
        ]

        factor_output, display_resource, resource_id_dict = self.clarifier.clarify(messages, resource)

        self.assertEqual(factor_output, "要素输出内容")
        self.assertEqual(display_resource, "")
        self.assertEqual(resource_id_dict, {})

    def test_init_with_llm(self):
        mock_llm = Mock()
        clarifier = Clarifier(mock_llm)
        self.assertEqual(clarifier.llm, mock_llm)

    def test_resource_config_structure(self):
        self.assertIn("plugin", Clarifier.RESOURCE_CONFIG)
        self.assertIn("knowledge", Clarifier.RESOURCE_CONFIG)
        self.assertIn("workflow", Clarifier.RESOURCE_CONFIG)

        plugin_config = Clarifier.RESOURCE_CONFIG["plugin"]
        self.assertEqual(plugin_config["label"], "插件")
        self.assertEqual(plugin_config["id_key"], "tool_id")
        self.assertEqual(plugin_config["name_key"], "tool_name")
        self.assertEqual(plugin_config["desc_key"], "tool_desc")

        knowledge_config = Clarifier.RESOURCE_CONFIG["knowledge"]
        self.assertEqual(knowledge_config["label"], "知识库")
        self.assertEqual(knowledge_config["id_key"], "knowledge_id")
        self.assertEqual(knowledge_config["name_key"], "knowledge_name")
        self.assertEqual(knowledge_config["desc_key"], "knowledge_desc")

        workflow_config = Clarifier.RESOURCE_CONFIG["workflow"]
        self.assertEqual(workflow_config["label"], "工作流")
        self.assertEqual(workflow_config["id_key"], "workflow_id")
        self.assertEqual(workflow_config["name_key"], "workflow_name")
        self.assertEqual(workflow_config["desc_key"], "workflow_desc")

    def test_parse_resource_output_with_multiple_resources_same_type(self):
        resource_output = """
        ## Agent资源规划
        【选择的插件】
        [
            {"tool_id": "tool1", "tool_name": "工具1", "tool_desc": "工具1描述"},
            {"tool_id": "tool2", "tool_name": "工具2", "tool_desc": "工具2描述"},
            {"tool_id": "tool3", "tool_name": "工具3", "tool_desc": "工具3描述"}
        ]
        """

        display_content, id_dict = Clarifier._parse_resource_output(resource_output)

        self.assertIn("【选择的插件】", display_content)
        self.assertIn("1. 工具1：工具1描述", display_content)
        self.assertIn("2. 工具2：工具2描述", display_content)
        self.assertIn("3. 工具3：工具3描述", display_content)

        self.assertEqual(id_dict["plugin"], ["tool1", "tool2", "tool3"])

    def test_parse_resource_output_with_mixed_valid_invalid_resources(self):
        resource_output = """
        ## Agent资源规划
        【选择的插件】
        [
            {"tool_id": "tool1", "tool_name": "工具1", "tool_desc": "工具1描述"},
            {"tool_name": "无效工具"},  # 缺少tool_id和tool_desc
            {"tool_id": "tool3", "tool_desc": "工具3描述"}  # 缺少tool_name
        ]
        """

        display_content, id_dict = Clarifier._parse_resource_output(resource_output)

        self.assertIn("【选择的插件】", display_content)
        self.assertIn("1. 工具1：工具1描述", display_content)
        self.assertNotIn("无效工具", display_content)
        self.assertNotIn("工具3", display_content)

        self.assertEqual(id_dict["plugin"], ["tool1", "tool3"])

    def test_clarify_with_complex_resource_structure(self):
        messages = "复杂的用户消息内容"
        resource = {
            "tools": [
                {"id": "tool1", "name": "高级工具1", "description": "功能强大的工具1"},
                {"id": "tool2", "name": "高级工具2", "description": "功能强大的工具2"}
            ],
            "knowledge": [
                {"id": "kb1", "name": "专业知识库1", "description": "包含专业知识的库1"},
                {"id": "kb2", "name": "专业知识库2", "description": "包含专业知识的库2"}
            ],
            "workflows": [
                {"id": "wf1", "name": "工作流1", "description": "复杂的工作流程1"},
                {"id": "wf2", "name": "工作流2", "description": "复杂的工作流程2"}
            ]
        }

        self.mock_llm.chat.side_effect = [
            "复杂的要素输出内容",
            """
            ## Agent资源规划
            【选择的插件】
            [{"tool_id": "tool1", "tool_name": "高级工具1", "tool_desc": "功能强大的工具1"}]

            【选择的知识库】
            [
                {"knowledge_id": "kb1", "knowledge_name": "专业知识库1", "knowledge_desc": "包含专业知识的库1"},
                {"knowledge_id": "kb2", "knowledge_name": "专业知识库2", "knowledge_desc": "包含专业知识的库2"}
            ]

            【选择的工作流】
            [{"workflow_id": "wf1", "workflow_name": "工作流1", "workflow_desc": "复杂的工作流程1"}]
            """
        ]

        factor_output, display_resource, resource_id_dict = self.clarifier.clarify(messages, resource)

        self.assertEqual(factor_output, "复杂的要素输出内容")

        self.assertIn("【选择的插件】", display_resource)
        self.assertIn("1. 高级工具1：功能强大的工具1", display_resource)
        self.assertIn("【选择的知识库】", display_resource)
        self.assertIn("1. 专业知识库1：包含专业知识的库1", display_resource)
        self.assertIn("2. 专业知识库2：包含专业知识的库2", display_resource)
        self.assertIn("【选择的工作流】", display_resource)
        self.assertIn("1. 工作流1：复杂的工作流程1", display_resource)

        self.assertEqual(resource_id_dict["plugin"], ["tool1"])
        self.assertEqual(resource_id_dict["knowledge"], ["kb1", "kb2"])
        self.assertEqual(resource_id_dict["workflow"], ["wf1"])


if __name__ == '__main__':
    unittest.main()
