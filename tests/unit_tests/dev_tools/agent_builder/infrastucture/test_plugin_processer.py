#!/usr/bin/env python
# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import unittest

from openjiuwen.dev_tools.agent_builder.infrastructure.resource import PluginProcessor


class TestContextManager(unittest.TestCase):
    def setUp(self):
        self.mock_raw_plugins = [
            {
                "plugin_id": "mock_plugin_1",
                "plugin_name": "Mock Plugin One",
                "plugin_desc": "Description of Mock Plugin One",
                "tools": [
                    {
                        "tool_id": "mock_tool_1",
                        "tool_name": "Mock Tool One",
                        "desc": "Description of Mock Tool One",
                        "input_parameters": [{"name": "input1", "description": "Input 1", "others": "mock_value"}],
                        "output_parameters": [{"name": "output1", "description": "Output 1", "others": "mock_value"}]
                    },
                    {
                        "tool_id": "mock_tool_2",
                        "tool_name": "Mock Tool Two",
                        "desc": "Description of Mock Tool Two",
                        "input_parameters": [{"name": "input2", "description": "Input 2", "others": "mock_value"}],
                        "output_parameters": [{"name": "output2", "description": "Output 2", "others": "mock_value"}]
                    }
                ]
            },
            {
                "plugin_id": "mock_plugin_2",
                "plugin_name": "Mock Plugin Two",
                "plugin_desc": "Description of Mock Plugin Two",
                "tools": [
                    {
                        "tool_id": "mock_tool_3",
                        "tool_name": "Mock Tool Three",
                        "desc": "Description of Mock Tool Three",
                        "input_parameters": [{"name": "input3", "description": "Input 3", "others": "mock_value"}],
                        "output_parameters": [{"name": "output3", "description": "Output 3", "others": "mock_value"}]
                    }
                ]
            }
        ]

        self.mock_plugin_dict = {
            "mock_plugin_1": {
                "plugin_id": "mock_plugin_1",
                "plugin_name": "Mock Plugin One",
                "plugin_desc": "Description of Mock Plugin One",
                "tools": {
                    "mock_tool_1": {
                        "tool_id": "mock_tool_1",
                        "tool_name": "Mock Tool One",
                        "tool_desc": "Description of Mock Tool One",
                        "ori_inputs": [{"name": "input1", "description": "Input 1", "others": "mock_value"}],
                        "ori_outputs": [{"name": "output1", "description": "Output 1", "others": "mock_value"}],
                        "inputs_for_dl_gen": [{"name": "input1", "description": "Input 1"}],
                        "outputs_for_dl_gen": [{"name": "output1", "description": "Output 1"}]
                    },
                    "mock_tool_2": {
                        "tool_id": "mock_tool_2",
                        "tool_name": "Mock Tool Two",
                        "tool_desc": "Description of Mock Tool Two",
                        "ori_inputs": [{"name": "input2", "description": "Input 2", "others": "mock_value"}],
                        "ori_outputs": [{"name": "output2", "description": "Output 2", "others": "mock_value"}],
                        "inputs_for_dl_gen": [{"name": "input2", "description": "Input 2"}],
                        "outputs_for_dl_gen": [{"name": "output2", "description": "Output 2"}]
                    }
                }
            },
            "mock_plugin_2": {
                "plugin_id": "mock_plugin_2",
                "plugin_name": "Mock Plugin Two",
                "plugin_desc": "Description of Mock Plugin Two",
                "tools": {
                    "mock_tool_3": {
                        "tool_id": "mock_tool_3",
                        "tool_name": "Mock Tool Three",
                        "tool_desc": "Description of Mock Tool Three",
                        "ori_inputs": [{"name": "input3", "description": "Input 3", "others": "mock_value"}],
                        "ori_outputs": [{"name": "output3", "description": "Output 3", "others": "mock_value"}],
                        "inputs_for_dl_gen": [{"name": "input3", "description": "Input 3"}],
                        "outputs_for_dl_gen": [{"name": "output3", "description": "Output 3"}]
                    }
                }
            }
        }

        self.mock_tool_plugin_id_map = {
            "mock_tool_1": "mock_plugin_1",
            "mock_tool_2": "mock_plugin_1",
            "mock_tool_3": "mock_plugin_2",
        }

    def test_preprocess(self):
        plugin_dict, tool_plugin_id_map = PluginProcessor.preprocess(self.mock_raw_plugins)
        self.assertIn("mock_plugin_1", plugin_dict)
        self.assertIn("mock_tool_1", plugin_dict["mock_plugin_1"]["tools"])
        self.assertEqual(
            self.mock_raw_plugins[0]["tools"][0]["input_parameters"][0],
            plugin_dict["mock_plugin_1"]["tools"]["mock_tool_1"]["ori_inputs"][0]
        )
        self.assertEqual(
            {"name": "input1", "description": "Input 1"},
            plugin_dict["mock_plugin_1"]["tools"]["mock_tool_1"]["inputs_for_dl_gen"][0]
        )
        self.assertEqual(tool_plugin_id_map["mock_tool_1"], "mock_plugin_1")
        self.assertEqual(tool_plugin_id_map["mock_tool_2"], "mock_plugin_1")
        self.assertEqual(tool_plugin_id_map["mock_tool_3"], "mock_plugin_2")

    def test_format_for_prompt(self):
        plugin_info_list = PluginProcessor.format_for_prompt(self.mock_plugin_dict)
        expected = [
            {
                "plugin_id": "mock_plugin_1",
                "plugin_name": "Mock Plugin One",
                "plugin_desc": "Description of Mock Plugin One",
                "tools": [
                    {
                        "tool_id": "mock_tool_1",
                        "tool_name": "Mock Tool One",
                        "tool_desc": "Description of Mock Tool One"
                    },
                    {
                        "tool_id": "mock_tool_2",
                        "tool_name": "Mock Tool Two",
                        "tool_desc": "Description of Mock Tool Two"
                    }
                ]
            },
            {
                "plugin_id": "mock_plugin_2",
                "plugin_name": "Mock Plugin Two",
                "plugin_desc": "Description of Mock Plugin Two",
                "tools": [
                    {
                        "tool_id": "mock_tool_3",
                        "tool_name": "Mock Tool Three",
                        "tool_desc": "Description of Mock Tool Three"
                    }
                ]
            }
        ]
        self.assertEqual(plugin_info_list, expected)
        
    def test_get_retrieved_info(self):
        tool_id_list = ["mock_tool_1", "mock_tool_2"]
        retrieved_plugin, retrieved_plugin_dict, retrieved_tool_id_map = PluginProcessor.get_retrieved_info(
            tool_id_list, self.mock_plugin_dict, self.mock_tool_plugin_id_map, need_inputs_outputs=True
        )
        expected_plugin = [
            {
                "tool_id": "mock_tool_1",
                "tool_name": "Mock Tool One",
                "tool_desc": "Description of Mock Tool One",
                "inputs": [{"name": "input1", "description": "Input 1"}],
                "outputs": [{"name": "output1", "description": "Output 1"}]
            },
            {
                "tool_id": "mock_tool_2",
                "tool_name": "Mock Tool Two",
                "tool_desc": "Description of Mock Tool Two",
                "inputs": [{"name": "input2", "description": "Input 2"}],
                "outputs": [{"name": "output2", "description": "Output 2"}]
            }
        ]
        self.assertEqual(retrieved_plugin, expected_plugin)
        self.assertEqual(retrieved_plugin_dict, {"mock_plugin_1": self.mock_plugin_dict["mock_plugin_1"]})
        self.assertEqual(retrieved_tool_id_map, {"mock_tool_1": "mock_plugin_1", "mock_tool_2": "mock_plugin_1"})

        retrieved_plugin_without_inputs_outputs, _, _ = PluginProcessor.get_retrieved_info(
            tool_id_list, self.mock_plugin_dict, self.mock_tool_plugin_id_map, need_inputs_outputs=False
        )
        expected_plugin_no_io = [
            {
                "tool_id": "mock_tool_1",
                "tool_name": "Mock Tool One",
                "tool_desc": "Description of Mock Tool One",
            },
            {
                "tool_id": "mock_tool_2",
                "tool_name": "Mock Tool Two",
                "tool_desc": "Description of Mock Tool Two",
            }
        ]
        self.assertEqual(retrieved_plugin_without_inputs_outputs, expected_plugin_no_io)


if __name__ == "__main__":
    unittest.main()
