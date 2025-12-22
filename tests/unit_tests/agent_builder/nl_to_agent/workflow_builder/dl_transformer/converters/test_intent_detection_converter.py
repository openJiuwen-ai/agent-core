#!/usr/bin/env python
# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved
import json
import unittest

from openjiuwen.agent_builder.nl_to_agent.workflow_builder.dl_transformer.converter_utils import ConverterUtils
from openjiuwen.agent_builder.nl_to_agent.workflow_builder.dl_transformer.converters.intent_detection_converter import \
    IntentDetectionConverter


class TestIntentDetectionConverter(unittest.TestCase):
    def setUp(self):
        super().setUp()

    def test_convert(self):
        node = json.loads("""{
            "id": "node_intent_detection",
            "type": "IntentDetection",
            "description": "根据输入判断意图类别",
            "parameters": {
                "inputs": [
                    {"name": "input", "value": "${node_start.query}"}
                ],
                "configs": {
                    "prompt": "你是一个功能分类器，可以根据用户的请求，结合相应的功能类别描述，帮助用户选择正确的分支"
                },
                "conditions": [
                    {
                        "branch": "branch_1",
                        "description": "分类1",
                        "expression": "${node_intent_detection.rawOutput} contain 分类1",
                        "next": "node_llm"
                    },
                    {
                        "branch": "branch_2",
                        "description": "分类2",
                        "expression": "${node_intent_detection.rawOutput} contain 分类2",
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
        intent_detection_converter = IntentDetectionConverter(
            node_data=node, nodes_dict={"node_intent_detection": node}
        )
        intent_detection_converter.convert()
        self.assertEqual(
            json.loads("""[
                {"sourceNodeID": "node_intent_detection", "targetNodeID": "node_llm", "sourcePortID": "branch_1"},
                {"sourceNodeID": "node_intent_detection", "targetNodeID": "node_llm_2", "sourcePortID": "branch_2"},
                {"sourceNodeID": "node_intent_detection", "targetNodeID": "node_end", "sourcePortID": "branch_0"}
            ]"""),
            ConverterUtils.convert_to_dict(intent_detection_converter.edges)
        )
        self.assertEqual(
            json.loads("""{"id": "node_intent_detection", "type": "6", "meta": {"position": {"x": 0, "y": 0}}, "data": {"title": "根据输入判断意图类别", "inputs": {"inputParameters": {"input": {"type": "ref", "content": ["node_start", "query"], "extra": {"index": 0}}}, "llmParam": {"systemPrompt": {"type": "template", "content": "你是一个功能分类器，可以根据用户的请求，结合相应的功能类别描述，帮助用户选择正确的分支"}, "prompt": {"type": "template", "content": ""}, "mode": {"id": "52", "name": "siliconf-qwen3-8b", "type": "Qwen/Qwen3-8B"}}, "intents": [{"name": "分类1"}, {"name": "分类2"}]}, "outputs": {"type": "object", "properties": {"classificationId": {"type": "integer", "extra": {"index": 1}}}, "required": ["classificationId"]}, "branches": [{"branchId": "branch_1"}, {"branchId": "branch_2"}, {"branchId": "branch_0"}]}}"""),
            ConverterUtils.convert_to_dict(intent_detection_converter.node)
        )


if __name__ == "__main__":
    unittest.main()
