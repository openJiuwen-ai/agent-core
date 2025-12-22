#!/usr/bin/env python
# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved
import json
import unittest

from openjiuwen.agent_builder.nl_to_agent.workflow_builder.dl_transformer.converter_utils import ConverterUtils
from openjiuwen.agent_builder.nl_to_agent.workflow_builder.dl_transformer.converters.branch_converter import \
    BranchConverter


class TestIntentDetectionConverter(unittest.TestCase):
    def setUp(self):
        super().setUp()

    def test_convert(self):
        node = json.loads("""{
            "id": "node_branch",
            "type": "Branch",
            "description": "条件判断",
            "parameters": {
                "conditions": [
                    {
                        "branch": "branch_1",
                        "description": "当值大于0且小于5时",
                        "expressions": [
                            "${node_questioner.number} longer_than 0",
                            "${node_questioner.number} short_than 5"
                        ],
                        "operator": "and",
                        "next": "node_llm"
                    },
                    {
                        "branch": "branch_2",
                        "description": "当值为空时",
                        "expression": "${node_questioner.number} is_empty",
                        "next": "node_llm_2"
                    },
                    {
                        "branch": "branch_0",
                        "description": "默认分支",
                        "expression": "default",
                        "next": "node_end"
                    }
                ]
            }
        }""")
        branch_converter = BranchConverter(node_data=node, nodes_dict={"node_branch": node})
        branch_converter.convert()
        self.assertEqual(
            json.loads("""[
                {"sourceNodeID": "node_branch", "targetNodeID": "node_llm", "sourcePortID": "branch_1"},
                {"sourceNodeID": "node_branch", "targetNodeID": "node_llm_2", "sourcePortID": "branch_2"},
                {"sourceNodeID": "node_branch", "targetNodeID": "node_end", "sourcePortID": "branch_0"}
            ]"""),
            ConverterUtils.convert_to_dict(branch_converter.edges)
        )
        self.assertEqual(
            json.loads("""{"id": "node_branch", "type": "4", "meta": {"position": {"x": 0, "y": 0}}, "data": {"title": "条件判断", "branches": [{"conditions": [{"left": {"type": "ref", "content": ["node_questioner", "number"]}, "operator": "11", "right": {"type": "constant", "content": "0", "schema": {"type": "string", "extra": {"weak": true}}}}, {"left": {"type": "ref", "content": ["node_questioner", "number"]}, "operator": "13", "right": {"type": "constant", "content": "5", "schema": {"type": "string", "extra": {"weak": true}}}}], "logic": 2, "branchId": "branch_1"}, {"conditions": [{"left": {"type": "ref", "content": ["node_questioner", "number"]}, "operator": "9"}], "branchId": "branch_2"}, {"conditions": [], "branchId": "branch_0"}]}}"""),
            ConverterUtils.convert_to_dict(branch_converter.node)
        )


if __name__ == "__main__":
    unittest.main()
