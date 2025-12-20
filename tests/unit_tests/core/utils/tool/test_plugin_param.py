#!/usr/bin/python3.11
# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025-2025. All rights reserved


import pytest

from openjiuwen.core.common.exception.exception import JiuWenBaseException
from openjiuwen.core.utils.tool.param import Param
from openjiuwen.core.utils.tool.param_util import ParamUtil
from openjiuwen.core.utils.tool.types import ValueTypeEnum, Type


class TestPluginParam:
    NEST_TYPE_EXAMPLE = {
        "Array<bool>": "array<boolean>", "list<String>": "array<string>",
        "List<int>": "array<integer>", "array<object>": "array<object>",
        "Array<float>": "array<number>"
    }
    UNSUPPORTED_TYPE_EXAMPLE = ['Array<array>', 'List<list>', 'Array<Array>', 'array<List>', "file"]
    NEST_SPLIT_RESULT = {
        "array<boolean>": ('array', 'boolean'), 'List<Object>': ('array', 'object'),
        'List<int>': ('array', 'integer'), 'Array<float>': ('array', 'number')
    }
    DEFAULT_VALUE_EXAMPLE = {
        'Array<bool>': [True, False, 1], 'int': [True, 2, 1], 'object': True,
        'list<object>': [{"a": 1}, 1], 'List<float>': 1.05
    }
    INPUTS = {"location": "上海"}
    def assertEqual(self, left, right):
        assert left == right
        
    def assertIsInstance(self, left, right):
        assert isinstance(left, right)
        
    def assertFalse(self, value):
        assert value is False
        
    def assertTrue(self, value):
        return value is True

    @pytest.fixture(autouse=True)
    def setUp(self):
        self.schema_example = {
            "name": "times",
            "description": "时间",
            "required": True,
            "type": "object",
            "visible": True,
            "method": "Body",
            "default_value": {"starttime": "12345", "endtime": "456123"},
            "schema": [
                {
                    "name": "starttime",
                    "description": "开始时间",
                    "required": True,
                    "type": "str",
                    "visible": False,
                    "method": "Body",
                    "default_value": "2025-07-14",
                },
                {
                    "name": "endtime",
                    "description": "结束时间",
                    "required": True,
                    "type": "String",
                    "visible": True,
                    "method": "Body",
                    "default_value": "2025-07-14",
                }
            ]
        }

    def test_basic_type_should_unify_correct(self):
        for nest_type in self.NEST_TYPE_EXAMPLE.keys():
            self.assertTrue(ValueTypeEnum.is_nested_array(nest_type))
        self.assertFalse(ValueTypeEnum.is_nested_array("array"))
        self.assertFalse(ValueTypeEnum.is_nested_array("object"))

    def test_whether_type_is_nested_array(self):
        for standard_type, aliases in Type.type_alias.items():
            for alias in aliases:
                self.assertEqual(Type(alias).json_schema_type.value, standard_type)

    def test_nest_array_type_should_split_connect(self):
        for nest_type, split_result in self.NEST_SPLIT_RESULT.items():
            main_type, sub_type = ValueTypeEnum.split_nested_type(nest_type)
            self.assertEqual((main_type.value, sub_type.value), split_result)

    def test_nest_array_type_should_unify_correct(self):
        for nest_type, unify_nest_type in self.NEST_TYPE_EXAMPLE.items():
            self.assertEqual(Type(nest_type).json_schema_type.value, unify_nest_type)

    def test_unsupported_type_raise_value_error(self):
        for supported_type in self.UNSUPPORTED_TYPE_EXAMPLE:
            with pytest.raises(ValueError):
                Type(supported_type).json_schema_type()

    def test_validate_default_value_should_raise_Exception(self):
        for type_string, default_value in self.DEFAULT_VALUE_EXAMPLE.items():
            with pytest.raises(JiuWenBaseException):
                Param("test", "test", param_type=type_string, default_value=default_value)

    def test_schema_should_format_correct(self):
        self.schema_example['schema'][0]['type'] = "object"
        self.schema_example['schema'][0]['default_value'] = {"starttime": "2025-07-15"}
        self.schema_example['schema'][0]['schema'] = [
            {
                "name": "beijing-starttime",
                "description": "北京开始时间",
                "required": True,
                "type": "string",
                "visible": True,
                "method": "Body",
                "default_value": "2025-07-14",
            }
        ]
        self.assertIsInstance(Param(param_type=self.schema_example.get("type"), **self.schema_example), Param)

    def test_validate_schema_should_raise_Exception(self):
        self.schema_example['schema'][0]['type'] = "object"
        with pytest.raises(JiuWenBaseException):
            Param(param_type=self.schema_example.get("type"), **self.schema_example)
        with pytest.raises(JiuWenBaseException):
            Param("test", "test", param_type="object")

    def test_correct_param_should_pass_validate(self):
        self.assertIsInstance(Param("test", "test", param_type="array<integer>"), Param)
        self.assertIsInstance(Param("test", "test", param_type="array<string>", default_value=["1", "2"]), Param)
        self.assertIsInstance(Param(param_type=self.schema_example.get("type"), **self.schema_example), Param)

    def test_init_default_value_in_complex_type_should_correct(self):
        self.schema_example = [self.schema_example]
        self.schema_example.append({
            "name": "location",
            "description": "地理位置",
            "default_value": "北京",
            "required": True,
            "type": "string",
            "visible": True,
            "method": "Body"
        })
        self.schema_example.append({
            "name": "num",
            "description": "人数",
            "required": True,
            "type": "integer",
            "visible": True,
            "method": "Body"
        })
        self.param = [Param(param_type=item.get('type'), **item) for item in self.schema_example]
        self.assertEqual(
            ParamUtil.format_input_with_default_when_required(self.param, self.INPUTS),
            {"times": {"starttime": "12345", "endtime": "456123"}, "location": "上海"},
        )

        self.INPUTS = {"times": {"starttime": "2025-07-15"}, "location": "上海"}
        self.assertEqual(
            ParamUtil.format_input_with_default_when_required(self.param, self.INPUTS),
            {"times": {"starttime": "2025-07-15"}, "location": "上海"},
        )

        self.schema_example[0]["default_value"] = None
        self.INPUTS = {"times": {"starttime": "2025-07-15"}, "location": "上海"}
        self.param = [Param(param_type=item.get("type"), **item) for item in self.schema_example]
        self.assertEqual(
            ParamUtil.format_input_with_default_when_required(self.param, self.INPUTS),
            {"times": {"starttime": "2025-07-15", "endtime": "2025-07-14"}, "location": "上海"},
        )

        self.INPUTS = {"location": "上海"}
        self.assertEqual(
            ParamUtil.format_input_with_default_when_required(self.param, self.INPUTS),
            {"times": {"starttime": "2025-07-14", "endtime": "2025-07-14"}, "location": "上海"},
        )

        self.INPUTS = {"location": "上海", "times": None}
        self.assertEqual(
            ParamUtil.format_input_with_default_when_required(self.param, self.INPUTS),
            {"times": {"starttime": "2025-07-14", "endtime": "2025-07-14"}, "location": "上海"},
        )

        self.INPUTS = {"location": "上海", "times": None}
        self.schema_example[0]['type'] = "array<object>"
        self.param = [Param(param_type=item.get("type"), **item) for item in self.schema_example]
        self.assertEqual(
            ParamUtil.format_input_with_default_when_required(self.param, self.INPUTS),
            {"times": [{"starttime": "2025-07-14", "endtime": "2025-07-14"}], "location": "上海"},
        )

        self.INPUTS = {"times": [{"starttime": "2020-01-01"}, {"endtime": "2020-01-02"}], "location": "上海"}
        self.assertEqual(
            ParamUtil.format_input_with_default_when_required(self.param, self.INPUTS),
            {
                "times": [
                    {"starttime": "2020-01-01", "endtime": "2025-07-14"},
                    {"starttime": "2025-07-14", "endtime": "2020-01-02"},
                ],
                "location": "上海",
            },
        )

        self.schema_example[2]["default_value"] = 20
        self.INPUTS = {"times": [{"starttime": "2020-01-01"}], "location": "上海", "num": None}
        self.param = [Param(param_type=item.get("type"), **item) for item in self.schema_example]
        self.assertEqual(
            ParamUtil.format_input_with_default_when_required(self.param, self.INPUTS),
            {"times": [{"starttime": "2020-01-01", "endtime": "2025-07-14"}], "location": "上海", "num": 20},
        )

    def test_init_default_value_in_base_type_should_correct(self):
        self.schema_example = {
            "name": "judgement",
            "description": "判断",
            "required": True,
            "type": "bool",
            "visible": False,
            "method": "Body",
            "default_value": True,
        }
        self.INPUTS = {"judgement": False}
        self.param = [Param(param_type=self.schema_example.get("type"), **self.schema_example)]
        self.assertEqual(
            ParamUtil.format_input_with_default_when_required(self.param, self.INPUTS), {"judgement": False}
        )

        self.INPUTS = {"judgement": None}
        self.assertEqual(
            ParamUtil.format_input_with_default_when_required(self.param, self.INPUTS), {"judgement": True}
        )

        self.schema_example["type"] = "array<int>"
        self.schema_example["default_value"] = [2, 3, 4]
        self.INPUTS = {"judgement": None}
        self.param = [Param(param_type=self.schema_example.get("type"), **self.schema_example)]
        self.assertEqual(
            ParamUtil.format_input_with_default_when_required(self.param, self.INPUTS), {"judgement": [2, 3, 4]}
        )

        self.schema_example["type"] = "array<bool>"
        self.schema_example["default_value"] = [True, False, True]
        self.INPUTS = {"judgement": None}
        self.param = [Param(param_type=self.schema_example.get("type"), **self.schema_example)]
        self.assertEqual(
            ParamUtil.format_input_with_default_when_required(self.param, self.INPUTS),
            {"judgement": [True, False, True]},
        )
