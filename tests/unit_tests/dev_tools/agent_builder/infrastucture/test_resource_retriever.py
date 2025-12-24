#!/usr/bin/env python
# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import unittest
from unittest.mock import patch, MagicMock
import json

import openjiuwen.dev_tools.agent_builder.infrastructure.resource.retriever as rr
from openjiuwen.dev_tools.agent_builder.infrastructure.resource.retriever import (
    ResourceRetriever, RETRIEVE_SYSTEM_PROMPT
)


LLM_CHAT_RESULT = "{\"plugin_id_list\": [\"mock_tool_id_1\", \"mock_tool_id_2\"]}"


class TestContextManager(unittest.TestCase):
    def setUp(self):
        self.mock_llm = MagicMock(name="llm")
        self.mock_llm.chat.return_value = LLM_CHAT_RESULT

        self.mock_query = "mock_query"

    @patch.object(ResourceRetriever, "load_resources", return_value="raw_plugins")
    @patch.object(rr.PluginProcessor, "preprocess", return_value=("plugin_dict", "tool_plugin_id_map"))
    @patch.object(rr.PluginProcessor, "format_for_prompt", return_value="plugin_info_list")
    @patch.object(rr.PluginProcessor, "get_retrieved_info", return_value=("plugins", "plugin_dict", "tool_id_map"))
    def test_retrieve(self,
                      mock_get_retrieved_info,
                      mock_format_for_prompt,
                      mock_preprocess,
                      mock_load_resources):
        retriever = ResourceRetriever(self.mock_llm)
        result = retriever.retrieve(self.mock_query)

        mock_load_resources.assert_called_once()
        mock_preprocess.assert_called_once_with("raw_plugins")
        mock_format_for_prompt.assert_called_once_with("plugin_dict")
        messages = RETRIEVE_SYSTEM_PROMPT.format({
            "user_input": "mock_query",
            "plugin_info_list": "plugin_info_list"
        }).to_messages()
        self.mock_llm.chat.assert_called_once_with(messages)
        mock_get_retrieved_info.assert_called_once_with(
            json.loads(LLM_CHAT_RESULT)["plugin_id_list"],
            "plugin_dict",
            "tool_plugin_id_map",
            need_inputs_outputs=True
        )
        self.assertEqual(result, dict(plugins="plugins", plugin_dict="plugin_dict", tool_id_map="tool_id_map"))


if __name__ == "__main__":
    unittest.main()
