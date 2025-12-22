#!/usr/bin/env python
# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
import unittest
from unittest.mock import MagicMock, Mock

import openjiuwen.agent_builder.nl_to_agent.workflow_builder.sop_generator.sop_generator as sg
from openjiuwen.agent_builder.nl_to_agent.workflow_builder.sop_generator.sop_generator import SopGenerator
from openjiuwen.core.foundation.llm import SystemMessage, HumanMessage

LLM_CHAT_SOP_RESULT = "<任务中文名称>mock_name</任务中文名称>\n<任务英文名称>mock_name_en</任务英文名称>\n" \
                      "<任务介绍>mock_description</任务介绍>\n<流程>mock_sop</流程>"


class TestSOPGenrator(unittest.TestCase):
    def setUp(self):
        self.llm = MagicMock(name="llm")
        self.llm.chat.return_value = LLM_CHAT_SOP_RESULT
        self.sop_generator = SopGenerator(self.llm)

        self.resource = {
            "plugins": [
                {"tool_id": "id1", "tool_name": "mock_plugin_1", "tool_description": "plugin_desc_1", "others": ""},
                {"tool_id": "id2", "tool_name": "mock_plugin_2", "tool_description": "plugin_desc_2", "others": ""},
                {"tool_id": "id3", "tool_name": "mock_plugin_3", "tool_description": "plugin_desc_3", "others": ""}
            ]
        }

    def test_update_prompt(self):
        prompt = self.sop_generator._update_prompt(None)
        self.assertEqual(prompt, sg.generate_system_prompt.replace("{{plugins}}", sg.EMPTY_RESOURCE_CONTENT))

        prompt = self.sop_generator._update_prompt(self.resource)
        expected_prompt = sg.generate_system_prompt.replace(
            "{{plugins}}", "\n".join(str(item) for item in self.resource["plugins"])
        )
        self.assertEqual(prompt, expected_prompt)

    def test_execute(self):
        sop_info = self.sop_generator._execute("test_query", "test_system_prompt")
        self.llm.chat.assert_called_with(
            [SystemMessage(content="test_system_prompt"), HumanMessage(content="test_query")]
        )
        self.assertEqual(sop_info["name"], "mock_name")
        self.assertEqual(sop_info["name_en"], "mock_name_en")
        self.assertEqual(sop_info["description"], "mock_description")
        self.assertEqual(sop_info["sop"], "mock_sop")

    def test_transform(self):
        self.sop_generator._execute = Mock()
        query = "test_query"
        self.sop_generator.transform(query)
        self.sop_generator._execute.assert_called_with(query, sg.transform_system_prompt)

    def test_generate_without_resource(self):
        self.sop_generator._execute = Mock()
        dialog_history = [{"role": "user", "content": "test_query"}]
        expected_prompt = sg.generate_system_prompt.replace("{{plugins}}", sg.EMPTY_RESOURCE_CONTENT)
        self.sop_generator.generate(dialog_history, None)
        self.sop_generator._execute.assert_called_with(sg.SOP_GENERATE_PROMPT + "user: test_query", expected_prompt)

    def test_generate_with_resource(self):
        self.sop_generator._execute = Mock()
        dialog_history = [{"role": "user", "content": "test_query"}]
        expected_prompt = sg.generate_system_prompt.replace(
            "{{plugins}}", "\n".join(str(item) for item in self.resource["plugins"])
        )
        self.sop_generator.generate(dialog_history, self.resource)
        self.sop_generator._execute.assert_called_with(sg.SOP_GENERATE_PROMPT + "user: test_query", expected_prompt)


if __name__ == "__main__":
    unittest.main()
