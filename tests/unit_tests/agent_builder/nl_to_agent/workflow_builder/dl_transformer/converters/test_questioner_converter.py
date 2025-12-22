#!/usr/bin/env python
# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved
import json
import unittest

from openjiuwen.agent_builder.nl_to_agent.workflow_builder.dl_transformer.converter_utils import ConverterUtils
from openjiuwen.agent_builder.nl_to_agent.workflow_builder.dl_transformer.converters.questioner_converter import \
    QuestionerConverter


class TestQuestionerConverter(unittest.TestCase):
    def setUp(self):
        super().setUp()

    def test_convert(self):
        node = json.loads("""{
            "id": "node_questioner",
            "type": "Questioner",
            "description": "提问用户的出行目的和出行方式",
            "parameters": {
                "inputs": [
                    {"name": "input", "value": "${node_start.query}"}
                ],
                "outputs": [
                    {"name": "destination", "description": "出行目的地"},
                    {"name": "travel_mode", "description": "出行方式"}
                ],
                "configs": {
                    "prompt": "请输入出行目的地以及出行方式"
                }
            },
            "next": "node_llm"
        }""")
        questioner_converter = QuestionerConverter(node_data=node, nodes_dict={"node_questioner": node})
        questioner_converter.convert()
        self.assertEqual(
            json.loads("""[{"sourceNodeID": "node_questioner", "targetNodeID": "node_llm"}]"""),
            ConverterUtils.convert_to_dict(questioner_converter.edges)
        )
        self.assertEqual(
            json.loads("""{"id": "node_questioner", "type": "7", "meta": {"position": {"x": 0, "y": 0}}, "data": {"title": "提问用户的出行目的和出行方式", "inputs": {"inputParameters": {"input": {"type": "ref", "content": ["node_start", "query"], "extra": {"index": 0}}}, "systemPrompt": {"type": "template", "content": "请输入出行目的地以及出行方式"}, "llmParam": {"systemPrompt": {"type": "template", "content": "请输入出行目的地以及出行方式"}, "prompt": {"type": "template", "content": ""}, "mode": {"id": "52", "name": "siliconf-qwen3-8b", "type": "Qwen/Qwen3-8B"}}}, "outputs": {"type": "object", "properties": {"destination": {"type": "string", "description": "出行目的地", "extra": {"index": 1}}, "travel_mode": {"type": "string", "description": "出行方式", "extra": {"index": 2}}}, "required": ["destination", "travel_mode"]}}}"""),
            ConverterUtils.convert_to_dict(questioner_converter.node)
        )


if __name__ == "__main__":
    unittest.main()
