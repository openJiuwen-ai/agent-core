#!/usr/bin/env python
# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved
import json
import unittest

from openjiuwen.agent_builder.nl_to_agent.workflow_builder.dl_transformer.converter_utils import ConverterUtils
from openjiuwen.agent_builder.nl_to_agent.workflow_builder.dl_transformer.converters.output_converter import \
    OutputConverter


class TestOutputConverter(unittest.TestCase):
    def setUp(self):
        super().setUp()

    def test_convert(self):
        node = json.loads("""{
            "id": "node_output",
            "type": "Output",
            "description": "输出大模型回答",
            "parameters": {
                "inputs": [
                    {"name": "content", "value": "${node_llm.output}"}
                ],
                "configs": {
                    "template": "{{content}}"
                }
            },
            "next": "node_end"
        }""")
        output_converter = OutputConverter(node_data=node, nodes_dict={"node_output": node})
        output_converter.convert()
        self.assertEqual(
            json.loads("""[{"sourceNodeID": "node_output", "targetNodeID": "node_end"}]"""),
            ConverterUtils.convert_to_dict(output_converter.edges)
        )
        self.assertEqual(
            json.loads("""{"id": "node_output", "type": "9", "meta": {"position": {"x": 0, "y": 0}}, "data": {"title": "输出大模型回答", "inputs": {"inputParameters": {"content": {"type": "ref", "content": ["node_llm", "output"], "extra": {"index": 0}}}, "content": {"type": "template", "content": "{{content}}"}}}}"""),
            ConverterUtils.convert_to_dict(output_converter.node)
        )


if __name__ == "__main__":
    unittest.main()
