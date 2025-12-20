#!/usr/bin/python3.11
# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025-2025. All rights reserved

import unittest

from jiuwen.core.utils.tool.tool import tool
from jiuwen.core.utils.tool.param import Param
from jiuwen.core.utils.llm.messages import ToolInfo, Function, Parameters

@tool(params=[Param(name="a", description="first arg", param_type="int", required=True),
              Param(name="b", description="second arg", param_type="int", required=True) ])
def add(a: int, b: int):
    """function add"""
    return a+b


@tool(
    name="local_sub",
    description="local function for sub",
    params=[
        Param(name="a", description="first arg", param_type="int", required=True),
        Param(name="b", description="second arg", param_type="int", required=True),
    ],
)
def sub(a: int, b: int):
    return a - b


class TestToolDecorator(unittest.TestCase):
    def test_invoke(self):
        result = add.invoke(
            {
                "a": 5,
                "b": 1
            }
        )
        self.assertEqual(add.name, "add")
        self.assertEqual(add.description, "function add")
        self.assertEqual(result, 6)

        sub_result = sub.invoke({"a": 5, "b": 1})
        self.assertEqual(sub.name, "local_sub")
        self.assertEqual(sub.description, "local function for sub")
        self.assertEqual(sub_result, 4)

    def test_get_tool_info(self):
        res = add.get_tool_info()
        too_info = ToolInfo(
            function=Function(
                name="add",
                description="function add",
                parameters=Parameters(
                    type="object",
                    properties={
                        "a": {"description": "first arg", "type": "integer"},
                        "b": {"description": "second arg", "type": "integer"},
                    },
                    required=["a", "b"],
                ),
            )
        )
        self.assertEqual(res, too_info)
