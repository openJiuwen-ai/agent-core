#!/usr/bin/env python
# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved
import json
import unittest

from openjiuwen.agent_builder.nl_to_agent.workflow_builder.dl_transformer.converter_utils import ConverterUtils
from openjiuwen.agent_builder.nl_to_agent.workflow_builder.dl_transformer.converters.end_converter import EndConverter


class TestEndConverter(unittest.TestCase):
    def setUp(self):
        super().setUp()

    def test_convert(self):
        node = json.loads("""{
            "id": "node_end",
            "type": "End",
            "description": "工作流结束",
            "parameters": {
                "inputs": [
                    {"name": "result", "value": "${node_start.query}"}
                ],
                "configs": {
                    "template": "{{result}}"
                }
            }
        }""")
        end_converter = EndConverter(node_data=node, nodes_dict={"node_end": node})
        end_converter.convert()
        self.assertEqual(
            json.loads("""[]"""),
            ConverterUtils.convert_to_dict(end_converter.edges)
        )
        self.assertEqual(
            json.loads("""{"id": "node_end", "type": "2", "meta": {"position": {"x": 0, "y": 0}}, "data": {"title": "工作流结束", "inputs": {"inputParameters": {"result": {"type": "ref", "content": ["node_start", "query"], "extra": {"index": 0}}}}}}"""),
            ConverterUtils.convert_to_dict(end_converter.node)
        )


if __name__ == "__main__":
    unittest.main()
