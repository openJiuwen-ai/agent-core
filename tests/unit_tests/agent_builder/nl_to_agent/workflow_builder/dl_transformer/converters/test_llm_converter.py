#!/usr/bin/env python
# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved
import json
import unittest

from openjiuwen.agent_builder.nl_to_agent.workflow_builder.dl_transformer.converter_utils import ConverterUtils
from openjiuwen.agent_builder.nl_to_agent.workflow_builder.dl_transformer.converters.llm_converter import LLMConverter


class TestLLMConverter(unittest.TestCase):
    def setUp(self):
        super().setUp()

    def test_convert(self):
        node = json.loads("""{
            "id": "node_llm",
            "type": "LLM",
            "description": "调用大模型生成回复",
            "parameters": {
                "inputs": [
                    {"name": "query", "value": "${node_start.query}"}
                ],
                "outputs": [
                    {"name": "output", "description": "大模型输出"}
                ],
                "configs": {
                    "system_prompt": "## 人设\\n你是一个xxx。\\n\\n## 任务描述\\nxxx",
                    "user_prompt": "{{query}}"
                }
            },
            "next": "node_end"
        }""")
        llm_converter = LLMConverter(node_data=node, nodes_dict={"node_llm": node})
        llm_converter.convert()
        self.assertEqual(
            json.loads("""[{"sourceNodeID": "node_llm", "targetNodeID": "node_end"}]"""),
            ConverterUtils.convert_to_dict(llm_converter.edges)
        )
        self.assertEqual(
            json.loads("""{"id": "node_llm", "type": "3", "meta": {"position": {"x": 0, "y": 0}}, "data": {"title": "调用大模型生成回复", "inputs": {"inputParameters": {"query": {"type": "ref", "content": ["node_start", "query"], "extra": {"index": 0}}}, "llmParam": {"systemPrompt": {"type": "template", "content": "## 人设\\n你是一个xxx。\\n\\n## 任务描述\\nxxx"}, "prompt": {"type": "template", "content": "{{query}}"}, "mode": {"id": "52", "name": "siliconf-qwen3-8b", "type": "Qwen/Qwen3-8B"}}}, "outputs": {"type": "object", "properties": {"output": {"type": "string", "description": "大模型输出", "extra": {"index": 1}}}, "required": []}}}"""),
            ConverterUtils.convert_to_dict(llm_converter.node)
        )


if __name__ == "__main__":
    unittest.main()
