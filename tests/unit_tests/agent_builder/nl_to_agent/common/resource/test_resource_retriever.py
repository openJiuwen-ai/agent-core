#!/usr/bin/env python
# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
import json
import unittest
from unittest.mock import patch, MagicMock

import openjiuwen.agent_builder.nl_to_agent.common.resource.resource_retriever as rr
from openjiuwen.agent_builder.nl_to_agent.common.resource.resource_retriever import ResourceRetriever

LLM_CHAT_RESULT = "{\"plugin_id_list\": [\"mock_tool_id_1\", \"mock_tool_id_2\"]}"


class TestContextManager(unittest.TestCase):
    def setUp(self):
        self.llm = MagicMock(name="llm")
        self.llm.chat.return_value = LLM_CHAT_RESULT

        self.dialog_history = [{"role": "user", "content": "query"}]

    @patch.object(ResourceRetriever, "load_resources", return_value="raw_plugins")
    @patch.object(ResourceRetriever, "_llm_retrieve", return_value=json.loads(LLM_CHAT_RESULT))
    @patch.object(rr.PluginProcessor, "preprocess", return_value=("plugin_dict", "tool_plugin_id_map"))
    @patch.object(rr.PluginProcessor, "format_for_prompt", return_value="plugin_info_list")
    @patch.object(rr.PluginProcessor, "get_retrieved_info", return_value=("plugins", "plugin_dict", "tool_id_map"))
    def test_retrieve(self,
                      mock_get_retrieved_info,
                      mock_format_for_prompt,
                      mock_preprocess,
                      mock_llm_retrieve,
                      mock_load_resources):
        rr.retrieve_system_prompt = "{{dialog_history}}\n{{plugin_info_list}}"
        retriever = ResourceRetriever(self.llm)
        result = retriever.retrieve(self.dialog_history)

        mock_load_resources.assert_called_once()
        mock_llm_retrieve.assert_called_once_with("user: query\nplugin_info_list")
        mock_preprocess.assert_called_once_with("raw_plugins")
        mock_format_for_prompt.assert_called_once_with("plugin_dict")
        mock_get_retrieved_info.assert_called_once_with(
            json.loads(LLM_CHAT_RESULT)["plugin_id_list"],
            "plugin_dict",
            "tool_plugin_id_map",
            need_inputs_outputs=True
        )
        self.assertEqual(result, dict(plugins="plugins", plugin_dict="plugin_dict", tool_id_map="tool_id_map"))


if __name__ == "__main__":
    unittest.main()
