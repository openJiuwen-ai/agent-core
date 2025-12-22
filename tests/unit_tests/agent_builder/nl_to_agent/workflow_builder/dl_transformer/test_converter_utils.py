#!/usr/bin/env python
# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved
import unittest
from dataclasses import dataclass
from enum import Enum
from typing import Optional, List
from unittest.mock import patch

from openjiuwen.agent_builder.nl_to_agent.workflow_builder.dl_transformer.converter_utils import ConverterUtils
from openjiuwen.agent_builder.nl_to_agent.workflow_builder.dl_transformer.models import SourceType


class TestConverterUtils(unittest.TestCase):
    def setUp(self):
        super().setUp()

    def test_extract_variable(self):
        result = ConverterUtils.extract_variable("${node.variable}")
        self.assertEqual(result, ("node", "variable"))

        result = ConverterUtils.extract_variable("${node.variable.var}")
        self.assertEqual(result, None)

    def test_convert_ref_variable(self):
        result = ConverterUtils.convert_ref_variable("${node.variable}")
        self.assertEqual(result, dict(type=SourceType.ref, content=["node", "variable"]))

    def test_convert_llm_param(self):
        with patch.object(ConverterUtils, "LLM_MODEL_CONFIG", "mock_llm_config"):
            result = ConverterUtils.convert_llm_param("mock_system_prompt", "mock_user_prompt")
        self.assertEqual(result, {
            "systemPrompt": {
                "type": "template",
                "content": "mock_system_prompt"
            },
            "prompt": {
                "type": "template",
                "content": "mock_user_prompt"
            },
            "mode": "mock_llm_config"
        })

    def test_convert_to_dict(self):
        class TestEnum(Enum):
            FIRST = "first"
            SECOND = "second"

        @dataclass
        class Inner:
            id: int
            value: Optional[str] = None

        @dataclass
        class Outer:
            name: str
            type: TestEnum
            items: List[Inner]
            desc: Optional[str] = None

        # convert Enum
        obj = {"type": TestEnum.FIRST}
        result = ConverterUtils.convert_to_dict(obj)
        self.assertEqual(result, {"type": "first"})

        # convert dataclass to clean dict
        obj = Inner(id=1, value=None)
        result = ConverterUtils.convert_to_dict(obj)
        self.assertEqual(result, {"id": 1})

        # convert nested dataclass + Enum + list
        obj = Outer(name="test", type=TestEnum.FIRST, items=[Inner(1), Inner(2, "val")], desc=None)
        result = ConverterUtils.convert_to_dict(obj)
        expected = {"name": "test", "type": "first", "items": [{"id": 1}, {"id": 2, "value": "val"}]}
        self.assertEqual(result, expected)

        # convert dict to clean dict
        data = {"a": 1, "b": None, "c": {"d": None, "e": 2}}
        result = ConverterUtils.convert_to_dict(data)
        self.assertEqual(result, {"a": 1, "c": {"e": 2}})

        # convert list
        data = [Inner(1), None, Inner(2, "val")]
        result = ConverterUtils.convert_to_dict(data)
        expected = [{"id": 1}, {"id": 2, "value": "val"}]
        self.assertEqual(result, expected)

        # convert None
        self.assertEqual(ConverterUtils.convert_to_dict(None), {})

        # convert unstructured type
        self.assertEqual(ConverterUtils.convert_to_dict("hello"), {})
        self.assertEqual(ConverterUtils.convert_to_dict(123), {})


if __name__ == "__main__":
    unittest.main()
