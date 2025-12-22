#!/usr/bin/env python
# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved
import json
import unittest

from openjiuwen.agent_builder.nl_to_agent.workflow_builder.dl_transformer.converter_utils import ConverterUtils
from openjiuwen.agent_builder.nl_to_agent.workflow_builder.dl_transformer.converters.start_converter import \
    StartConverter


class TestStartConverter(unittest.TestCase):
    def setUp(self):
        super().setUp()

    def test_convert(self):
        node = json.loads("""{
            "id": "node_start",
            "type": "Start",
            "description": "工作流开始",
            "parameters": {
                "outputs": [
                    {"name": "query", "description": "用户输入"}
                ]
            },
            "next": "node_end"
        }""")
        start_converter = StartConverter(node_data=node, nodes_dict={"node_start": node})
        start_converter.convert()
        self.assertEqual(
            json.loads("""[{"sourceNodeID": "node_start", "targetNodeID": "node_end"}]"""),
            ConverterUtils.convert_to_dict(start_converter.edges)
        )
        self.assertEqual(
            json.loads("""{"id": "node_start", "type": "1", "meta": {"position": {"x": 0, "y": 0}}, "data": {"title": "工作流开始", "outputs": {"type": "object", "properties": {"query": {"type": "string", "description": "用户输入", "extra": {"index": 0}}}, "required": []}}}"""),
            ConverterUtils.convert_to_dict(start_converter.node)
        )


if __name__ == "__main__":
    unittest.main()
