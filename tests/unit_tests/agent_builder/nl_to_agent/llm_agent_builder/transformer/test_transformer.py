#!/usr/bin/env python
# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
import json
import unittest
from unittest.mock import patch

from openjiuwen.agent_builder.nl_to_agent.llm_agent_builder.transformer.transformer import Transformer


class TestTransformer(unittest.TestCase):
    def setUp(self):
        self.agent_info = dict(
            name="name",
            description="description",
            prompt="prompt",
            opening_remarks="opening_remarks",
            plugin=["mock_tool_1", "mock_tool_2"],
            workflow=["mock_workflow_1", "mock_workflow_2"]
        )
        self.transformer = Transformer()

    def test_collect_plugin(self):
        tool_id_list = ["mock_tool_1", "mock_tool_3"]
        plugin_dict = {
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
        tool_id_map = {
            "mock_tool_1": "mock_plugin_1",
            "mock_tool_2": "mock_plugin_1",
            "mock_tool_3": "mock_plugin_2",
        }
        result = Transformer.collect_plugin(tool_id_list, plugin_dict, tool_id_map)
        expected = [
            {
                "plugin_id": "mock_plugin_1",
                "plugin_name": "Mock Plugin One",
                "tool_id": "mock_tool_1",
                "tool_name": "Mock Tool One",
            },
            {
                "plugin_id": "mock_plugin_2",
                "plugin_name": "Mock Plugin Two",
                "tool_id": "mock_tool_3",
                "tool_name": "Mock Tool Three",
            }
        ]
        self.assertEqual(result, expected)

    def test_collect_workflow(self):
        workflow_id_list = ["mock_workflow_1", "mock_workflow_2"]
        workflow_dict = {
            "mock_workflow_1": {
                "workflow_id": "mock_workflow_1",
                "workflow_name": "Mock Workflow One",
                "workflow_desc": "Description of Mock Workflow One",
                "workflow_version": "Version of Mock Workflow One",
                "others": "",
            },
            "mock_workflow_2": {
                "workflow_id": "mock_workflow_2",
                "workflow_name": "Mock Workflow Two",
                "workflow_desc": "Description of Mock Workflow Two",
                "workflow_version": "Version of Mock Workflow Two",
                "others": "",
            },
        }
        result = Transformer.collect_workflow(workflow_id_list, workflow_dict)
        expected = [
            {
                "workflow_id": "mock_workflow_1",
                "workflow_name": "Mock Workflow One",
                "workflow_version": "Version of Mock Workflow One",
                "description": "Description of Mock Workflow One",
            },
            {
                "workflow_id": "mock_workflow_2",
                "workflow_name": "Mock Workflow Two",
                "workflow_version": "Version of Mock Workflow Two",
                "description": "Description of Mock Workflow Two",
            }
        ]
        self.assertEqual(result, expected)

    @patch.object(Transformer, "collect_plugin", return_value=[{"plugin_id": "mock_plugin_1"}])
    @patch.object(Transformer, "collect_workflow", return_value=[{"workflow_id": "mock_workflow_1"}])
    def test_transform_to_dsl(self, mock_collect_workflow, mock_collect_plugin):
        dsl_str = self.transformer.transform_to_dsl(
            self.agent_info, resource={"plugins": ["mock_plugins"], "workflows": ["mock_workflows"]}
        )
        dsl = json.loads(dsl_str)
        mock_collect_plugin.assert_called_once()
        mock_collect_workflow.assert_called_once()
        self.assertNotEqual(dsl["agent_id"], "")
        self.assertEqual(dsl["name"], "name")
        self.assertEqual(dsl["description"], "description")
        self.assertEqual(dsl["configs"]["system_prompt"], "prompt")
        self.assertEqual(dsl["opening_remarks"], "opening_remarks")
        self.assertEqual(dsl["plugins"], [{"plugin_id": "mock_plugin_1"}])
        self.assertEqual(dsl["workflows"], [{"workflow_id": "mock_workflow_1"}])
        self.assertIsNotNone(dsl["create_time"])
        self.assertIsNotNone(dsl["update_time"])


if __name__ == "__main__":
    unittest.main()
