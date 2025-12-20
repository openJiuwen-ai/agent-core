#!/usr/bin/env python
# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from enum import Enum


class ValueTypeEnum(Enum):
    """Enumeration type for the plugin"""
    # normal type
    STRING = 'string'
    NUMBER = 'number'
    INTEGER = 'integer'
    BOOLEAN = 'boolean'
    OBJECT = 'object'
    ARRAY = 'array'

    # Nested type
    ARRAY_STRING = 'array<string>'
    ARRAY_NUMBER = 'array<number>'
    ARRAY_INTEGER = 'array<integer>'
    ARRAY_BOOLEAN = 'array<boolean>'
    ARRAY_OBJECT = 'array<object>'

    @staticmethod
    def is_object(value: str):
        return Type(value).json_schema_type.value in ('object', 'array<object>')

    @staticmethod
    def split_nested_type(type_string: str):
        main_type, sub_type = None, None
        if ValueTypeEnum.is_nested_array(type_string):
            main_type_string, sub_type_string = type_string.split('<')[0], type_string.split('<')[1].rstrip('>')
            main_type, sub_type = Type(main_type_string).json_schema_type, Type(sub_type_string).json_schema_type
        return main_type, sub_type

    @classmethod
    def is_nested_array(cls, value: str) -> bool:
        is_array_prefix = value.startswith(cls.ARRAY.value)
        has_array_alias = any(value.startswith(alias) for alias in Type.type_alias[cls.ARRAY.value])
        return value != cls.ARRAY.value if is_array_prefix else has_array_alias

    @classmethod
    def from_string(cls, type_string: str):
        """create ValueTypeEnum from a string"""
        if cls.is_nested_array(type_string):
            _, sub_type = ValueTypeEnum.split_nested_type(type_string)
            array_enum_map = {
                ValueTypeEnum.STRING: cls.ARRAY_STRING,
                ValueTypeEnum.NUMBER: cls.ARRAY_NUMBER,
                ValueTypeEnum.INTEGER: cls.ARRAY_INTEGER,
                ValueTypeEnum.BOOLEAN: cls.ARRAY_BOOLEAN,
                ValueTypeEnum.OBJECT: cls.ARRAY_OBJECT,
            }
            if sub_type in array_enum_map:
                return array_enum_map[sub_type]
            raise ValueError("Invalid type")
        for item in cls:
            if item.value.lower() == type_string.lower():
                return item
        raise ValueError("Invalid type")


class Type:
    type_alias = {
        ValueTypeEnum.STRING.value: ("String", "str", "Str"),
        ValueTypeEnum.NUMBER.value: ("Number", "Float", "float"),
        ValueTypeEnum.INTEGER.value: ("Integer", "int", "Int"),
        ValueTypeEnum.BOOLEAN.value: ("Boolean", "bool", "Bool"),
        ValueTypeEnum.ARRAY.value: ("Array", "List", "list"),
        ValueTypeEnum.OBJECT.value: ("Object", "Map", "map"),
    }

    def __init__(self, var_type: str):
        self.var_type = var_type
        self.json_schema_type = self._json_schema_type()

    def _json_schema_type(self):
        """convert type by json schema"""
        for standard_type, aliases in self.type_alias.items():
            if self.var_type in aliases:
                return ValueTypeEnum.from_string(standard_type)
        return ValueTypeEnum.from_string(self.var_type)