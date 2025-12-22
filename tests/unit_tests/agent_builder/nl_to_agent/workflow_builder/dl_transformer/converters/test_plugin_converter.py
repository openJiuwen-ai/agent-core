#!/usr/bin/env python
# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved
import json
import unittest

from openjiuwen.agent_builder.nl_to_agent.workflow_builder.dl_transformer.converter_utils import ConverterUtils
from openjiuwen.agent_builder.nl_to_agent.workflow_builder.dl_transformer.converters.plugin_converter import \
    PluginConverter


class TestPluginConverter(unittest.TestCase):
    def setUp(self):
        super().setUp()

    def test_convert(self):
        node = json.loads("""{
            "id": "node_plugin",
            "type": "Plugin",
            "description": "调用OCR",
            "parameters": {
                "inputs": [
                    {"name": "image_file", "value": "${node_start.input_file}"}
                ],
                "outputs": [
                    {"name": "text", "description": "文本识别结果"}
                ],
                "configs": {
                    "tool_id": "mock_ocr",
                    "tool_name": "ocr"
                }
            },
            "next": "node_llm"
        }""")
        resource = {
            "plugins": [{
                "tool_id": "mock_ocr",
                "tool_name": "ocr",
                "plugin_id": "mock_plugin_id",
                "plugin_name": "mock_plugin_name"
            }]
        }
        plugin_converter = PluginConverter(node_data=node, nodes_dict={"Plugin": node}, resource=resource)
        plugin_converter.convert()
        self.assertEqual(
            json.loads("""[{"sourceNodeID": "node_plugin", "targetNodeID": "node_llm"}]"""),
            ConverterUtils.convert_to_dict(plugin_converter.edges)
        )
        self.assertEqual(
            json.loads("""{"id": "node_plugin", "type": "19", "meta": {"position": {"x": 0, "y": 0}}, "data": {"title": "调用OCR", "inputs": {"inputParameters": {"image_file": {"type": "ref", "content": ["node_start", "input_file"], "extra": {"index": 0}}}, "pluginParam": {"toolID": "mock_ocr", "toolName": "ocr", "pluginID": "mock_plugin_id", "pluginName": "mock_plugin_name"}}, "outputs": {"type": "object", "properties": {"text": {"type": "string", "description": "文本识别结果", "extra": {"index": 1}}}, "required": []}}}"""),
            ConverterUtils.convert_to_dict(plugin_converter.node)
        )


if __name__ == "__main__":
    unittest.main()
