#!/usr/bin/env python
# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
import json
import unittest
from unittest.mock import patch, MagicMock

import openjiuwen.agent_builder.nl_to_agent.workflow_builder.dl_transformer.dl_transformer as dtf
from openjiuwen.agent_builder.nl_to_agent.workflow_builder.dl_transformer.dl_transformer import DLTransformer
from openjiuwen.agent_builder.nl_to_agent.workflow_builder.dl_transformer.models import NodeType


class TestConverterUtils(unittest.TestCase):
    def setUp(self):
        self.transformer = DLTransformer()

        self.plugin_dict = {
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
        self.tool_id_map = {
            "mock_tool_1": "mock_plugin_1",
            "mock_tool_2": "mock_plugin_1",
            "mock_tool_3": "mock_plugin_2",
        }

    def test_collect_plugin(self):
        tool_id_list = ["mock_tool_1", "mock_tool_3"]
        result = DLTransformer.collect_plugin(tool_id_list, self.plugin_dict, self.tool_id_map)
        expected = [
            {
                "plugin_id": "mock_plugin_1",
                "plugin_name": "Mock Plugin One",
                "tool_id": "mock_tool_1",
                "tool_name": "Mock Tool One",
                "inputs": [{"name": "input1", "description": "Input 1", "others": "mock_value"}],
                "outputs": [{"name": "output1", "description": "Output 1", "others": "mock_value"}],
            },
            {
                "plugin_id": "mock_plugin_2",
                "plugin_name": "Mock Plugin Two",
                "tool_id": "mock_tool_3",
                "tool_name": "Mock Tool Three",
                "inputs": [{"name": "input3", "description": "Input 3", "others": "mock_value"}],
                "outputs": [{"name": "output3", "description": "Output 3", "others": "mock_value"}],
            }
        ]
        self.assertEqual(result, expected)

    @patch.object(dtf, "Workflow")
    @patch.object(dtf, "Position")
    @patch.object(dtf, "ConverterUtils")
    @patch.object(DLTransformer, "collect_plugin", return_value=[{"plugin_id": "mock_plugin_1"}])
    def test_transform_to_dsl(self, mock_collect_plugin, mock_converter_utils, mock_position, mock_workflow):
        mock_converter = MagicMock()
        mock_converter.node = {"node": "converted"}
        mock_converter.edges = [{"edge": "converted"}]
        mock_converter_class = MagicMock(return_value=mock_converter)

        with patch.dict(
            DLTransformer._dsl_converter_registry,
            {NodeType.Start.dl_type: mock_converter_class, NodeType.Plugin.dl_type: mock_converter_class},
            clear=False,
        ):
            workflow_instance = MagicMock()
            workflow_instance.nodes = []
            workflow_instance.edges = []
            mock_workflow.return_value = workflow_instance

            mock_converter_utils.convert_to_dict.return_value = {
                "nodes": ["converted_node"], "edges": ["converted_edge"],
            }
            dl_nodes = [
                {"id": "1", "type": NodeType.Start.dl_type},
                {"id": "2", "type": NodeType.Plugin.dl_type},
            ]
            dl_json = json.dumps(dl_nodes)

            resource_arg = {"plugins": [{"tool_id": "t1"}]}
            output = self.transformer.transform_to_dsl(dl_json, resource=resource_arg)

            self.assertEqual(mock_converter_class.call_count, 2)
            calls = mock_position.call_args_list
            self.assertEqual(calls[0][0], (0, 0))
            self.assertEqual(calls[1][0], (20, 20))

            mock_collect_plugin.assert_called_once()
            second_call_kwargs = mock_converter_class.call_args_list[1][1]
            self.assertIn("resource", second_call_kwargs)

            self.assertEqual(workflow_instance.nodes, [{"node": "converted"}, {"node": "converted"}])
            self.assertEqual(workflow_instance.edges, [{"edge": "converted"}, {"edge": "converted"}])
            result_json = json.loads(output)
            self.assertEqual(result_json, {"nodes": ["converted_node"], "edges": ["converted_edge"]})


if __name__ == "__main__":
    unittest.main()
