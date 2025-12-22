#!/usr/bin/env python
# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
import unittest
from unittest.mock import MagicMock, Mock, patch

import openjiuwen.agent_builder.nl_to_agent.workflow_builder.dl_generator.dl_generator as dg
from openjiuwen.agent_builder.nl_to_agent.workflow_builder.dl_generator.dl_generator import DLGenerator
from openjiuwen.core.foundation.llm import SystemMessage, HumanMessage, AIMessage

LLM_CHAT_DL_RESULT = "dl_result"


class TestDLGenrator(unittest.TestCase):
    def setUp(self):
        self.llm = MagicMock(name="llm")
        self.llm.chat.return_value = LLM_CHAT_DL_RESULT
        self.resource = {"plugins": [{"name": "mock_plugin"}]}

        with patch.object(
            DLGenerator,
            'load_schema_and_examples',
            return_value=("mock_components_info", "mock_schema_info", "mock_examples")
        ):
            self.dl_generator = DLGenerator(self.llm)

    def test_update_prompt(self):
        dg.generate_system_prompt = "{{components}}\n{{schema}}\n{{plugins}}\n{{examples}}"
        prompt = self.dl_generator._update_prompt(self.resource)
        self.assertEqual(prompt, "mock_components_info\nmock_schema_info\n{\'name\': \'mock_plugin\'}\nmock_examples")

    def test_execute(self):
        self.dl_generator._execute("test_query", "test_system_prompt")
        self.llm.chat.assert_called_with(
            [SystemMessage(content="test_system_prompt"), HumanMessage(content="test_query")]
        )

        self.dl_generator.reflect_prompts = [
            AIMessage(content="mock_generated_dl"),
            HumanMessage(content="mock_reflect")
        ]
        self.dl_generator._execute("test_query", "test_system_prompt")
        self.llm.chat.assert_called_with([
            SystemMessage(content="test_system_prompt"),
            HumanMessage(content="test_query"),
            AIMessage(content="mock_generated_dl"),
            HumanMessage(content="mock_reflect")
        ])

    def test_generate(self):
        self.dl_generator._execute = Mock()
        self.dl_generator._update_prompt = Mock(return_value="mock_prompt")

        query = "test_query"
        self.dl_generator.generate(query, self.resource)
        self.dl_generator._update_prompt.assert_called_with(self.resource)
        self.dl_generator._execute.assert_called_with("test_query", "mock_prompt")

    def test_refine(self):
        self.dl_generator._execute = Mock()
        self.dl_generator._update_prompt = Mock(return_value="mock_prompt")
        dg.refine_user_prompt = "{{user_input}}\n{{exist_dl}}\n{{exist_mermaid}}"

        query = "test_query"
        exist_dl = "exist_dl"
        exist_mermaid = "exist_mermaid"
        self.dl_generator.refine(query, self.resource, exist_dl, exist_mermaid)
        self.dl_generator._update_prompt.assert_called_with(self.resource)
        self.dl_generator._execute.assert_called_with("test_query\nexist_dl\nexist_mermaid", "mock_prompt")


if __name__ == "__main__":
    unittest.main()
