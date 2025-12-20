#!/usr/bin/env python
# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from typing import Any, List

from openjiuwen.core.common.exception.exception import JiuWenBaseException
from openjiuwen.core.common.exception.status_code import StatusCode
from openjiuwen.core.utils.tool.types import Type, ValueTypeEnum


class Param:
    """Plugin parameter"""

    def __init__(self, name: str, description: str, param_type=None, default_value=None, required=True, visible=True,
                 level=0, schema=None, **kwargs):
        if name:
            self.name = name
        else:
            raise JiuWenBaseException(error_code=StatusCode.PLUGIN_UNEXPECTED_ERROR.code,
                                      message="Plugin param's name cannot be empty.")
        self.description = '' if description is None else description
        param_type = param_type or 'string'
        self.type = Type(param_type).json_schema_type.value
        self.default_value: Any = default_value
        if default_value:
            Param.validate_default_value(self.type, default_value)
        self.required = required
        self.visible = visible
        self.method = kwargs.get('method', "Body")
        self.actual_type = kwargs.get('actual_type', "")
        if level > 0:
            self.level = level
        if ValueTypeEnum.is_object(self.type) and not schema:
            raise JiuWenBaseException(
                error_code=StatusCode.PLUGIN_PARAMS_CHECK_FAILED.code,
                message="The schema field is missing."
            )
        if schema:
            self.schema = self._format_schema(schema)

    @staticmethod
    def validate_default_value(type_string: str, default_value: Any):
        """Validate whether the default value matches the given type."""
        if ValueTypeEnum.is_nested_array(type_string):
            main_type, sub_type = ValueTypeEnum.split_nested_type(type_string)
        else:
            main_type, sub_type = Type(type_string).json_schema_type, None
        type_check = {
            "string": lambda x: isinstance(x, str),
            "integer": lambda x: isinstance(x, int),
            "number": lambda x: isinstance(x, (int, float)),
            "boolean": lambda x: isinstance(x, bool),
            "object": lambda x: isinstance(x, dict),
            "array": lambda x: isinstance(x, list)
        }
        if sub_type is None:
            if not type_check.get(main_type.value)(default_value):
                raise JiuWenBaseException(
                    error_code=StatusCode.PLUGIN_PARAMS_CHECK_FAILED.code,
                    message=f"Default value must be a {main_type.value}.",
                )
        else:
            if not isinstance(default_value, list):
                raise JiuWenBaseException(
                    error_code=StatusCode.PLUGIN_PARAMS_CHECK_FAILED.code,
                    message=f"Default value must be a array."
                )
            if not all(type_check.get(sub_type.value)(item) for item in default_value):
                raise JiuWenBaseException(
                    error_code=StatusCode.PLUGIN_PARAMS_CHECK_FAILED.code,
                    message=f"Not all items in the default value list match the type for {sub_type.value}."
                )

    @staticmethod
    def _format_schema(schema: list):
        params = []
        for item in schema:
            if isinstance(item, dict):
                if ValueTypeEnum.is_object(item.get("type")) and 'schema' not in item:
                    raise JiuWenBaseException(
                        error_code=StatusCode.PLUGIN_PARAMS_CHECK_FAILED.code,
                        message="The schema field is missing."
                    )
                sub_schema = item.get("schema", [])
                sub_schema = Param._format_schema(sub_schema) if sub_schema else []
                param_kwargs = {
                    'name': item.get("name"),
                    'description': item.get("description"),
                    'param_type': item.get("type"),
                    'required': item.get("required", True),
                    'visible': item.get("visible", True),
                    'method': item.get("method", "Body"),
                    'default_value': item.get("default_value")
                }
                if sub_schema:
                    param_kwargs['schema'] = sub_schema
                params.append(Param(**param_kwargs))
            if isinstance(item, Param):
                params.append(item)
        return params

    @staticmethod
    def format_functions_for_complex(params: List['Param'], properties: dict):
        """format complex type to json schema"""
        required = []
        for param in params:
            if not param.visible:
                continue
            if param.required:
                required.append(param.name)
            param_type = param.type
            if ValueTypeEnum.is_object(param_type):
                properties[param.name] = {}
                properties[param.name]['description'] = param.description
                if ValueTypeEnum.is_nested_array(param_type):
                    properties[param.name]['type'] = 'array'
                    properties[param.name]['items'] = Param.format_functions_for_complex(param.schema, {})
                else:
                    properties[param.name]['type'] = 'object'
                    properties[param.name]['properties'] = Param.format_functions_for_complex(param.schema, {})
            elif ValueTypeEnum.is_nested_array(param_type):
                properties[param.name] = {}
                properties[param.name]['description'] = param.description
                properties[param.name]['type'] = 'array'
                _, sub_type = ValueTypeEnum.split_nested_type(param_type)
                properties[param.name]['items'] = {"description": param.description, "type": sub_type.value}
            else:
                properties[param.name] = {"description": param.description, "type": param_type}
            properties["required"] = required
        return properties

    @staticmethod
    def format_functions(tool):
        """format function info for LLM"""
        properties = dict()
        params = tool.params
        properties = Param.format_functions_for_complex(params, properties)
        required = properties.pop("required", [])
        tool_name = tool.name
        format_tool_name = tool.name
        if '#*' in tool_name:
            split_list = tool_name.split('#*', 1)
            if split_list:
                format_tool_name = split_list[0].strip()
        function = dict(
            name=format_tool_name,
            description=tool.description,
            parameters=dict(
                type="object",
                properties=properties,
                required=required
            )
        )

        output_properties = dict()
        output_params = tool.response if hasattr(tool, "response") else []
        for param in output_params:
            output_properties[param.name] = {
                "description": param.description,
                "type": param.type
            }
        if output_properties and "results" in output_properties:
            function["results"] = output_properties["results"]
        return function
