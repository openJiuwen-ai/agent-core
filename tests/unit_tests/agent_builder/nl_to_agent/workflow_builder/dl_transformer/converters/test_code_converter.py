#!/usr/bin/env python
# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved
import json
import unittest

from openjiuwen.agent_builder.nl_to_agent.workflow_builder.dl_transformer.converter_utils import ConverterUtils
from openjiuwen.agent_builder.nl_to_agent.workflow_builder.dl_transformer.converters.code_converter import CodeConverter


class TestCodeConverter(unittest.TestCase):
    def setUp(self):
        super().setUp()

    def test_convert(self):
        node = json.loads("""{
            "id": "node_code",
            "type": "Code",
            "description": "计算BMI",
            "parameters": {
                "inputs": [
                    {"name": "height", "value": "${node_questioner.height}"},
                    {"name": "weight", "value": "${node_questioner.weight}"}
                ],
                "outputs": [
                    {"name": "bmi", "description": "计算的BMI结果"}
                ],
                "configs": {
                    "code": "def main(args: dict):\\n    '''运行代码会调用此函数\\n    :param args: 输入固定为args字典类型，kv为输入参数键值对在args.params中\\n    :return: 输出参数为字典类型，kv为输出参数键值对\\n    '''\\n    params = args.get('params')\\n    h = params.get('height')\\n    w = params.get('weight')\\n    bmi = w / h / h\\n    result = {'bmi': bmi}\\n    return result"
                }
            },
            "next": "node_llm"
        }""")
        code_converter = CodeConverter(node_data=node, nodes_dict={"node_code": node})
        code_converter.convert()
        self.assertEqual(
            json.loads("""[{"sourceNodeID": "node_code", "targetNodeID": "node_llm"}]"""),
            ConverterUtils.convert_to_dict(code_converter.edges)
        )
        self.assertEqual(
            json.loads("""{"id": "node_code", "type": "10", "meta": {"position": {"x": 0, "y": 0}}, "data": {"title": "计算BMI", "inputs": {"inputParameters": {"height": {"type": "ref", "content": ["node_questioner", "height"], "extra": {"index": 0}}, "weight": {"type": "ref", "content": ["node_questioner", "weight"], "extra": {"index": 1}}}, "language": "python", "code": "def main(args: dict):\\n    '''运行代码会调用此函数\\n    :param args: 输入固定为args字典类型，kv为输入参数键值对在args.params中\\n    :return: 输出参数为字典类型，kv为输出参数键值对\\n    '''\\n    params = args.get('params')\\n    h = params.get('height')\\n    w = params.get('weight')\\n    bmi = w / h / h\\n    result = {'bmi': bmi}\\n    return result"}, "outputs": {"type": "object", "properties": {"bmi": {"type": "string", "description": "计算的BMI结果", "extra": {"index": 2}}}, "required": []}, "exceptionConfig": {"retryTimes": 3, "timeoutSeconds": 30, "processType": "break"}}}"""),
            ConverterUtils.convert_to_dict(code_converter.node)
        )


if __name__ == "__main__":
    unittest.main()
